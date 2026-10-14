import asyncio
import json
import time
from urllib.parse import quote

import httpx

from .domain import now
from .security import Denied, Principal
from .services import dependencies
from .invocations import local_execute
from .identity import hold_for_consent


async def process(job):
    config, store, control, tokens = dependencies()
    p = Principal.model_validate(job["principal"])
    sid, run_id = job["sid"], job["run_id"]
    async def progress(**event):
        allowed = {"MODEL_STARTED", "INTENT_RECOGNIZED", "TASK_STARTED", "MCP_CONNECTING",
                   "MCP_CONNECTED", "TOOL_CALL", "TOOL_RESULT"}
        if event.get("kind") in allowed:
            control.trace(p, sid, run_id, **{k: v for k, v in event.items()
                                          if k in {"kind", "source", "label", "details", "status", "duration_ms"}})
    try:
        control.start_run(p, sid, run_id, job["kind"])
        control.run_status(p, sid, run_id, "RUNNING")
        claims = tokens.verify(job["runtime_token"], "onchain-runtime")
        control.validate_capability(claims)
        control.trace(p, sid, run_id, "IDENTITY_VERIFIED", "worker", "用户与会话校验通过")
        if job["kind"] == "execute":
            if config.runtime_mode == "aws":
                from runtimes.agent import call_gateway
                result = await call_gateway(
                    config.gateway_url, job["gateway_token"], "execute_trade",
                    {"operation_id": job["operation_id"]}, progress,
                )
            else:
                from .interceptors import request_handler
                event = {
                    "mcp": {"gatewayRequest": {
                        "headers": {"Authorization": "Bearer "+job["gateway_token"]},
                        "body": {"jsonrpc": "2.0", "id": run_id, "method": "tools/call",
                                 "params": {"name": "execute_trade",
                                            "arguments": {"operation_id": job["operation_id"]}}},
                    }}
                }
                check = request_handler(event, None)["mcp"]
                await progress(kind="TOOL_CALL", source="local", label="本地调用 execute_trade",
                               details={"tool": "execute_trade", "arguments": {"operation_id": job["operation_id"]}})
                if "transformedGatewayResponse" in check:
                    result = check["transformedGatewayResponse"]["body"]
                else:
                    result = local_execute(config, control, tokens, claims, "execute_trade",
                                          {"operation_id": job["operation_id"]})
                await progress(kind="TOOL_RESULT", source="local", label="execute_trade 返回结果", details=result)
            result = hold_for_consent(result, job, store, p, config)
            control.append_message(p, sid, "assistant", {"action": "execute", "tool_result": result})
            outcome = result
        else:
            session = control.conversation(p, sid)
            payload = {"message": job["message"], "history": session["messages"][-10:-1],
                       "runtime_session_id": claims["runtime_sid"], "run_id": run_id,
                       "gateway_token": job["gateway_token"]}
            if job["kind"] == "task":
                payload["task"] = job["task"]
            if config.runtime_mode == "aws":
                endpoint = (
                    f"https://bedrock-agentcore.{config.region}.amazonaws.com/runtimes/"
                    f"{quote(config.agent_runtime_arn, safe='')}/invocations?qualifier=DEFAULT"
                )
                async with httpx.AsyncClient(timeout=130) as client:
                    async with client.stream(
                        "POST", endpoint, json=payload,
                        headers={"Authorization": "Bearer "+job["runtime_token"],
                                 "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": claims["runtime_sid"]},
                    ) as response:
                        response.raise_for_status()
                        if "text/event-stream" in response.headers.get("content-type", ""):
                            result = None
                            async for line in response.aiter_lines():
                                if not line.startswith("data: "):
                                    continue
                                event = json.loads(line[6:])
                                if event["kind"] == "RUN_RESULT":
                                    result = event["result"]
                                elif event["kind"] == "RUN_ERROR":
                                    if event["code"] == "RUNTIME_FAILED":
                                        raise RuntimeError("Runtime reported an execution failure")
                                    raise Denied(event["code"], event["message"])
                                else:
                                    await progress(**event)
                            if result is None:
                                raise RuntimeError("Runtime stream ended without a result")
                        else:
                            # Compatible with an older Runtime during rolling deployments.
                            await response.aread()
                            result = response.json()
            elif job["kind"] == "task":
                from runtimes.task_runner import run_template
                async def local_call(tool, args):
                    control.authorize_tool(claims, tool, args)
                    return local_execute(config, control, tokens, claims, tool, args)
                result = await run_template(job["task"], claims, local_call, progress)
            else:
                from .reasoning import recognize, tool_for_intent
                started = time.monotonic()
                await progress(kind="MODEL_STARTED", source="model", label="Bedrock 正在识别意图",
                               details={"model_id": config.model_id})
                from .catalog import skill_by_id
                skill = skill_by_id(control.agent(p, claims["agent_id"])["skill_id"])
                result = await asyncio.to_thread(recognize, config, job["message"], payload["history"], skill)
                await progress(kind="INTENT_RECOGNIZED", source="model", label="意图识别完成",
                               details={"intent": result["intent"], "usage": result.get("usage", {})},
                               duration_ms=int((time.monotonic()-started)*1000))
                mapped = tool_for_intent(result["intent"])
                if mapped:
                    from .interceptors import request_handler
                    tool, args = mapped
                    result["tool"] = tool
                    await progress(kind="TOOL_CALL", source="local", label="本地调用 "+tool,
                                   details={"tool": tool, "arguments": args})
                    check = request_handler({"mcp": {"gatewayRequest": {
                        "headers": {"Authorization": "Bearer "+job["gateway_token"]},
                        "body": {"jsonrpc": "2.0", "id": run_id, "method": "tools/call",
                                 "params": {"name": tool, "arguments": dict(args)}},
                    }}}, None)["mcp"]
                    if "transformedGatewayResponse" in check:
                        result["tool_result"] = check["transformedGatewayResponse"]["body"]
                    else:
                        result["tool_result"] = local_execute(config, control, tokens, claims, tool, args)
                    await progress(kind="TOOL_RESULT", source="local", label=tool+" 返回结果",
                                   details=result["tool_result"])
            # Recheck ownership/lifecycle after long-running inference.
            control.conversation(p, sid)
            result = hold_for_consent(result, job, store, p, config)
            control.append_message(p, sid, "assistant", result)
            outcome = result.get("tool_result", result)
        error = outcome.get("error") if isinstance(outcome, dict) else None
        final_status = "BLOCKED" if error else "SUCCEEDED"
        control.trace(p, sid, run_id, "RUN_FINISHED", "worker",
                      "运行被阻止" if error else "本次运行完成",
                      {"error": error} if error else {"status": outcome.get("status", "completed")},
                      status="BLOCKED" if error else "OK")
        control.run_status(p, sid, run_id, final_status)
    except Exception as e:
        code = e.code if isinstance(e, Denied) else "RUN_FAILED"
        message = e.message if isinstance(e, Denied) else "运行失败，请查看服务状态后重试。"
        try:
            control.trace(p, sid, run_id, "RUN_FAILED", "worker", "本次运行未完成",
                          {"code": code, "message": message}, status="ERROR")
            control.run_status(p, sid, run_id, "BLOCKED" if isinstance(e, Denied) else "FAILED")
            control.append_message(p, sid, "assistant", {"error": {"code": code, "message": message}})
        except Denied:
            pass
        # Log only a sanitized error class; never request payloads/capabilities.
        diagnostic = {"event": "run_failed", "run_id": run_id, "error_type": type(e).__name__}
        if isinstance(e, httpx.HTTPStatusError):
            diagnostic["http_status"] = e.response.status_code
            diagnostic["request_id"] = e.response.headers.get("x-amzn-requestid")
        print(json.dumps(diagnostic))
    finally:
        control.release_conversation(sid, run_id)


def handler(event, _context):
    for record in event.get("Records", []):
        job = json.loads(record["body"])
        _config, store, _control, _tokens = dependencies()
        key = "JOB#"+job["run_id"]
        # SQS can redeliver after a crash. A lease prevents concurrent runs; expiry permits recovery.
        def claim(current):
            current = current or {}
            if current.get("done") or current.get("until", 0) > now():
                return current, False
            return {"until": now()+180, "expires_at": now()+86400}, True
        if not store.mutate(key, claim):
            continue
        asyncio.run(process(job))
        store.put(key, {"done": True, "expires_at": now()+86400})
    return {"batchItemFailures": []}
