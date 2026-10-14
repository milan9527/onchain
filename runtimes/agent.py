import asyncio
import json
import time
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from mcp import ClientSession, McpError
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

from onchain.config import settings
from onchain.reasoning import recognize, tool_for_intent
from onchain.catalog import SKILLS
from onchain.security import TokenService, Denied, bearer
from runtimes.isolation import SessionBinding

app = FastAPI()
binding = SessionBinding()


async def no_progress(**_event):
    pass


async def call_gateway(url, token, tool, args, progress=no_progress):
    # A fresh ClientSession for every run. Never share a global MCP client across users.
    await progress(kind="MCP_CONNECTING", source="mcp", label="连接 MCP Gateway",
                   details={"mcp_server": "onchain"})
    async with create_mcp_http_client(headers={"Authorization": "Bearer "+token}) as client:
        async with streamable_http_client(url, http_client=client) as (read, write, _):
            async with ClientSession(read, write) as session:
                initialized = await session.initialize()
                await progress(kind="MCP_CONNECTED", source="mcp", label="MCP 会话已建立",
                               details={"mcp_server": "onchain", "protocol_version": initialized.protocolVersion})
                await progress(kind="TOOL_CALL", source="mcp", label="调用 "+tool,
                               details={"mcp_server": "onchain", "tool": tool, "arguments": args})
                started = time.monotonic()
                try:
                    result = await session.call_tool("onchain___"+tool, args)
                except McpError as e:
                    data = e.error.data if isinstance(e.error.data, dict) else {}
                    from onchain.identity import authorization_challenge
                    challenge = authorization_challenge(e.error.model_dump(), settings())
                    payload = {"error": challenge or {"code": data.get("code", "MCP_ERROR"),
                                                      "message": e.error.message}}
                    await progress(kind="TOOL_RESULT", source="mcp", label=tool+" 调用被拒绝",
                                   details=payload, status="BLOCKED",
                                   duration_ms=int((time.monotonic()-started)*1000))
                    return payload
                texts = [block.text for block in result.content if block.type == "text"]
                text = "\n".join(texts)
                from onchain.identity import authorization_challenge
                challenge = authorization_challenge(result.model_dump(), settings()) if result.isError else None
                if challenge:
                    return {"error": challenge}
                try:
                    payload = json.loads(text)
                except ValueError:
                    payload = {"message": text}
                if not isinstance(payload, dict):
                    payload = {"value": payload}
                if result.isError:
                    payload = {"error": payload}
                await progress(kind="TOOL_RESULT", source="mcp", label=tool+" 返回结果",
                               details={"tool": tool, "result": payload},
                               status="ERROR" if payload.get("error") else "OK",
                               duration_ms=int((time.monotonic()-started)*1000))
                return payload


async def run(payload, claims, config, progress=no_progress):
    if payload.get("runtime_session_id") != claims["runtime_sid"]:
        raise Denied("SESSION_MISMATCH", "Invalid runtime session.")
    if payload.get("run_id") != claims["run_id"]:
        raise Denied("RUN_MISMATCH", "Invalid invocation.")
    if payload.get("task") is not None:
        from runtimes.task_runner import run_template
        async def call(tool, args):
            return await call_gateway(config.gateway_url, payload["gateway_token"], tool, args, progress)
        return await run_template(payload["task"], claims, call, progress)
    message = payload.get("message")
    if not isinstance(message, str) or not 1 <= len(message) <= 4000:
        raise Denied("INVALID_MESSAGE", "Message must be a string of 1–4000 characters.")
    # Bedrock's synchronous SDK must not block Runtime health checks during inference.
    started = time.monotonic()
    await progress(kind="MODEL_STARTED", source="model", label="Bedrock 正在识别意图",
                   details={"model_id": config.model_id})
    skill = next((s for s in SKILLS if s["digest"] == claims.get("skill_digest")), None)
    if not skill:
        raise Denied("SKILL_CHANGED", "The reviewed skill version is unavailable.")
    result = await asyncio.to_thread(recognize, config, message, payload.get("history", []), skill)
    await progress(kind="INTENT_RECOGNIZED", source="model", label="意图识别完成",
                   details={"intent": result["intent"], "usage": result.get("usage", {})},
                   duration_ms=int((time.monotonic()-started)*1000))
    print(json.dumps({"event": "intent_recognized", "action": result["intent"]["action"]}), flush=True)
    intent = result["intent"]
    mapped = tool_for_intent(intent)
    if mapped:
        tool, args = mapped
        result["tool"] = tool
        result["tool_result"] = await call_gateway(
            config.gateway_url, payload["gateway_token"], tool, args, progress,
        )
    return result


@app.get("/ping")
def ping():
    return {"status": "Healthy"}


@app.post("/invocations")
async def invoke(request: Request):
    print('{"event":"invocation_received"}', flush=True)
    config = settings()
    tokens = await asyncio.to_thread(TokenService, config)
    try:
        claims = tokens.verify(bearer(dict(request.headers)), "onchain-runtime")
        header_sid = request.headers.get("x-amzn-bedrock-agentcore-runtime-session-id")
        if (config.production or header_sid) and header_sid != claims["runtime_sid"]:
            raise Denied("SESSION_MISMATCH", "Runtime session does not belong to this invocation.")
        binding.check(claims)
        print('{"event":"identity_verified"}', flush=True)
        payload = await request.json()
        async def stream():
            queue = asyncio.Queue()
            async def progress(**event):
                await queue.put(event)
            async def produce():
                try:
                    result = await run(payload, claims, config, progress)
                    await queue.put({"kind": "RUN_RESULT", "result": result})
                except Exception as e:
                    await queue.put({"kind": "RUN_ERROR",
                                     "code": e.code if isinstance(e, Denied) else "RUNTIME_FAILED",
                                     "message": e.message if isinstance(e, Denied) else "运行失败，请稍后重试。"})
                finally:
                    await queue.put(None)
            task = asyncio.create_task(produce())
            try:
                while (event := await queue.get()) is not None:
                    yield "data: "+json.dumps(event, ensure_ascii=False)+"\n\n"
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        return StreamingResponse(stream(), media_type="text/event-stream")
    except Denied as e:
        return {"error": {"code": e.code, "message": e.message}}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
