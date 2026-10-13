"""Create isolated marketplace acceptance accounts without sending email or printing secrets."""
import json
import os
import secrets
import time
from pathlib import Path

import boto3

root = Path(__file__).resolve().parents[1]
deployment = json.loads((root / ".deployment/resources.json").read_text())
path = root / ".deployment/market-credentials.json"
if path.exists():
    raise SystemExit("Marketplace acceptance accounts already exist; reuse the saved credentials.")
client = boto3.client("cognito-idp", region_name=deployment["region"])
users = {}
for role in ("seller", "buyer"):
    email = f"onchain-market-{role}-{int(time.time())}@example.invalid"
    password = "Tt9!" + secrets.token_urlsafe(28)
    client.admin_create_user(
        UserPoolId=deployment["outputs"]["UserPoolId"], Username=email, MessageAction="SUPPRESS",
        UserAttributes=[{"Name": "email", "Value": email}, {"Name": "email_verified", "Value": "true"}],
    )
    client.admin_set_user_password(UserPoolId=deployment["outputs"]["UserPoolId"],
                                   Username=email, Password=password, Permanent=True)
    users[role] = {"email": email, "password": password}
with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as output:
    json.dump(users, output)
print("Created isolated marketplace seller and buyer accounts. Credentials saved with mode 0600.")
