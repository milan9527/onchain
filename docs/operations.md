# Operations

Deployment state: `.deployment/resources.json` (not committed).

## Independent releases

- Frontend: build `frontend/`, then `scripts/deploy.py --frontend-only`.
- Backend + runtimes: build Python bundle, then `scripts/deploy.py --skip-stack`.
- Infrastructure: run deployment without `--skip-stack`.

The backend builder selects ARM64 Linux wheels for Python 3.12. AgentCore deploys the zip directly,
without a Docker daemon or container registry. No deployment step starts real crypto transfers.

## Monitoring

Lambda log groups:

```text
/aws/lambda/onchain-control-apifunction
/aws/lambda/onchain-control-requestfunction
/aws/lambda/onchain-control-responsefunction
/aws/lambda/onchain-control-executorfunction
/aws/lambda/onchain-control-workerfunction
/aws/lambda/onchain-control-schedulerfunction
```

Use the runtime IDs in deployment state to locate AgentCore application/runtime logs.
Inspect `onchain-control-dlq` for timed-out jobs. Before replaying an execution, query its operation
record: a `CONFIRMED` operation must not create a second receipt or deduct funds again.

## Stop and recovery

- User-level stop: pause the agent; revoke any automatic grant.
- Tool-level stop: disable MCP subscription for the user; use Gateway Policy forbid for a platform-wide incident.
- Scheduler stop: disable the EventBridge rule `onchain-control-scheduler`.
- Whole-platform stop: disable queue consumption and deny sensitive Gateway tools; retain records for investigation.
- Recovery: review logs, restore approved manifests and permissions, re-authenticate, and explicitly resume agents.

Do not change Gateway Policy to LOG_ONLY to make a denied operation pass.

## Test users

`scripts/smoke_users.py` creates accounts without sending email. Delete them after testing:

```bash
aws cognito-idp admin-delete-user --user-pool-id POOL_ID --username TEST_USERNAME
```

Do not paste `.deployment/smoke-credentials.json` into tickets or logs.

## Removal

CloudFormation retains the state table, Cognito user pool, signing key and frontend bucket.
AgentCore runtimes, Gateway/targets/policy engine and the artifact bucket are reconciled by the deployment
script and must be removed separately if the environment is retired. Review retained data before removal.
The deployment script intentionally has no destructive automatic cleanup path.

## Existing conversation migration

After updating backend code, run `scripts/backfill_history.py` once to index existing, nonempty sessions.
It scans application session records using deployment credentials, preserves the original execution
expiry as `active_until`, and retains existing messages for the configured history window.
The user-facing history APIs perform owner-specific key reads and never scan other users' records.
No historical tool calls are inferred from message text.
