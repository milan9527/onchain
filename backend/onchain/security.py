import base64
import hashlib
import json
import os
import secrets
import time
from pathlib import Path

import boto3
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import BaseModel


class Denied(Exception):
    def __init__(self, code: str, message: str, status: int = 403):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)


class Principal(BaseModel):
    sub: str
    tenant: str
    email: str = ""
    admin: bool = False

    @property
    def owner(self) -> str:
        return hashlib.sha256(f"{self.tenant}:{self.sub}".encode()).hexdigest()


def uid(prefix="") -> str:
    return prefix + secrets.token_hex(18)


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


class TokenService:
    """API/worker/scheduler issue routing JWTs. Gateway verifies; MCP uses Cognito OAuth."""

    def __init__(self, config):
        self.config = config
        self.kid = hashlib.sha256((config.signing_key_id or "local").encode()).hexdigest()[:16]
        self.private = None
        if config.signing_key_id:
            self.kms = boto3.client("kms", region_name=config.region)
            der = self.kms.get_public_key(KeyId=config.signing_key_id)["PublicKey"]
            self.public = serialization.load_der_public_key(der)
        else:
            if config.production:
                raise RuntimeError("Local signing key is forbidden in production.")
            path = Path(config.local_key_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
                data = private.private_bytes(
                    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
                try:
                    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, "wb") as f:
                        f.write(data)
                except FileExistsError:
                    pass
            self.private = serialization.load_pem_private_key(path.read_bytes(), password=None)
            self.public = self.private.public_key()

    def issue(self, audience: str, claims: dict, ttl=300) -> str:
        now = int(time.time())
        payload = {**claims, "iss": self.config.issuer, "aud": audience, "iat": now,
                   "nbf": now - 1, "exp": now + ttl, "jti": uid()}
        if self.private:
            return jwt.encode(payload, self.private, algorithm="RS256", headers={"kid": self.kid})
        header = b64(json.dumps({"alg": "RS256", "typ": "JWT", "kid": self.kid}).encode())
        body = b64(json.dumps(payload, separators=(",", ":")).encode())
        signing_input = f"{header}.{body}".encode()
        signature = self.kms.sign(
            KeyId=self.config.signing_key_id, Message=hashlib.sha256(signing_input).digest(),
            MessageType="DIGEST", SigningAlgorithm="RSASSA_PKCS1_V1_5_SHA_256",
        )["Signature"]
        return f"{header}.{body}.{b64(signature)}"

    def verify(self, token: str, audience: str) -> dict:
        try:
            return jwt.decode(
                token, self.public, algorithms=["RS256"], issuer=self.config.issuer,
                audience=audience, options={"require": ["exp", "iat", "iss", "aud", "sub", "jti"]},
            )
        except jwt.PyJWTError as e:
            raise Denied("INVALID_CAPABILITY", "Invalid or expired authentication.", 401) from e

    def jwks(self):
        obj = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.public))
        return {"keys": [{**obj, "kid": self.kid, "use": "sig", "alg": "RS256"}]}


def bearer(headers: dict) -> str:
    value = next((v for k, v in headers.items() if k.lower() == "authorization"), "")
    if not isinstance(value, str) or not value.startswith("Bearer "):
        raise Denied("UNAUTHENTICATED", "Authentication is required.", 401)
    return value[7:]
