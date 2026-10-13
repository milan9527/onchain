"""Create two isolated, email-verified test users without sending email. Never prints passwords."""
import json
import os
import secrets
import time
from pathlib import Path

import boto3

root = Path(__file__).resolve().parents[1]
state = json.loads((root / ".deployment/resources.json").read_text())
path = root / ".deployment/smoke-credentials.json"
client = boto3.client("cognito-idp", region_name=state["region"])
pool = state["outputs"]["UserPoolId"]
if path.exists():
    raise SystemExit("Smoke credentials already exist; reuse them or explicitly remove the test users first.")
users = {}
for name in ["alice", "bob"]:
    email = f"onchain-smoke-{name}-{int(time.time())}@example.invalid"
    password = "Tt9!"+secrets.token_urlsafe(28)
    client.admin_create_user(
        UserPoolId=pool, Username=email, MessageAction="SUPPRESS",
        UserAttributes=[{"Name": "email", "Value": email}, {"Name": "email_verified", "Value": "true"}],
    )
    client.admin_set_user_password(UserPoolId=pool, Username=email, Password=password, Permanent=True)
    users[name] = {"email": email, "password": password}
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as f:
    json.dump(users, f)
print("Created two test users without sending email. Credentials saved with mode 0600.")
