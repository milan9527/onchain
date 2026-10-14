"""Provision one isolated keyless-demo acceptance user; never email or print credentials."""
import json
import argparse
import os
import secrets
import time
from pathlib import Path

import boto3

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--identity", action="store_true", help="Create a fresh account for first-time Token Vault consent")
args = parser.parse_args()
purpose = "identity" if args.identity else "demo"
path = root / f".deployment/{purpose}-credentials.json"
if path.exists():
    raise SystemExit("Demo acceptance user already exists; reuse the saved credentials.")
deployment = json.loads((root / ".deployment/resources.json").read_text())
client = boto3.client("cognito-idp", region_name=deployment["region"])
pool = deployment["outputs"]["UserPoolId"]
email = f"onchain-{purpose}-{int(time.time())}@example.invalid"
password = "Tt9!" + secrets.token_urlsafe(28)
client.admin_create_user(
    UserPoolId=pool, Username=email, MessageAction="SUPPRESS",
    UserAttributes=[{"Name": "email", "Value": email}, {"Name": "email_verified", "Value": "true"}],
)
client.admin_set_user_password(UserPoolId=pool, Username=email, Password=password, Permanent=True)
with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as output:
    json.dump({"email": email, "password": password}, output)
print("Created isolated demo acceptance user. Credentials saved with mode 0600.")
