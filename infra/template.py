"""CloudFormation infrastructure. Application code is built and deployed independently of the web app."""


def ref(name):
    return {"Ref": name}


def att(name, attribute):
    return {"Fn::GetAtt": [name, attribute]}


def sub(value):
    return {"Fn::Sub": value}


def template(artifact_bucket, artifact_key, region, account, public_url="https://pending.invalid"):
    resources = {}
    r = resources
    log_policy = {"Effect": "Allow", "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
                  "Resource": f"arn:aws:logs:{region}:{account}:log-group:*onchain*"}
    table_policy = {"Effect": "Allow", "Action": ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem",
                                               "dynamodb:DeleteItem", "dynamodb:Scan"],
                    "Resource": att("StateTable", "Arn")}
    verify_policy = {"Effect": "Allow", "Action": ["kms:GetPublicKey"], "Resource": att("SigningKey", "Arn")}
    sign_policy = {"Effect": "Allow", "Action": ["kms:Sign"], "Resource": att("SigningKey", "Arn")}
    vault_policy = {"Effect": "Allow", "Action": ["kms:Encrypt", "kms:Decrypt"],
                    "Resource": att("OAuthVaultKey", "Arn")}
    queue_send = {"Effect": "Allow", "Action": ["sqs:SendMessage"], "Resource": att("JobsQueue", "Arn")}

    def role(name, service, policies):
        trust = {"Effect": "Allow", "Principal": {"Service": service}, "Action": "sts:AssumeRole"}
        if service == "bedrock-agentcore.amazonaws.com":
            trust["Condition"] = {"StringEquals": {"aws:SourceAccount": account}}
        r[name] = {"Type": "AWS::IAM::Role", "Properties": {
            "RoleName": "onchain-control-"+name.lower(),
            "AssumeRolePolicyDocument": {"Version": "2012-10-17", "Statement": [trust]},
            "Policies": [{"PolicyName": "scoped-access", "PolicyDocument": {
                "Version": "2012-10-17", "Statement": policies,
            }}],
        }}

    r["StateTable"] = {"Type": "AWS::DynamoDB::Table", "DeletionPolicy": "Retain",
                      "UpdateReplacePolicy": "Retain", "Properties": {
        "TableName": "onchain-control-state", "BillingMode": "PAY_PER_REQUEST",
        "AttributeDefinitions": [{"AttributeName": "pk", "AttributeType": "S"}],
        "KeySchema": [{"AttributeName": "pk", "KeyType": "HASH"}],
        "SSESpecification": {"SSEEnabled": True},
        "PointInTimeRecoverySpecification": {"PointInTimeRecoveryEnabled": True},
        "TimeToLiveSpecification": {"AttributeName": "ttl", "Enabled": True},
    }}
    r["SigningKey"] = {"Type": "AWS::KMS::Key", "DeletionPolicy": "Retain", "Properties": {
        "Description": "Onchain Control short-lived capability signing",
        "KeySpec": "RSA_2048", "KeyUsage": "SIGN_VERIFY",
        "KeyPolicy": {"Version": "2012-10-17", "Statement": [{
            "Sid": "EnableIAM", "Effect": "Allow",
            "Principal": {"AWS": f"arn:aws:iam::{account}:root"}, "Action": "kms:*", "Resource": "*",
        }]},
    }}
    r["OAuthVaultKey"] = {"Type": "AWS::KMS::Key", "DeletionPolicy": "Retain", "Properties": {
        "Description": "Cognito refresh tokens bound to individual BFF sessions",
        "EnableKeyRotation": True,
        "KeyPolicy": {"Version": "2012-10-17", "Statement": [{
            "Sid": "EnableIAM", "Effect": "Allow",
            "Principal": {"AWS": f"arn:aws:iam::{account}:root"}, "Action": "kms:*", "Resource": "*",
        }]},
    }}
    r["UserPool"] = {"Type": "AWS::Cognito::UserPool", "DeletionPolicy": "Retain", "Properties": {
        "UserPoolName": "onchain-control-users", "UsernameAttributes": ["email"], "UserPoolTier": "ESSENTIALS",
        "AutoVerifiedAttributes": ["email"], "MfaConfiguration": "OPTIONAL",
        "EnabledMfas": ["SOFTWARE_TOKEN_MFA"],
        "Policies": {"PasswordPolicy": {
            "MinimumLength": 12, "RequireLowercase": True, "RequireUppercase": True,
            "RequireNumbers": True, "RequireSymbols": True, "TemporaryPasswordValidityDays": 1,
        }},
        "AccountRecoverySetting": {"RecoveryMechanisms": [{"Name": "verified_email", "Priority": 1}]},
        "Schema": [{"Name": "email", "Required": True, "Mutable": True, "AttributeDataType": "String"}],
    }}
    r["OAuthDomain"] = {"Type": "AWS::Cognito::UserPoolDomain", "Properties": {
        "Domain": f"onchain-control-{account}-{region}", "UserPoolId": ref("UserPool"),
        "ManagedLoginVersion": 2,
    }}
    r["OAuthResource"] = {"Type": "AWS::Cognito::UserPoolResourceServer", "Properties": {
        "UserPoolId": ref("UserPool"), "Identifier": "onchain", "Name": "Onchain Control",
        "Scopes": [{"ScopeName": name, "ScopeDescription": description} for name, description in [
            ("mcp.invoke", "Connect to the MCP Gateway"),
            ("portfolio.read", "Read your portfolio, quote and operations"),
            ("trade.prepare", "Prepare a trade that requires your confirmation"),
            ("trade.execute", "Execute only a specifically approved trade"),
            ("control", "Manage your platform workspace"),
            ("approve", "Confirm a specific operation after fresh authentication"),
            ("market.read", "Read public token, risk, route and signal data"),
        ]],
    }}
    mcp_scopes = ["onchain/mcp.invoke", "onchain/portfolio.read", "onchain/trade.prepare", "onchain/trade.execute",
                  "onchain/market.read"]
    r["UserClient"] = {"Type": "AWS::Cognito::UserPoolClient", "DependsOn": "OAuthResource", "Properties": {
        "UserPoolId": ref("UserPool"), "ClientName": "onchain-control-bff", "GenerateSecret": False,
        "ExplicitAuthFlows": ["ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"],
        "PreventUserExistenceErrors": "ENABLED", "EnableTokenRevocation": True,
        "AccessTokenValidity": 15, "IdTokenValidity": 15, "RefreshTokenValidity": 1,
        "TokenValidityUnits": {"AccessToken": "minutes", "IdToken": "minutes", "RefreshToken": "days"},
        "AllowedOAuthFlowsUserPoolClient": True, "AllowedOAuthFlows": ["code"],
        "AllowedOAuthScopes": ["openid", "email", "aws.cognito.signin.user.admin",
                              "onchain/control", "onchain/approve", *mcp_scopes],
        "CallbackURLs": [public_url + "/api/auth/oauth/callback"], "LogoutURLs": [public_url + "/"],
        "SupportedIdentityProviders": ["COGNITO"],
    }}
    r["ExternalMcpClient"] = {"Type": "AWS::Cognito::UserPoolClient", "DependsOn": "OAuthResource", "Properties": {
        "UserPoolId": ref("UserPool"), "ClientName": "onchain-external-mcp", "GenerateSecret": False,
        "ExplicitAuthFlows": ["ALLOW_REFRESH_TOKEN_AUTH"],
        "AllowedOAuthFlowsUserPoolClient": True, "AllowedOAuthFlows": ["code"],
        "AllowedOAuthScopes": ["openid", *mcp_scopes],
        "CallbackURLs": ["http://localhost:8765/callback"], "SupportedIdentityProviders": ["COGNITO"],
        "PreventUserExistenceErrors": "ENABLED", "EnableTokenRevocation": True,
        "AccessTokenValidity": 15, "IdTokenValidity": 15, "RefreshTokenValidity": 1,
        "TokenValidityUnits": {"AccessToken": "minutes", "IdToken": "minutes", "RefreshToken": "days"},
    }}
    r["AdminGroup"] = {"Type": "AWS::Cognito::UserPoolGroup", "Properties": {
        "UserPoolId": ref("UserPool"), "GroupName": "platform-admin",
        "Description": "Reviewed platform administrators; no agent can join this group.",
    }}
    r["DeadLetterQueue"] = {"Type": "AWS::SQS::Queue", "Properties": {
        "QueueName": "onchain-control-dlq", "SqsManagedSseEnabled": True, "MessageRetentionPeriod": 1209600,
    }}
    r["JobsQueue"] = {"Type": "AWS::SQS::Queue", "Properties": {
        "QueueName": "onchain-control-jobs", "SqsManagedSseEnabled": True,
        "VisibilityTimeout": 240, "MessageRetentionPeriod": 86400,
        "RedrivePolicy": {"deadLetterTargetArn": att("DeadLetterQueue", "Arn"), "maxReceiveCount": 3},
    }}
    role("ApiRole", "lambda.amazonaws.com", [
        log_policy, table_policy, verify_policy, sign_policy, vault_policy, queue_send,
        {"Effect": "Allow", "Action": ["cognito-idp:AdminListGroupsForUser"], "Resource": att("UserPool", "Arn")},
    ])
    role("RequestRole", "lambda.amazonaws.com", [log_policy, table_policy, verify_policy, sign_policy])
    role("ResponseRole", "lambda.amazonaws.com", [log_policy, table_policy, verify_policy])
    role("ExecutorRole", "lambda.amazonaws.com", [log_policy, table_policy, verify_policy])
    role("WorkerRole", "lambda.amazonaws.com", [
        log_policy, table_policy, verify_policy,
        {"Effect": "Allow", "Action": ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"],
         "Resource": att("JobsQueue", "Arn")},
    ])
    role("SchedulerRole", "lambda.amazonaws.com", [log_policy, table_policy, verify_policy, sign_policy, queue_send])

    shared_env = {
        "ENVIRONMENT": "production", "AWS_REGION_NAME": region, "REGION": region,
        "TABLE_NAME": ref("StateTable"), "SIGNING_KEY_ID": ref("SigningKey"),
        "COGNITO_POOL_ID": ref("UserPool"), "COGNITO_CLIENT_ID": ref("UserClient"),
        "MODEL_ID": "us.openai.gpt-6-sol", "RUNTIME_MODE": "aws",
        "QUEUE_URL": ref("JobsQueue"), "TRADING_MODE": "paper",
        # Deployment reconciles these once CloudFront/API URLs are allocated.
        "PUBLIC_URL": "https://pending.invalid", "ISSUER": "https://pending.invalid/api/identity",
    }

    def function(name, handler, role_id, timeout=30, memory=512):
        function_name = "onchain-control-"+name.lower()
        r[name+"Log"] = {"Type": "AWS::Logs::LogGroup", "Properties": {
            "LogGroupName": "/aws/lambda/"+function_name, "RetentionInDays": 30,
        }}
        r[name] = {"Type": "AWS::Lambda::Function", "DependsOn": name+"Log", "Properties": {
            "FunctionName": function_name, "Runtime": "python3.12", "Architectures": ["arm64"],
            "Handler": handler, "Role": att(role_id, "Arn"), "Timeout": timeout, "MemorySize": memory,
            "Code": {"S3Bucket": artifact_bucket, "S3Key": artifact_key},
            "Environment": {"Variables": shared_env},
        }}
    function("ApiFunction", "onchain.api.handler", "ApiRole")
    function("RequestFunction", "onchain.interceptors.request_handler", "RequestRole", 20)
    function("ResponseFunction", "onchain.interceptors.response_handler", "ResponseRole", 20)
    function("ExecutorFunction", "onchain.services.executor_handler", "ExecutorRole", 30)
    function("WorkerFunction", "onchain.worker.handler", "WorkerRole", 180, 1024)
    function("SchedulerFunction", "onchain.scheduler.handler", "SchedulerRole", 120)
    r["JobsMapping"] = {"Type": "AWS::Lambda::EventSourceMapping", "Properties": {
        "EventSourceArn": att("JobsQueue", "Arn"), "FunctionName": ref("WorkerFunction"),
        "BatchSize": 1, "FunctionResponseTypes": ["ReportBatchItemFailures"],
    }}
    r["ScheduleRule"] = {"Type": "AWS::Events::Rule", "Properties": {
        "Name": "onchain-control-scheduler", "ScheduleExpression": "rate(1 minute)", "State": "ENABLED",
        "Targets": [{"Id": "Scheduler", "Arn": att("SchedulerFunction", "Arn")}],
    }}
    r["SchedulePermission"] = {"Type": "AWS::Lambda::Permission", "Properties": {
        "FunctionName": ref("SchedulerFunction"), "Action": "lambda:InvokeFunction",
        "Principal": "events.amazonaws.com", "SourceArn": att("ScheduleRule", "Arn"),
    }}
    r["HttpApi"] = {"Type": "AWS::ApiGatewayV2::Api", "Properties": {
        "Name": "onchain-control-api", "ProtocolType": "HTTP",
    }}
    r["HttpIntegration"] = {"Type": "AWS::ApiGatewayV2::Integration", "Properties": {
        "ApiId": ref("HttpApi"), "IntegrationType": "AWS_PROXY",
        "IntegrationUri": att("ApiFunction", "Arn"), "PayloadFormatVersion": "2.0",
        "TimeoutInMillis": 29000,
    }}
    r["HttpRoute"] = {"Type": "AWS::ApiGatewayV2::Route", "Properties": {
        "ApiId": ref("HttpApi"), "RouteKey": "$default",
        "Target": {"Fn::Join": ["/", ["integrations", ref("HttpIntegration")]]},
    }}
    r["HttpStage"] = {"Type": "AWS::ApiGatewayV2::Stage", "Properties": {
        "ApiId": ref("HttpApi"), "StageName": "$default", "AutoDeploy": True,
        "DefaultRouteSettings": {"ThrottlingBurstLimit": 100, "ThrottlingRateLimit": 50},
    }}
    r["ApiPermission"] = {"Type": "AWS::Lambda::Permission", "Properties": {
        "FunctionName": ref("ApiFunction"), "Action": "lambda:InvokeFunction",
        "Principal": "apigateway.amazonaws.com",
        "SourceArn": sub("arn:aws:execute-api:${AWS::Region}:${AWS::AccountId}:${HttpApi}/*"),
    }}
    r["WebBucket"] = {"Type": "AWS::S3::Bucket", "DeletionPolicy": "Retain", "Properties": {
        "PublicAccessBlockConfiguration": {"BlockPublicAcls": True, "BlockPublicPolicy": True,
                                           "IgnorePublicAcls": True, "RestrictPublicBuckets": True},
        "BucketEncryption": {"ServerSideEncryptionConfiguration": [{"ServerSideEncryptionByDefault": {
            "SSEAlgorithm": "AES256"}}]},
    }}
    r["WebOAC"] = {"Type": "AWS::CloudFront::OriginAccessControl", "Properties": {
        "OriginAccessControlConfig": {"Name": "onchain-control-web", "OriginAccessControlOriginType": "s3",
                                      "SigningBehavior": "always", "SigningProtocol": "sigv4"},
    }}
    r["WebHeaders"] = {"Type": "AWS::CloudFront::ResponseHeadersPolicy", "Properties": {
        "ResponseHeadersPolicyConfig": {"Name": "onchain-control-security",
            "SecurityHeadersConfig": {
                "ContentTypeOptions": {"Override": True},
                "FrameOptions": {"FrameOption": "DENY", "Override": True},
                "ReferrerPolicy": {"ReferrerPolicy": "same-origin", "Override": True},
                "StrictTransportSecurity": {"AccessControlMaxAgeSec": 31536000, "Override": True},
                "ContentSecurityPolicy": {"Override": True, "ContentSecurityPolicy":
                    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                    "font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; "
                    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"},
            }},
    }}
    r["WebDistribution"] = {"Type": "AWS::CloudFront::Distribution", "Properties": {
        "DistributionConfig": {
            "Comment": "Onchain Control independent frontend", "Enabled": True,
            "DefaultRootObject": "index.html", "PriceClass": "PriceClass_100", "HttpVersion": "http2and3",
            "Origins": [
                {"Id": "web", "DomainName": att("WebBucket", "RegionalDomainName"),
                 "OriginAccessControlId": ref("WebOAC"), "S3OriginConfig": {"OriginAccessIdentity": ""}},
                {"Id": "api", "DomainName": {"Fn::Select": [2, {"Fn::Split": ["/", att("HttpApi", "ApiEndpoint")]}]},
                 "CustomOriginConfig": {"OriginProtocolPolicy": "https-only", "OriginSSLProtocols": ["TLSv1.2"]}},
            ],
            "DefaultCacheBehavior": {
                "TargetOriginId": "web", "ViewerProtocolPolicy": "redirect-to-https",
                "AllowedMethods": ["GET", "HEAD", "OPTIONS"], "Compress": True,
                "CachePolicyId": "658327ea-f89d-4fab-a63d-7e88639e58f6",
                "ResponseHeadersPolicyId": ref("WebHeaders"),
            },
            "CacheBehaviors": [{
                "PathPattern": "/api/*", "TargetOriginId": "api", "ViewerProtocolPolicy": "https-only",
                "AllowedMethods": ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"],
                "CachePolicyId": "4135ea2d-6df8-44a3-9df3-4b5a84be39ad",
                "OriginRequestPolicyId": "b689b0a8-53d0-40ab-baf2-68738e2966ac",
                "ResponseHeadersPolicyId": ref("WebHeaders"),
            }],
        },
    }}
    r["WebPolicy"] = {"Type": "AWS::S3::BucketPolicy", "Properties": {
        "Bucket": ref("WebBucket"), "PolicyDocument": {"Version": "2012-10-17", "Statement": [{
            "Effect": "Allow", "Principal": {"Service": "cloudfront.amazonaws.com"},
            "Action": "s3:GetObject", "Resource": sub("${WebBucket.Arn}/*"),
            "Condition": {"StringEquals": {"AWS:SourceArn": sub(
                "arn:aws:cloudfront::${AWS::AccountId}:distribution/${WebDistribution}")}},
        }]},
    }}
    artifact_read = {"Effect": "Allow", "Action": ["s3:GetObject", "s3:GetObjectVersion"],
                     "Resource": f"arn:aws:s3:::{artifact_bucket}/bundles/*"}
    runtime_logs = {"Effect": "Allow", "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents",
                                               "logs:DescribeLogStreams", "logs:DescribeLogGroups"],
                    "Resource": f"arn:aws:logs:{region}:{account}:*"}
    role("AgentRuntimeRole", "bedrock-agentcore.amazonaws.com", [
        artifact_read, runtime_logs, verify_policy,
        {"Effect": "Allow", "Action": ["bedrock:InvokeModel"], "Resource": [
            f"arn:aws:bedrock:{region}:{account}:inference-profile/us.openai.gpt-6-sol",
            "arn:aws:bedrock:*::foundation-model/openai.gpt-6-sol",
        ]},
    ])
    role("McpRuntimeRole", "bedrock-agentcore.amazonaws.com", [
        artifact_read, runtime_logs, verify_policy,
        {"Effect": "Allow", "Action": ["lambda:InvokeFunction"], "Resource": att("ExecutorFunction", "Arn")},
    ])
    role("GatewayRole", "bedrock-agentcore.amazonaws.com", [
        {"Effect": "Allow", "Action": ["bedrock-agentcore:GetPolicyEngine"],
         "Resource": f"arn:aws:bedrock-agentcore:{region}:{account}:policy-engine/onchain_control_policy-*"},
        {"Effect": "Allow", "Action": ["bedrock-agentcore:AuthorizeAction",
                                     "bedrock-agentcore:PartiallyAuthorizeActions"],
         "Resource": [
             f"arn:aws:bedrock-agentcore:{region}:{account}:policy-engine/onchain_control_policy-*",
             f"arn:aws:bedrock-agentcore:{region}:{account}:gateway/onchain-control-gateway-*",
             f"arn:aws:bedrock-agentcore:{region}:{account}:gateway/onchain-oauth-gateway-*",
         ]},
        {"Effect": "Allow", "Action": ["lambda:InvokeFunction"],
         "Resource": [att("RequestFunction", "Arn"), att("ResponseFunction", "Arn")]},
        {"Effect": "Allow", "Action": ["bedrock-agentcore:InvokeAgentRuntime"],
         "Resource": f"arn:aws:bedrock-agentcore:{region}:{account}:runtime/onchain_control_mcp-*"},
        {"Effect": "Allow", "Action": ["bedrock-agentcore:GetWorkloadAccessToken"],
         "Resource": [
             f"arn:aws:bedrock-agentcore:{region}:{account}:workload-identity-directory/default",
             f"arn:aws:bedrock-agentcore:{region}:{account}:workload-identity-directory/default/workload-identity/*",
         ]},
    ])
    outputs = {
        "ApiUrl": {"Value": att("HttpApi", "ApiEndpoint")},
        "WebsiteUrl": {"Value": sub("https://${WebDistribution.DomainName}")},
        "DistributionId": {"Value": ref("WebDistribution")},
        "WebBucketName": {"Value": ref("WebBucket")},
        "TableName": {"Value": ref("StateTable")},
        "SigningKeyId": {"Value": ref("SigningKey")},
        "OAuthVaultKeyId": {"Value": ref("OAuthVaultKey")},
        "CognitoDomain": {"Value": f"https://onchain-control-{account}-{region}.auth.{region}.amazoncognito.com"},
        "ExternalMcpClientId": {"Value": ref("ExternalMcpClient")},
        "UserPoolId": {"Value": ref("UserPool")},
        "UserClientId": {"Value": ref("UserClient")},
        "QueueUrl": {"Value": ref("JobsQueue")},
    }
    for name in ["ApiFunction", "RequestFunction", "ResponseFunction", "ExecutorFunction",
                 "WorkerFunction", "SchedulerFunction", "AgentRuntimeRole", "McpRuntimeRole", "GatewayRole"]:
        outputs[name+"Arn"] = {"Value": att(name, "Arn")}
    return {"AWSTemplateFormatVersion": "2010-09-09",
            "Description": "Onchain Control: Cognito, Gateway enforcement, isolated AgentCore runtimes.",
            "Resources": resources, "Outputs": outputs}
