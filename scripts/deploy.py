"""Reconcile the application stack, AgentCore resources, and independently built static frontend."""
import argparse
import hashlib
import json
import mimetypes
import sys
import time
from pathlib import Path
from urllib.parse import quote

import boto3
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from infra.template import template  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--stack", default="OnchainControl")
    parser.add_argument("--frontend-only", action="store_true")
    parser.add_argument("--skip-stack", action="store_true")
    parser.add_argument("--okx-secret-arn", help="Existing Secrets Manager secret for the read-only OKX adapter")
    parser.add_argument("--onchain-data-mode", choices=["demo", "live"],
                        help="Explicit research data source; demo requires no OKX key, live never falls back")
    args = parser.parse_args()
    session = boto3.Session(region_name=args.region)
    account = session.client("sts").get_caller_identity()["Account"]
    s3, cf, lambdas = session.client("s3"), session.client("cloudformation"), session.client("lambda")
    ac = session.client("bedrock-agentcore-control")
    deployment_dir = ROOT / ".deployment"
    deployment_dir.mkdir(exist_ok=True)
    state_path = deployment_dir / "resources.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    def save():
        state_path.write_text(json.dumps(state, indent=2))
    artifact_bucket = f"onchain-control-artifacts-{account}-{args.region}"
    if not args.frontend_only:
        try:
            s3.head_bucket(Bucket=artifact_bucket)
        except ClientError:
            kwargs = {"Bucket": artifact_bucket}
            if args.region != "us-east-1":
                kwargs["CreateBucketConfiguration"] = {"LocationConstraint": args.region}
            s3.create_bucket(**kwargs)
        s3.put_public_access_block(Bucket=artifact_bucket, PublicAccessBlockConfiguration={
            "BlockPublicAcls": True, "IgnorePublicAcls": True, "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        })
        s3.put_bucket_encryption(Bucket=artifact_bucket, ServerSideEncryptionConfiguration={
            "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}],
        })
        archive = ROOT / ".build/backend.zip"
        artifact_key = "bundles/"+hashlib.sha256(archive.read_bytes()).hexdigest()+".zip"
        s3.upload_file(str(archive), artifact_bucket, artifact_key)
        state["artifact"] = {"bucket": artifact_bucket, "key": artifact_key}
        save()
        if not args.skip_stack:
            body = json.dumps(template(artifact_bucket, artifact_key, args.region, account,
                                       state.get("outputs", {}).get("WebsiteUrl", "https://pending.invalid")))
            (deployment_dir / "template.json").write_text(body)
            exists = True
            try:
                cf.describe_stacks(StackName=args.stack)
            except ClientError:
                exists = False
            try:
                operation = cf.update_stack if exists else cf.create_stack
                operation(StackName=args.stack, TemplateBody=body, Capabilities=["CAPABILITY_NAMED_IAM"])
                print("CloudFormation submitted", flush=True)
                while True:
                    status = cf.describe_stacks(StackName=args.stack)["Stacks"][0]["StackStatus"]
                    print("CloudFormation:", status, flush=True)
                    if status in {"CREATE_COMPLETE", "UPDATE_COMPLETE"}:
                        break
                    if "FAILED" in status or "ROLLBACK" in status:
                        events = cf.describe_stack_events(StackName=args.stack)["StackEvents"]
                        failures = [{"resource": e["LogicalResourceId"], "reason": e.get("ResourceStatusReason")}
                                    for e in events if "FAILED" in e["ResourceStatus"]]
                        raise RuntimeError(json.dumps(failures))
                    time.sleep(10)
            except ClientError as e:
                if "No updates are to be performed" not in str(e):
                    raise
    outputs = {x["OutputKey"]: x["OutputValue"]
               for x in cf.describe_stacks(StackName=args.stack)["Stacks"][0]["Outputs"]}
    state.update(stack=args.stack, region=args.region, account=account, outputs=outputs)
    save()
    if not args.frontend_only:
        env = {
            "ENVIRONMENT": "production", "REGION": args.region, "MODEL_ID": "us.openai.gpt-6-sol",
            "TABLE_NAME": outputs["TableName"], "SIGNING_KEY_ID": outputs["SigningKeyId"],
            "COGNITO_POOL_ID": outputs["UserPoolId"], "COGNITO_CLIENT_ID": outputs["UserClientId"],
            "PUBLIC_URL": outputs["WebsiteUrl"], "ISSUER": outputs["ApiUrl"]+"/api/identity",
            "QUEUE_URL": outputs["QueueUrl"], "RUNTIME_MODE": "aws", "TRADING_MODE": "paper",
            "TOOL_EXECUTOR_ARN": outputs["ExecutorFunctionArn"],
            "COGNITO_DOMAIN": outputs["CognitoDomain"],
            "COGNITO_EXTERNAL_CLIENT_ID": outputs["ExternalMcpClientId"],
            "OAUTH_VAULT_KEY_ID": outputs["OAuthVaultKeyId"],
        }
        okx_arn = args.okx_secret_arn if args.okx_secret_arn is not None else state.get("environment", {}).get("OKX_SECRET_ARN")
        env["ONCHAIN_DATA_MODE"] = (
            args.onchain_data_mode or state.get("environment", {}).get("ONCHAIN_DATA_MODE")
            or ("live" if okx_arn else "demo")
        )
        if okx_arn:
            if not okx_arn.startswith(f"arn:aws:secretsmanager:{args.region}:{account}:secret:"):
                raise ValueError("OKX secret must be in this deployment's AWS account and region.")
            env["OKX_SECRET_ARN"] = okx_arn
            executor_role = lambdas.get_function_configuration(FunctionName=outputs["ExecutorFunctionArn"])["Role"].split("/")[-1]
            session.client("iam").put_role_policy(
                RoleName=executor_role, PolicyName="okx-read-credentials",
                PolicyDocument=json.dumps({"Version": "2012-10-17", "Statement": [{
                    "Effect": "Allow", "Action": ["secretsmanager:GetSecretValue"], "Resource": okx_arn,
                }]}),
            )
        if state.get("oauth_gateway"):
            env["OAUTH_GATEWAY_URL"] = state["oauth_gateway"]["url"]
        cognito = session.client("cognito-idp")
        # On a first deployment the website hostname is allocated after the client is created.
        client_info = cognito.describe_user_pool_client(
            UserPoolId=outputs["UserPoolId"], ClientId=outputs["UserClientId"],
        )["UserPoolClient"]
        accepted = cognito.meta.service_model.operation_model("UpdateUserPoolClient").input_shape.members
        client_update = {k: v for k, v in client_info.items() if k in accepted}
        client_update.update(CallbackURLs=[outputs["WebsiteUrl"]+"/api/auth/oauth/callback"],
                             LogoutURLs=[outputs["WebsiteUrl"]+"/"])
        cognito.update_user_pool_client(**client_update)
        for client_id in [outputs["UserClientId"], outputs["ExternalMcpClientId"]]:
            try:
                cognito.describe_managed_login_branding_by_client(
                    UserPoolId=outputs["UserPoolId"], ClientId=client_id, ReturnMergedResources=False,
                )
            except cognito.exceptions.ResourceNotFoundException:
                cognito.create_managed_login_branding(
                    UserPoolId=outputs["UserPoolId"], ClientId=client_id, UseCognitoProvidedValues=True,
                )
        if state.get("gateway"):
            env["GATEWAY_URL"] = state["gateway"]["url"]
        if state.get("onchain_control_agent"):
            env["AGENT_RUNTIME_ARN"] = state["onchain_control_agent"]["arn"]

        def configure_lambdas(update_code=True):
            for name in ["Api", "Request", "Response", "Executor", "Worker", "Scheduler"]:
                print("Configuring Lambda:", name, flush=True)
                function_name = outputs[name+"FunctionArn"]
                lambdas.get_waiter("function_updated_v2").wait(FunctionName=function_name)
                if args.skip_stack and update_code:
                    lambdas.update_function_code(
                        FunctionName=function_name, S3Bucket=artifact_bucket, S3Key=artifact_key,
                    )
                    lambdas.get_waiter("function_updated_v2").wait(FunctionName=function_name)
                lambdas.update_function_configuration(
                    FunctionName=function_name, Environment={"Variables": env},
                )
                lambdas.get_waiter("function_updated_v2").wait(FunctionName=function_name)
        configure_lambdas()

        def runtime(name, entry, role_name, protocol, jwt_auth=False):
            existing_id = state.get(name, {}).get("id")
            params = {
                "agentRuntimeArtifact": {"codeConfiguration": {
                    "code": {"s3": {"bucket": artifact_bucket, "prefix": artifact_key}},
                    "runtime": "PYTHON_3_12", "entryPoint": [entry],
                }},
                "roleArn": outputs[role_name+"Arn"],
                "networkConfiguration": {"networkMode": "PUBLIC"},
                "protocolConfiguration": {"serverProtocol": protocol},
                "lifecycleConfiguration": {"idleRuntimeSessionTimeout": 300, "maxLifetime": 3600},
                "environmentVariables": env,
            }
            if jwt_auth:
                params["authorizerConfiguration"] = {"customJWTAuthorizer": {
                    "discoveryUrl": env["ISSUER"]+"/.well-known/openid-configuration",
                    "allowedAudience": ["onchain-runtime"],
                }}
                params["requestHeaderConfiguration"] = {"requestHeaderAllowlist": ["Authorization"]}
            if existing_id:
                result = ac.update_agent_runtime(agentRuntimeId=existing_id, **params)
            else:
                result = ac.create_agent_runtime(agentRuntimeName=name, **params)
            state[name] = {"id": result["agentRuntimeId"], "arn": result["agentRuntimeArn"]}
            save()
            for _ in range(90):
                info = ac.get_agent_runtime(agentRuntimeId=result["agentRuntimeId"])
                print(name, info["status"], flush=True)
                if info["status"] == "READY":
                    return result["agentRuntimeArn"]
                if info["status"] in {"CREATE_FAILED", "UPDATE_FAILED"}:
                    raise RuntimeError(json.dumps(info, default=str))
                time.sleep(10)
            raise TimeoutError(name)

        mcp_arn = runtime("onchain_control_mcp", "runtimes/mcp_server.py", "McpRuntimeRole", "MCP")
        policy = {
            "Version": "2012-10-17", "Statement": [{
                "Effect": "Allow", "Principal": {"AWS": outputs["GatewayRoleArn"]},
                "Action": "bedrock-agentcore:InvokeAgentRuntime",
                "Resource": [mcp_arn, mcp_arn+"/runtime-endpoint/DEFAULT"],
            }, {
                "Effect": "Deny", "Principal": "*", "Action": "bedrock-agentcore:InvokeAgentRuntime",
                "Resource": [mcp_arn, mcp_arn+"/runtime-endpoint/DEFAULT"],
                "Condition": {"ArnNotEquals": {"aws:PrincipalArn": outputs["GatewayRoleArn"]}},
            }],
        }
        for arn in [mcp_arn, mcp_arn+"/runtime-endpoint/DEFAULT"]:
            scoped = json.loads(json.dumps(policy))
            for statement in scoped["Statement"]:
                statement["Resource"] = arn
            ac.put_resource_policy(resourceArn=arn, policy=json.dumps(scoped))
        gateway_params = {
            "name": "onchain-control-gateway", "roleArn": outputs["GatewayRoleArn"],
            "protocolType": "MCP",
            "protocolConfiguration": {"mcp": {
                "supportedVersions": ["2025-11-25", "2025-06-18", "2025-03-26"],
                "sessionConfiguration": {"sessionTimeoutInSeconds": 900},
            }},
            "authorizerType": "CUSTOM_JWT",
            "authorizerConfiguration": {"customJWTAuthorizer": {
                "discoveryUrl": env["ISSUER"]+"/.well-known/openid-configuration",
                "allowedAudience": ["onchain-gateway"], "allowedScopes": ["tools:invoke"],
            }},
            "interceptorConfigurations": [
                {"interceptor": {"lambda": {"arn": outputs["RequestFunctionArn"]}},
                 "interceptionPoints": ["REQUEST"], "inputConfiguration": {"passRequestHeaders": True}},
                {"interceptor": {"lambda": {"arn": outputs["ResponseFunctionArn"]}},
                 "interceptionPoints": ["RESPONSE"], "inputConfiguration": {"passRequestHeaders": True}},
            ],
        }
        if state.get("policy_engine"):
            gateway_params["policyEngineConfiguration"] = {
                "arn": state["policy_engine"]["arn"], "mode": "ENFORCE",
            }
        if state.get("gateway"):
            gateway = ac.update_gateway(gatewayIdentifier=state["gateway"]["id"], **gateway_params)
        else:
            gateway = ac.create_gateway(**gateway_params)
        state["gateway"] = {"id": gateway["gatewayId"], "arn": gateway["gatewayArn"],
                            "url": gateway["gatewayUrl"]}
        save()
        gateway_id = gateway["gatewayId"]
        for _ in range(60):
            info = ac.get_gateway(gatewayIdentifier=gateway_id)
            if info["status"] == "READY":
                break
            if "FAILED" in info["status"]:
                raise RuntimeError(json.dumps(info, default=str))
            time.sleep(5)
        target_params = {
            "gatewayIdentifier": gateway_id, "name": "onchain",
            "targetConfiguration": {"mcp": {"mcpServer": {
                "endpoint": f"https://bedrock-agentcore.{args.region}.amazonaws.com/runtimes/"
                            f"{quote(mcp_arn, safe='')}/invocations?qualifier=DEFAULT",
            }}},
            "credentialProviderConfigurations": [{
                "credentialProviderType": "GATEWAY_IAM_ROLE",
                "credentialProvider": {"iamCredentialProvider": {"service": "bedrock-agentcore", "region": args.region}},
            }],
        }
        if state.get("target"):
            target = ac.update_gateway_target(targetId=state["target"]["id"], **target_params)
        else:
            target = ac.create_gateway_target(**target_params)
        state["target"] = {"id": target["targetId"]}
        save()
        for _ in range(60):
            info = ac.get_gateway_target(gatewayIdentifier=gateway_id, targetId=target["targetId"])
            print("MCP target:", info["status"], flush=True)
            if info["status"] == "READY":
                break
            if "FAILED" in info["status"]:
                raise RuntimeError(json.dumps(info, default=str))
            time.sleep(5)
        else:
            raise TimeoutError("MCP target synchronization")
        if not state.get("policy_engine"):
            engine = ac.create_policy_engine(name="onchain_control_policy")
            state["policy_engine"] = {"id": engine["policyEngineId"], "arn": engine["policyEngineArn"]}
            save()
            for _ in range(40):
                info = ac.get_policy_engine(policyEngineId=engine["policyEngineId"])
                if info["status"] == "ACTIVE":
                    break
                time.sleep(3)
        from onchain.catalog import TOOL_PRODUCTS
        actions = ", ".join('AgentCore::Action::"onchain___'+t+'"' for t in TOOL_PRODUCTS)
        statement = (f'permit(principal, action in [{actions}], '
                     f'resource == AgentCore::Gateway::"{gateway["gatewayArn"]}");')
        if not state.get("policy"):
            policy = ac.create_policy(
                policyEngineId=state["policy_engine"]["id"], name="approved_platform_tools",
                definition={"cedar": {"statement": statement}},
                validationMode="IGNORE_ALL_FINDINGS",
            )
            state["policy"] = {"id": policy["policyId"]}
            save()
        else:
            ac.update_policy(policyEngineId=state["policy_engine"]["id"], policyId=state["policy"]["id"],
                             definition={"cedar": {"statement": statement}}, validationMode="IGNORE_ALL_FINDINGS")
        for _ in range(40):
            info = ac.get_policy(policyEngineId=state["policy_engine"]["id"], policyId=state["policy"]["id"])
            if info["status"] == "ACTIVE":
                break
            if "FAILED" in info["status"]:
                raise RuntimeError(json.dumps(info, default=str))
            time.sleep(3)
        else:
            raise TimeoutError("Policy activation")
        gateway_params["policyEngineConfiguration"] = {"arn": state["policy_engine"]["arn"], "mode": "ENFORCE"}
        ac.update_gateway(gatewayIdentifier=gateway_id, **gateway_params)
        env["GATEWAY_URL"] = gateway["gatewayUrl"]
        # External callers authenticate with Cognito. IAM is only the private Gateway→Runtime hop.
        oauth_params = json.loads(json.dumps(gateway_params))
        oauth_params["name"] = "onchain-oauth-gateway"
        oauth_params["authorizerConfiguration"] = {"customJWTAuthorizer": {
            "discoveryUrl": f"https://cognito-idp.{args.region}.amazonaws.com/"
                            f"{outputs['UserPoolId']}/.well-known/openid-configuration",
            "allowedClients": [outputs["ExternalMcpClientId"]],
            "allowedScopes": ["onchain/mcp.invoke"],
        }}
        # Preserve existing audience/scope checks and active policy during subsequent reconciles.
        if state.get("oauth_gateway"):
            known_url = state["oauth_gateway"]["url"]
            oauth_params["authorizerConfiguration"]["customJWTAuthorizer"].update(
                allowedAudience=[known_url], allowedScopes=[known_url + "/mcp.invoke"],
            )
        # Each Gateway has its own Cedar resource. Attach only once its policy is active.
        if not state.get("oauth_policy"):
            oauth_params.pop("policyEngineConfiguration", None)
        if state.get("oauth_gateway"):
            oauth_gateway = ac.update_gateway(gatewayIdentifier=state["oauth_gateway"]["id"], **oauth_params)
        else:
            oauth_gateway = ac.create_gateway(**oauth_params)
        state["oauth_gateway"] = {"id": oauth_gateway["gatewayId"], "arn": oauth_gateway["gatewayArn"],
                                  "url": oauth_gateway["gatewayUrl"]}
        save()
        oauth_id = oauth_gateway["gatewayId"]
        def wait_gateway(identifier):
            for _ in range(60):
                info = ac.get_gateway(gatewayIdentifier=identifier)
                if info["status"] == "READY":
                    return
                if "FAILED" in info["status"]:
                    raise RuntimeError(json.dumps(info, default=str))
                time.sleep(5)
            raise TimeoutError("OAuth Gateway")
        wait_gateway(oauth_id)
        resource_identifier = oauth_gateway["gatewayUrl"]
        resource_scopes = [
            {"ScopeName": name, "ScopeDescription": description} for name, description in [
                ("mcp.invoke", "Connect to the MCP Gateway"), ("portfolio.read", "Read owned data"),
                ("trade.prepare", "Prepare a trade for confirmation"),
                ("trade.execute", "Execute an explicitly approved trade"),
                ("control", "Manage the user's workspace"), ("approve", "Confirm a specific operation"),
                ("market.read", "Read public token, risk, route and signal data"),
            ]
        ]
        try:
            cognito.describe_resource_server(UserPoolId=outputs["UserPoolId"], Identifier=resource_identifier)
            cognito.update_resource_server(UserPoolId=outputs["UserPoolId"], Identifier=resource_identifier,
                                           Name="Public MCP Gateway", Scopes=resource_scopes)
        except cognito.exceptions.ResourceNotFoundException:
            cognito.create_resource_server(UserPoolId=outputs["UserPoolId"], Identifier=resource_identifier,
                                          Name="Public MCP Gateway", Scopes=resource_scopes)
        for client_id, scopes in [
            (outputs["UserClientId"], ["openid", "email", "aws.cognito.signin.user.admin"]
             + [resource_identifier + "/" + s["ScopeName"] for s in resource_scopes]),
            (outputs["ExternalMcpClientId"], ["openid"]
             + [resource_identifier + "/" + s["ScopeName"] for s in resource_scopes
                if s["ScopeName"] not in {"control", "approve"}]),
        ]:
            client_info = cognito.describe_user_pool_client(
                UserPoolId=outputs["UserPoolId"], ClientId=client_id,
            )["UserPoolClient"]
            client_update = {k: v for k, v in client_info.items() if k in accepted}
            client_update["AllowedOAuthScopes"] = scopes
            cognito.update_user_pool_client(**client_update)
        oauth_params["authorizerConfiguration"]["customJWTAuthorizer"]["allowedAudience"] = [oauth_gateway["gatewayUrl"]]
        oauth_params["authorizerConfiguration"]["customJWTAuthorizer"]["allowedScopes"] = [resource_identifier + "/mcp.invoke"]
        oauth_target = dict(target_params, gatewayIdentifier=oauth_id)
        if state.get("oauth_target"):
            target = ac.update_gateway_target(targetId=state["oauth_target"]["id"], **oauth_target)
        else:
            target = ac.create_gateway_target(**oauth_target)
        state["oauth_target"] = {"id": target["targetId"]}
        save()
        for _ in range(60):
            info = ac.get_gateway_target(gatewayIdentifier=oauth_id, targetId=target["targetId"])
            print("OAuth MCP target:", info["status"], flush=True)
            if info["status"] == "READY":
                break
            if "FAILED" in info["status"]:
                raise RuntimeError(json.dumps(info, default=str))
            time.sleep(5)
        else:
            raise TimeoutError("OAuth MCP target")
        oauth_statement = (f'permit(principal, action in [{actions}], '
                           f'resource == AgentCore::Gateway::"{oauth_gateway["gatewayArn"]}");')
        if not state.get("oauth_policy"):
            policy = ac.create_policy(
                policyEngineId=state["policy_engine"]["id"], name="approved_oauth_tools",
                definition={"cedar": {"statement": oauth_statement}},
                validationMode="IGNORE_ALL_FINDINGS",
            )
            state["oauth_policy"] = {"id": policy["policyId"]}
            save()
        else:
            ac.update_policy(policyEngineId=state["policy_engine"]["id"], policyId=state["oauth_policy"]["id"],
                             definition={"cedar": {"statement": oauth_statement}}, validationMode="IGNORE_ALL_FINDINGS")
        for _ in range(40):
            info = ac.get_policy(policyEngineId=state["policy_engine"]["id"], policyId=state["oauth_policy"]["id"])
            if info["status"] == "ACTIVE":
                break
            if "FAILED" in info["status"]:
                raise RuntimeError(json.dumps(info, default=str))
            time.sleep(3)
        else:
            raise TimeoutError("OAuth policy activation")
        oauth_params["policyEngineConfiguration"] = {"arn": state["policy_engine"]["arn"], "mode": "ENFORCE"}
        ac.update_gateway(gatewayIdentifier=oauth_id, **oauth_params)
        wait_gateway(oauth_id)
        env["OAUTH_GATEWAY_URL"] = oauth_gateway["gatewayUrl"]
        agent_arn = runtime("onchain_control_agent", "runtimes/agent.py", "AgentRuntimeRole", "HTTP", True)
        env["AGENT_RUNTIME_ARN"] = agent_arn
        configure_lambdas(update_code=False)
        state["environment"] = env
        save()
    distribution = session.client("cloudfront")
    for file in (ROOT / "frontend/dist").rglob("*"):
        if file.is_file():
            relative = str(file.relative_to(ROOT / "frontend/dist"))
            s3.upload_file(str(file), outputs["WebBucketName"], relative, ExtraArgs={
                "ContentType": mimetypes.guess_type(str(file))[0] or "application/octet-stream",
                "CacheControl": "no-cache" if file.name == "index.html" else "public,max-age=31536000,immutable",
            })
    distribution.create_invalidation(
        DistributionId=outputs["DistributionId"],
        InvalidationBatch={"Paths": {"Quantity": 1, "Items": ["/*"]}, "CallerReference": str(time.time())},
    )
    print("Website:", outputs["WebsiteUrl"], flush=True)
    print("API:", outputs["ApiUrl"], flush=True)


if __name__ == "__main__":
    main()
