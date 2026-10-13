"""Real Cognito MFA and public-boundary checks using only synthetic smoke accounts."""
import base64
import hashlib
import hmac
import json
import struct
import time
from pathlib import Path

import boto3
import httpx
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]


def totp(secret):
    key = base64.b32decode(secret + "=" * ((8-len(secret) % 8) % 8))
    value = hmac.new(key, struct.pack(">Q", int(time.time())//30), hashlib.sha1).digest()
    offset = value[-1] & 15
    return f"{(struct.unpack('>I', value[offset:offset+4])[0] & 0x7fffffff) % 1000000:06}"


def main():
    state = json.loads((ROOT / ".deployment/resources.json").read_text())
    credentials = json.loads((ROOT / ".deployment/smoke-credentials.json").read_text())["bob"]
    assert credentials["email"].startswith("onchain-smoke-")
    assert credentials["email"].endswith("@example.invalid")
    cognito = boto3.client("cognito-idp", region_name=state["region"])
    with httpx.Client(base_url=state["outputs"]["WebsiteUrl"], timeout=30) as client:
        assert client.get("/api/dashboard").status_code == 401
        login = client.post("/api/auth/login", json=credentials)
        assert login.status_code == 200 and "HttpOnly" in login.headers["set-cookie"]
        csrf = login.json()["csrf"]
        assert client.post("/api/agents", json={"name": "blocked"}).status_code == 403
        headers = {"X-CSRF-Token": csrf}
        assert client.post("/api/agents", json={"name": "blocked"},
                           headers={**headers, "Origin": "https://attacker.invalid"}).status_code == 403
        enrolled = client.post("/api/auth/mfa/enroll", json=credentials, headers=headers)
        assert enrolled.status_code == 200, enrolled.text
        enrollment = enrolled.json()
        try:
            verified = client.post("/api/auth/mfa/verify", headers=headers, json={
                "enrollment_id": enrollment["enrollment_id"], "code": totp(enrollment["secret"]),
            })
            assert verified.status_code == 200, verified.text
            assert client.post("/api/auth/logout", headers=headers).status_code == 200
            # Cognito consumes the enrollment code; a new login needs the next TOTP window.
            time.sleep(31 - time.time() % 30)
            challenge = client.post("/api/auth/login", json=credentials).json()
            assert challenge["type"] == "SOFTWARE_TOKEN_MFA"
            completed = client.post("/api/auth/challenge", json={
                "challenge_id": challenge["challenge_id"], "code": totp(enrollment["secret"]),
            })
            assert completed.status_code == 200, completed.text
            assert client.get("/api/dashboard").status_code == 200
            assert client.post("/api/auth/challenge", json={
                "challenge_id": challenge["challenge_id"], "code": totp(enrollment["secret"]),
            }).status_code == 401
            print("PASS: Cognito TOTP enrollment, MFA login, challenge replay, CSRF, origin and cookies")
        finally:
            # Return only this synthetic test account to its initial state for repeatable browser tests.
            cognito.admin_set_user_mfa_preference(
                UserPoolId=state["outputs"]["UserPoolId"], Username=credentials["email"],
                SoftwareTokenMfaSettings={"Enabled": False, "PreferredMfa": False},
            )
    with httpx.Client(timeout=30) as client:
        response = client.post(state["gateway"]["url"], json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/list",
        })
        assert response.status_code == 401, response.status_code
        print("PASS: unauthenticated Gateway request rejected")
    try:
        boto3.client("bedrock-agentcore", region_name=state["region"]).invoke_agent_runtime(
            agentRuntimeArn=state["onchain_control_mcp"]["arn"],
            payload=b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}',
        )
        raise AssertionError("A non-Gateway IAM principal reached the MCP runtime")
    except ClientError as error:
        assert error.response["Error"]["Code"] == "AccessDeniedException"
        print("PASS: direct MCP runtime bypass rejected by its resource policy")


if __name__ == "__main__":
    main()
