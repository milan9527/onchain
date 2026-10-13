import hashlib
import hmac
import secrets
import time

import boto3
from botocore.exceptions import ClientError

from .security import Denied, Principal, uid

COOKIE = "onchain_session"


class Auth:
    def __init__(self, config, store):
        self.config, self.store = config, store

    def client(self):
        return boto3.client("cognito-idp", region_name=self.config.region)

    def authenticate(self, email, password, challenge=None):
        if self.config.production and self.config.cognito_domain:
            raise Denied("OAUTH_LOGIN_REQUIRED", "Use Cognito OAuth to authenticate.", 401)
        if not self.config.production:
            if email not in {"alice@example.test", "bob@example.test"} or not hmac.compare_digest(
                password, "local-only-change-me"
            ):
                raise Denied("LOGIN_FAILED", "Email or password is incorrect.", 401)
            return Principal(sub=email, tenant="local-"+email, email=email), None
        try:
            if challenge:
                result = self.client().respond_to_auth_challenge(
                    ClientId=self.config.cognito_client_id,
                    ChallengeName=challenge["name"], Session=challenge["session"],
                    ChallengeResponses={"USERNAME": email, "SOFTWARE_TOKEN_MFA_CODE": password}
                    if challenge["name"] == "SOFTWARE_TOKEN_MFA"
                    else {"USERNAME": email, "SMS_MFA_CODE": password},
                )
            else:
                result = self.client().initiate_auth(
                    ClientId=self.config.cognito_client_id, AuthFlow="USER_PASSWORD_AUTH",
                    AuthParameters={"USERNAME": email, "PASSWORD": password},
                )
            if "ChallengeName" in result:
                if result["ChallengeName"] not in {"SOFTWARE_TOKEN_MFA", "SMS_MFA"}:
                    raise Denied("AUTH_CHALLENGE", "Complete account setup before signing in.", 401)
                return None, {"name": result["ChallengeName"], "session": result["Session"], "email": email}
            # get_user validates the freshly returned access token server-side. Tokens never enter the LLM.
            user = self.client().get_user(AccessToken=result["AuthenticationResult"]["AccessToken"])
            attrs = {a["Name"]: a["Value"] for a in user["UserAttributes"]}
            sub = attrs["sub"]
            groups = self.client().admin_list_groups_for_user(
                UserPoolId=self.config.cognito_pool_id, Username=user["Username"],
            ).get("Groups", [])
            principal = Principal(
                sub=sub, tenant="personal-"+sub, email=attrs.get("email", email),
                admin=any(g["GroupName"] == "platform-admin" for g in groups),
            )
            return principal, None
        except ClientError as e:
            raise Denied("LOGIN_FAILED", "Authentication failed. Check credentials and account status.", 401) from e

    def new_session(self, principal, oauth=None, refresh_token=None):
        cookie = secrets.token_urlsafe(40)
        csrf = secrets.token_urlsafe(32)
        record = {
            "principal": principal.model_dump(), "csrf": csrf, "created_at": int(time.time()),
            "expires_at": int(time.time())+3600, "active": True, "stepups": {},
        }
        key = "AUTH#"+hashlib.sha256(cookie.encode()).hexdigest()
        if oauth:
            from .oauth import CognitoOAuth
            record["oauth"] = oauth
            record["oauth_refresh"] = CognitoOAuth(self.config, self.store).seal_refresh(refresh_token, key)
        self.store.put(key, record)
        return cookie, record

    def record(self, cookie):
        if not cookie:
            raise Denied("UNAUTHENTICATED", "Sign in to continue.", 401)
        key = "AUTH#"+hashlib.sha256(cookie.encode()).hexdigest()
        record = self.store.get(key)
        if not record or not record.get("active") or record["expires_at"] <= time.time():
            raise Denied("SESSION_EXPIRED", "Sign in again.", 401)
        if self.config.production and self.config.cognito_domain:
            from .oauth import CognitoOAuth
            record = CognitoOAuth(self.config, self.store).refresh_session(key, record)
        return key, record

    def principal(self, cookie, csrf=None, mutating=False):
        _key, record = self.record(cookie)
        if self.config.production and self.config.cognito_domain:
            from .oauth import CognitoOAuth, CONTROL
            CognitoOAuth(self.config, self.store).scope(record["oauth"], CONTROL)
        if mutating and (not csrf or not hmac.compare_digest(csrf, record["csrf"])):
            raise Denied("CSRF_FAILED", "Invalid request verification token.")
        return Principal.model_validate(record["principal"])

    def mark_stepup(self, cookie, principal, operation_id):
        key, _ = self.record(cookie)
        def apply(record):
            if record["principal"]["sub"] != principal.sub or not record["active"]:
                raise Denied("STEP_UP_OWNER", "Re-authentication must use the current account.")
            record["stepups"][operation_id] = int(time.time())+120
            return record, None
        self.store.mutate(key, apply)

    def consume_stepup(self, cookie, operation_id):
        key, _ = self.record(cookie)
        def apply(record):
            proof = record["stepups"].pop(operation_id, 0)
            expires = proof["expires_at"] if isinstance(proof, dict) else proof
            if expires <= time.time() or not record["active"]:
                raise Denied("STEP_UP_REQUIRED", "Re-authenticate for this operation.", 401)
            return record, operation_id
        return self.store.mutate(key, apply)

    def logout(self, cookie):
        key, before = self.record(cookie)
        def apply(record):
            record["active"] = False
            record.pop("oauth_refresh", None)
            return record, None
        self.store.mutate(key, apply)
        if before.get("oauth_refresh"):
            # Local invalidation is immediate even if the provider's revocation endpoint is unavailable.
            try:
                from .oauth import CognitoOAuth
                token = CognitoOAuth(self.config, self.store).open_refresh(before["oauth_refresh"], key)
                self.client().revoke_token(Token=token, ClientId=self.config.cognito_client_id)
            except Exception as e:
                print({"event": "oauth_revoke_failed", "error_type": type(e).__name__})

    def challenge(self, result, kind, operation_id=None, auth_key=None):
        challenge_id = uid("challenge_")
        self.store.put("CHALLENGE#"+challenge_id, {
            **result, "kind": kind, "operation_id": operation_id, "auth_key": auth_key,
            "expires_at": int(time.time())+180, "used": False,
        })
        return {"challenge_id": challenge_id, "type": result["name"], "kind": kind}

    def resolve_challenge(self, challenge_id, code, cookie=None):
        def claim(record):
            if not record or record["used"] or record["expires_at"] <= time.time():
                raise Denied("CHALLENGE_EXPIRED", "Restart authentication.", 401)
            if record["kind"] == "stepup":
                key, _ = self.record(cookie)
                if key != record["auth_key"]:
                    raise Denied("CHALLENGE_OWNER", "Challenge belongs to another login session.")
            record["used"] = True
            return record, record
        record = self.store.mutate("CHALLENGE#"+challenge_id, claim)
        principal, further = self.authenticate(record["email"], code, record)
        if further:
            raise Denied("AUTH_CHALLENGE", "Authentication did not complete.", 401)
        return principal, record
