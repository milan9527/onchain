import { test, expect, type Browser } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import { execFileSync } from "node:child_process";
import { cognitoSignIn, cognitoLogin as login, connectToolAccount } from "./cognito";

const cloud = process.env.TEST_BASE_URL?.startsWith("https://");
test.skip(!cloud, "Requires the deployed Cognito and AgentCore environment");

async function externalToken(browser: Browser, info: any, credentials: any, scopes: string[]) {
  const context = await browser.newContext();
  const page = await context.newPage();
  const verifier = crypto.randomBytes(48).toString("base64url");
  const state = crypto.randomBytes(32).toString("base64url");
  let code = "";
  await page.route("http://localhost:8765/callback**", async route => {
    const url = new URL(route.request().url());
    expect(url.searchParams.get("state")).toBe(state);
    code = url.searchParams.get("code") || "";
    await route.fulfill({ contentType: "text/plain", body: "OAuth callback received" });
  });
  const params = new URLSearchParams({
    response_type: "code", client_id: info.client_id, redirect_uri: info.redirect_uri,
    scope: ["openid", ...scopes].join(" "), resource: info.gateway_url, state, prompt: "login",
    code_challenge_method: "S256", code_challenge: crypto.createHash("sha256").update(verifier).digest("base64url"),
  });
  await page.goto(info.authorization_endpoint + "?" + params);
  await cognitoSignIn(page, credentials);
  await expect.poll(() => code).not.toBe("");
  // The same code is not consumed by a wrong verifier.
  const wrong = await fetch(info.token_endpoint, { signal: AbortSignal.timeout(15_000), method: "POST", body: new URLSearchParams({
    grant_type: "authorization_code", client_id: info.client_id, redirect_uri: info.redirect_uri,
    code, code_verifier: crypto.randomBytes(48).toString("base64url"),
  }) });
  expect(wrong.status).toBe(400);
  // Cognito can consume a rejected authorization code: obtain a fresh code for the correct exchange.
  code = "";
  await page.goto(info.authorization_endpoint + "?" + params);
  await cognitoSignIn(page, credentials);
  await expect.poll(() => code).not.toBe("");
  const response = await fetch(info.token_endpoint, { signal: AbortSignal.timeout(15_000), method: "POST", body: new URLSearchParams({
    grant_type: "authorization_code", client_id: info.client_id, redirect_uri: info.redirect_uri,
    code, code_verifier: verifier,
  }) });
  expect(response.status).toBe(200);
  const token = await response.json();
  const refreshed = await fetch(info.token_endpoint, {
    method: "POST", signal: AbortSignal.timeout(15_000), body: new URLSearchParams({
      grant_type: "refresh_token", client_id: info.client_id, refresh_token: token.refresh_token,
    }),
  });
  expect(refreshed.status).toBe(200);
  const renewed = await refreshed.json();
  const renewedClaims = JSON.parse(Buffer.from(renewed.access_token.split(".")[1], "base64url").toString());
  expect(renewedClaims.aud).toBe(info.gateway_url);
  token.access_token = renewed.access_token;
  await context.close();
  return token;
}

function rpcBody(text: string) {
  if (text.trim().startsWith("{")) return JSON.parse(text);
  const data = text.split("\n").filter(line => line.startsWith("data: ")).at(-1);
  if (!data) throw new Error("No MCP response");
  return JSON.parse(data.slice(6));
}

async function rpc(url: string, token: string, method: string, params: any = {}, sid?: string, id = 1) {
  const response = await fetch(url, {
    signal: AbortSignal.timeout(35_000),
    method: "POST", headers: {
      Authorization: "Bearer " + token, "Content-Type": "application/json",
      Accept: "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25",
      ...(sid ? { "Mcp-Session-Id": sid } : {}),
    }, body: JSON.stringify({ jsonrpc: "2.0", id, method, params }),
  });
  if (method === "initialize" && response.headers.get("mcp-session-id")) {
    const notified = await fetch(url, {
      method: "POST", signal: AbortSignal.timeout(15_000), headers: {
        Authorization: "Bearer " + token, "Content-Type": "application/json",
        Accept: "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25",
        "Mcp-Session-Id": response.headers.get("mcp-session-id")!,
      }, body: JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized" }),
    });
    expect(notified.status).toBe(202);
  }
  return { response, body: rpcBody(await response.text()), sid: response.headers.get("mcp-session-id") || sid };
}

test("Cognito OAuth: login, scope enforcement, operation continuation and external session revocation", async ({ page, browser }) => {
  test.setTimeout(360_000);
  const credentials = JSON.parse(fs.readFileSync(path.resolve("../.deployment/smoke-credentials.json"), "utf8"));
  const actor = credentials.bob;
  await login(page, actor);
  console.info("PASS: real Cognito OAuth login");
  let me = await (await page.request.get("/api/auth/me")).json();
  const sessionCookie = (await page.context().cookies()).find(c => c.name === "onchain_session")!;
  execFileSync("../.venv/bin/python", ["../scripts/expire_test_oauth_access.py"], {
    input: JSON.stringify({ cookie: sessionCookie.value }), stdio: ["pipe", "pipe", "pipe"],
  });
  const refreshedMe = await page.request.get("/api/auth/me");
  expect(refreshedMe.status()).toBe(200);
  expect((await refreshedMe.json()).user.sub).toBe(me.user.sub);
  console.info("PASS: BFF refresh with the real KMS-encrypted Cognito credential");
  await connectToolAccount(page, actor, "internal");
  await connectToolAccount(page, actor, "external");
  let before = await (await page.request.get("/api/dashboard")).json();
  let agent = before.agents.filter((a: any) => a.status === "ACTIVE" && a.skill_id === "controlled-trader").at(-1);
  if (!agent) {
    const created = await page.request.post("/api/agents", {
      headers: { "X-CSRF-Token": me.csrf }, data: { name: "OAuth acceptance", max_trade: "100", daily_limit: "1000" },
    });
    expect(created.status()).toBe(200); agent = await created.json(); await page.reload();
  }
  expect((await page.request.post("/api/auth/login", { data: actor })).status()).toBe(401);
  await page.getByRole("button", { name: "MCP / Skill 管理" }).click();
  await page.getByLabel("MCP 绑定 Agent").selectOption(agent.id);
  await page.getByLabel("允许准备交易").check();
  await page.getByLabel("允许执行我已明确批准的交易").check();
  await page.getByRole("button", { name: /验证并.*连接/ }).click();
  await cognitoSignIn(page, actor);
  await expect(page.getByRole("button", { name: "撤销连接" })).toBeVisible();
  const info = await (await page.request.get("/api/mcp/connections")).json();
  const challenge = await fetch(info.gateway_url, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", id: 0, method: "tools/list" }) });
  expect(challenge.status).toBe(401);
  expect(challenge.headers.get("www-authenticate")).toContain("resource_metadata");
  const metadataUrl = challenge.headers.get("www-authenticate")?.match(/resource_metadata="([^"]+)"/)?.[1];
  expect(metadataUrl).toBeTruthy();
  const metadata = await (await fetch(metadataUrl!)).json();
  expect(metadata.authorization_servers).toContain(info.issuer);
  console.info("PASS: connection consent and Gateway OAuth discovery");
  const access = await externalToken(browser, info, actor, info.scopes);
  const claims = JSON.parse(Buffer.from(access.access_token.split(".")[1], "base64url").toString());
  expect(claims.aud).toBe(info.gateway_url);
  expect(claims.token_use).toBe("access");
  console.info("PASS: external authorization code and PKCE exchange");
  const initialized = await rpc(info.gateway_url, access.access_token, "initialize", {
    protocolVersion: "2025-11-25", capabilities: {}, clientInfo: { name: "oauth-acceptance", version: "1.0" },
  });
  expect(initialized.response.status).toBe(200); expect(initialized.sid).toBeTruthy();
  const sid = initialized.sid!;
  const tools = await rpc(info.gateway_url, access.access_token, "tools/list", {}, sid, 2);
  expect(tools.body.error).toBeUndefined();
  expect(tools.body.result.tools).toHaveLength(6);
  expect(JSON.stringify(tools.body.result.tools)).not.toMatch(/security_context|context_id/);
  console.info("PASS: authenticated MCP discovery");
  let prepared = await rpc(info.gateway_url, access.access_token, "tools/call", {
    name: "onchain___prepare_trade", arguments: { amount: "20", buy_asset: "ETH", chain: "8453", max_slippage_bps: 50 },
  }, sid, 3);
  if (prepared.body.error?.code === -32042) {
    const authorization = prepared.body.error.data.elicitations[0].url;
    const completion = page.waitForResponse(r => r.url().endsWith("/api/identity/complete"));
    await page.goto(authorization);
    await cognitoSignIn(page, actor);
    const finished = await completion;
    expect(finished.status(), JSON.stringify(await finished.json())).toBe(200);
    await expect(page.getByText("工具账户连接已生效。", { exact: true })).toBeVisible();
    prepared = await rpc(info.gateway_url, access.access_token, "tools/call", {
      name: "onchain___prepare_trade", arguments: { amount: "20", buy_asset: "ETH", chain: "8453", max_slippage_bps: 50 },
    }, sid, 3);
    console.info("PASS: external Gateway native consent completed with its original user JWT");
  }
  expect(prepared.body.error).toBeUndefined();
  const result = JSON.parse(prepared.body.result.content[0].text);
  expect(result.status).toBe("approval_required");
  const op = result.operation;
  const idTokenAttempt = await rpc(info.gateway_url, access.id_token, "tools/list");
  expect([401, 403]).toContain(idTokenAttempt.response.status);
  expect(idTokenAttempt.body.error).toBeTruthy();
  expect(idTokenAttempt.body.result).toBeUndefined();
  const denied = await rpc(info.gateway_url, access.access_token, "tools/call", {
    name: "onchain___execute_trade", arguments: { operation_id: op.id },
  }, sid, 4);
  expect(denied.body.error.data.code).toBe("APPROVAL_REQUIRED");
  console.info("PASS: external prepare; unapproved execute blocked");
  const other = await browser.newContext();
  const otherPage = await other.newPage();
  await login(otherPage, JSON.parse(fs.readFileSync(path.resolve("../.deployment/demo-credentials.json"), "utf8")));
  expect((await otherPage.request.get("/api/operations/" + op.id)).status()).toBe(404);
  expect((await otherPage.request.get("/api/conversations/" + op.conversation_id)).status()).toBe(404);
  await other.close();

  expect((await page.request.post("/api/auth/logout", { headers: { "X-CSRF-Token": me.csrf } })).status()).toBe(200);
  await page.goto(result.approval_url);
  await page.getByRole("button", { name: "通过 Cognito 登录" }).click();
  await cognitoSignIn(page, actor);
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByRole("dialog").locator('input[type="password"]')).toHaveCount(0);
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "验证并执行", exact: true }).click();
  await cognitoSignIn(page, { ...actor, password: "incorrect-test-password" });
  await expect(page.locator('input[name="password"]')).toBeVisible();
  expect((await (await page.request.get("/api/operations/" + op.id)).json()).status).toBe("PENDING");
  await page.locator('input[name="password"]').fill(actor.password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Agent 工作台", exact: true })).toBeVisible();
  await expect.poll(async () => (await (await page.request.get("/api/operations/" + op.id)).json()).status,
    { timeout: 100_000 }).toBe("CONFIRMED");
  console.info("PASS: Cognito reauthentication resumes exact approved trade");
  const after = await (await page.request.get("/api/dashboard")).json();
  expect(Number(after.wallet.USDC)).toBe(Number(before.wallet.USDC) - 20);
  me = await (await page.request.get("/api/auth/me")).json();
  expect((await page.request.post(`/api/operations/${op.id}/approve`, {
    headers: { "X-CSRF-Token": me.csrf }, data: { intent_hash: op.intent_hash },
  })).status()).toBe(401);
  const narrow = await externalToken(browser, info, actor,
    [info.gateway_url + "/mcp.invoke", info.gateway_url + "/portfolio.read"]);
  const narrowInit = await rpc(info.gateway_url, narrow.access_token, "initialize", {
    protocolVersion: "2025-11-25", capabilities: {}, clientInfo: { name: "read-only", version: "1" },
  });
  const narrowTools = await rpc(info.gateway_url, narrow.access_token, "tools/list", {}, narrowInit.sid!, 2);
  expect(narrowTools.body.result.tools).toHaveLength(4);
  const scopeDenied = await rpc(info.gateway_url, narrow.access_token, "tools/call", {
    name: "onchain___execute_trade", arguments: { operation_id: op.id },
  }, narrowInit.sid!, 3);
  expect(scopeDenied.body.error.data.code).toBe("INSUFFICIENT_SCOPE");
  const crossed = await rpc(info.gateway_url, narrow.access_token, "tools/list", {}, sid, 8);
  expect(crossed.body.error.data.code).toBe("GATEWAY_SESSION_MISMATCH");
  const history = await (await page.request.get("/api/conversations/" + op.conversation_id)).json();
  expect(history.kind).toBe("external"); expect(history.can_resume).toBe(false);
  expect(history.runs.length).toBeGreaterThan(0);
  await page.getByRole("button", { name: "Agent 工作台" }).click();
  await page.getByLabel("选择 Agent").selectOption(agent.id);
  await page.getByRole("button", { name: "新会话", exact: true }).click();
  await page.getByLabel("输入操作意图").fill("查看我的持仓");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.locator(".tool-result").getByText("模拟资产", { exact: true })).toBeVisible({ timeout: 130_000 });
  const timeline = page.getByRole("region", { name: "运行过程" });
  await expect(timeline.getByText("Gateway 权限检查通过", { exact: true })).toBeVisible();
  console.info("PASS: OAuth user delegation through real Bedrock, Agent Runtime, internal Gateway and MCP");
  expect((await page.request.delete("/api/mcp/connections/" + info.client_id, {
    headers: { "X-CSRF-Token": me.csrf },
  })).status()).toBe(200);
  const revoked = await rpc(info.gateway_url, access.access_token, "tools/call", {
    name: "onchain___get_portfolio", arguments: {},
  }, sid, 9);
  expect(revoked.body.error.data.code).toBe("MCP_CONNECTION_REQUIRED");
  await page.getByRole("button", { name: "MCP / Skill 管理" }).click();
  await expect(page.getByLabel("MCP 绑定 Agent")).toBeVisible();
  await page.screenshot({ path: "../.deployment/oauth-mcp.png", fullPage: true });
});
