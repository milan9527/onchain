from functools import lru_cache
from os import getenv
from typing import Literal

from pydantic import BaseModel


class Settings(BaseModel):
    environment: str = "local"
    region: str = "us-east-1"
    model_id: str = "us.openai.gpt-6-sol"
    table_name: str = ""
    issuer: str = "http://localhost:8000/api/identity"
    public_url: str = "http://localhost:5173"
    cognito_domain: str = ""
    cognito_client_id: str = ""
    cognito_pool_id: str = ""
    cognito_external_client_id: str = ""
    oauth_gateway_url: str = ""
    oauth_vault_key_id: str = ""
    signing_key_id: str = ""
    agent_runtime_arn: str = ""
    gateway_url: str = ""
    tool_executor_arn: str = ""
    runtime_mode: str = "local"
    local_path: str = ".local/state.db"
    local_key_path: str = ".local/signing.pem"
    # Live trading is deliberately unavailable until a separate wallet integration is configured.
    trading_mode: str = "paper"
    # Explicit mode: live failures never fall back to synthetic data.
    onchain_data_mode: Literal["demo", "live"] = "demo"
    okx_secret_arn: str = ""
    queue_url: str = ""

    @property
    def production(self) -> bool:
        return self.environment == "production"


@lru_cache
def settings() -> Settings:
    s = Settings(
        **{
            name: getenv(name.upper(), str(field.default))
            for name, field in Settings.model_fields.items()
        }
    )
    if s.production and not all(
        [s.table_name, s.signing_key_id, s.cognito_pool_id, s.cognito_client_id]
    ):
        raise RuntimeError("Production requires DynamoDB, KMS and Cognito; local auth is disabled.")
    if s.trading_mode != "paper":
        raise RuntimeError("Live trading requires an independently reviewed wallet signer; not enabled.")
    return s
