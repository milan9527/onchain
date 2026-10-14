"""Reconcile the managed Identity Token Vault provider without persisting client secrets locally."""
import json
from urllib.parse import quote


def mcp_endpoint(region, arn):
    return f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{quote(arn, safe='')}/invocations?qualifier=DEFAULT"


def configure_identity(session, ac, cognito, state, outputs, account, region, save):
    public_url = outputs["WebsiteUrl"]
    runtime_arn = state.get("onchain_control_mcp", {}).get("arn")
    audience = mcp_endpoint(region, runtime_arn) if runtime_arn else public_url + "/mcp/tools"
    # Gateway limits each scope to 128 chars; a Runtime ARN URL + Cognito custom scope exceeds it.
    # OpenID authenticates the downstream user. Exact tool authority comes from the invocation record.
    tool_scope = "openid"
    pool = outputs["UserPoolId"]
    client_options = {
        "UserPoolId": pool, "ClientName": "onchain-mcp-runtime-identity",
        "AllowedOAuthFlowsUserPoolClient": True, "AllowedOAuthFlows": ["code"],
        "AllowedOAuthScopes": [tool_scope],
        "SupportedIdentityProviders": ["COGNITO"], "EnableTokenRevocation": True,
        "PreventUserExistenceErrors": "ENABLED",
        "AccessTokenValidity": 15, "IdTokenValidity": 15, "RefreshTokenValidity": 30,
        "TokenValidityUnits": {"AccessToken": "minutes", "IdToken": "minutes", "RefreshToken": "days"},
        "CallbackURLs": [state.get("identity_provider", {}).get("callback_url") or public_url + "/api/identity/provider"],
    }
    if not state.get("identity_client"):
        created = cognito.create_user_pool_client(GenerateSecret=True, **client_options)["UserPoolClient"]
        state["identity_client"] = {"id": created["ClientId"]}
        save()
    client_id = state["identity_client"]["id"]
    client = cognito.describe_user_pool_client(UserPoolId=pool, ClientId=client_id)["UserPoolClient"]
    provider_name = "onchain-mcp-user-oauth"
    provider_options = {
        "name": provider_name, "credentialProviderVendor": "CustomOauth2",
        "oauth2ProviderConfigInput": {"customOauth2ProviderConfig": {
            "oauthDiscovery": {"discoveryUrl": f"https://cognito-idp.{region}.amazonaws.com/{pool}/.well-known/openid-configuration"},
            "clientId": client_id, "clientSecret": client["ClientSecret"],
            "clientAuthenticationMethod": "CLIENT_SECRET_BASIC",
        }},
    }
    if state.get("identity_provider"):
        provider = ac.update_oauth2_credential_provider(**provider_options)
    else:
        provider = ac.create_oauth2_credential_provider(**provider_options)
    state["identity_provider"] = {
        "name": provider_name, "arn": provider["credentialProviderArn"],
        "callback_url": provider["callbackUrl"], "secret_arn": provider["clientSecretArn"]["secretArn"],
    }
    save()
    client_options["CallbackURLs"] = [provider["callbackUrl"]]
    cognito.update_user_pool_client(ClientId=client_id, **client_options)
    try:
        cognito.describe_managed_login_branding_by_client(UserPoolId=pool, ClientId=client_id)
    except cognito.exceptions.ResourceNotFoundException:
        cognito.create_managed_login_branding(UserPoolId=pool, ClientId=client_id, UseCognitoProvidedValues=True)
    iam = session.client("iam")
    base = f"arn:aws:bedrock-agentcore:{region}:{account}"
    gateway_workloads = [
        base + ":workload-identity-directory/default/workload-identity/onchain-control-gateway-*",
        base + ":workload-identity-directory/default/workload-identity/onchain-oauth-gateway-*",
    ]
    def provider_access(workloads):
        return [
            {"Effect": "Allow", "Action": ["bedrock-agentcore:GetResourceOauth2Token"],
             "Resource": [base + ":workload-identity-directory/default", *workloads,
                          base + ":token-vault/default", provider["credentialProviderArn"]]},
            {"Effect": "Allow", "Action": ["secretsmanager:GetSecretValue"],
             "Resource": provider["clientSecretArn"]["secretArn"]},
        ]
    iam.put_role_policy(RoleName=outputs["GatewayRoleArn"].split("/")[-1],
                        PolicyName="identity-token-vault",
                        PolicyDocument=json.dumps({"Version": "2012-10-17", "Statement": [
                            *provider_access(gateway_workloads),
                            {"Effect": "Allow", "Action": ["bedrock-agentcore:GetWorkloadAccessTokenForJWT"],
                             "Resource": [
                                 base + ":workload-identity-directory/default",
                                 *gateway_workloads,
                             ]},
                        ]}))
    api_role = session.client("lambda").get_function_configuration(FunctionName=outputs["ApiFunctionArn"])["Role"].split("/")[-1]
    iam.put_role_policy(RoleName=api_role, PolicyName="identity-token-vault",
                        PolicyDocument=json.dumps({"Version": "2012-10-17", "Statement": [
                            {"Effect": "Allow", "Action": ["bedrock-agentcore:CompleteResourceTokenAuth"], "Resource": "*"},
                            # Identity exchanges the code using this secret on the completion caller's behalf.
                            {"Effect": "Allow", "Action": ["secretsmanager:GetSecretValue"],
                             "Resource": provider["clientSecretArn"]["secretArn"]},
                        ]}))
    key_arn = f"arn:aws:kms:{region}:{account}:key/{outputs['OAuthVaultKeyId']}"
    for component in ("Worker", "Response"):
        role = session.client("lambda").get_function_configuration(FunctionName=outputs[component + "FunctionArn"])["Role"].split("/")[-1]
        iam.put_role_policy(RoleName=role, PolicyName="identity-consent-proof",
                            PolicyDocument=json.dumps({"Version": "2012-10-17", "Statement": [{
                                "Effect": "Allow", "Action": ["kms:Encrypt"], "Resource": key_arn,
                                "Condition": {"StringEquals": {"kms:EncryptionContext:purpose": "identity-consent"}},
                            }]}))
    # Remove permissions made obsolete by the OAuth/record migration, including on skip-stack deploys.
    for role_name, action in [
        ("onchain-control-requestrole", "kms:Sign"),
        (outputs["McpRuntimeRoleArn"].split("/")[-1], "kms:GetPublicKey"),
        (outputs["GatewayRoleArn"].split("/")[-1], "bedrock-agentcore:InvokeAgentRuntime"),
    ]:
        policy = iam.get_role_policy(RoleName=role_name, PolicyName="scoped-access")["PolicyDocument"]
        statements = []
        for statement in policy["Statement"]:
            actions = statement["Action"] if isinstance(statement["Action"], list) else [statement["Action"]]
            actions = [a for a in actions if a != action]
            if actions:
                statements.append({**statement, "Action": actions})
        iam.put_role_policy(RoleName=role_name, PolicyName="scoped-access",
                            PolicyDocument=json.dumps({**policy, "Statement": statements}))
    return {
        "MCP_OAUTH_CLIENT_ID": client_id, "MCP_OAUTH_AUDIENCE": audience,
        "MCP_OAUTH_SCOPE": tool_scope,
        "IDENTITY_PROVIDER_ARN": provider["credentialProviderArn"],
        "IDENTITY_PROVIDER_NAME": provider_name,
    }


def register_gateway_callback(ac, gateway_id, public_url, source):
    gateway = ac.get_gateway(gatewayIdentifier=gateway_id)
    arn = gateway["workloadIdentityDetails"]["workloadIdentityArn"]
    ac.update_workload_identity(name=arn.rsplit("/", 1)[-1],
                               allowedResourceOauth2ReturnUrls=[public_url + "/api/identity/callback/" + source])


def target_oauth(env, source):
    return [{
        "credentialProviderType": "OAUTH",
        "credentialProvider": {"oauthCredentialProvider": {
            "providerArn": env["IDENTITY_PROVIDER_ARN"], "grantType": "AUTHORIZATION_CODE",
            "scopes": [env["MCP_OAUTH_SCOPE"]],
            # MCP Gateway sets RFC 8707 resource to the target endpoint. Do not duplicate it here.
            "customParameters": {"prompt": "login"},
            "defaultReturnUrl": env["PUBLIC_URL"] + "/api/identity/callback/" + source,
        }},
    }]


def mcp_schema():
    import asyncio
    from runtimes.mcp_server import mcp
    tools = asyncio.run(mcp.list_tools())
    return {"inlinePayload": json.dumps({"tools": [tool.model_dump(by_alias=True, exclude_none=True) for tool in tools]})}
