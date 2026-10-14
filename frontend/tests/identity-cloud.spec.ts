import { test, expect } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { cognitoLogin, cognitoSignIn } from "./cognito";

test("Identity Vault consent resumes a real task; revocation blocks cached credentials; reconnect recovers", async ({ page }) => {
  test.skip(!process.env.TEST_BASE_URL?.startsWith("https://"), "Requires deployed Identity and AgentCore");
  test.setTimeout(480_000);
  const credentials = JSON.parse(fs.readFileSync(path.resolve("../.deployment/identity-credentials.json"), "utf8"));
  await cognitoLogin(page, credentials);
  const me = await (await page.request.get("/api/auth/me")).json();
  const headers = { "X-CSRF-Token": me.csrf };
  const before = await (await page.request.get("/api/dashboard")).json();
  let agent = before.agents.find((a: any) => a.skill_id === "token-researcher" && a.status === "ACTIVE");
  if (!agent) {
    const created = await page.request.post("/api/agents", { headers, data: {
      name: "Identity 授权验收", template_id: "token-research", enable_template_products: true,
    } });
    expect(created.status()).toBe(200);
    agent = await created.json();
    await page.reload();
  }
  const catalog = await (await page.request.get("/api/catalog")).json();
  const template = catalog.templates.find((t: any) => t.id === "token-research");
  const connections = await (await page.request.get("/api/identity/connections")).json();
  expect(connections.enabled).toBe(true);
  async function connect(source: string) {
    const response = await page.request.post("/api/identity/connections/" + source, { headers });
    expect(response.ok()).toBeTruthy();
    const body = await response.json();
    if (!body.authorization_url) return;
    const completion = page.waitForResponse(r => r.url().endsWith("/api/identity/complete"), { timeout: 45_000 });
    await page.goto(body.authorization_url);
    await cognitoSignIn(page, credentials);
    const finished = await completion;
    expect(finished.status(), JSON.stringify(await finished.json())).toBe(200);
    await expect(page.getByText("工具账户连接已生效。", { exact: true })).toBeVisible({ timeout: 45_000 });
    await expect(page).toHaveURL("/");
    const after = await (await page.request.get("/api/identity/connections")).json();
    expect(after.connections[source]).toMatchObject({ active: true, connected: true });
  }
  if (connections.connections.internal?.active === false) await connect("internal");
  async function task() {
    const conv = await (await page.request.post(`/api/agents/${agent.id}/conversations`, { headers })).json();
    const response = await page.request.post(`/api/conversations/${conv.id}/tasks`, { headers, data: {
      template_id: template.id, template_digest: template.digest, parameters: template.examples["8453"],
    } });
    expect(response.status()).toBe(202);
    await expect.poll(async () => (await (await page.request.get("/api/conversations/" + conv.id)).json()).runs?.at(-1)?.status,
      { timeout: 130_000, intervals: [1500] }).toMatch(/SUCCEEDED|BLOCKED|FAILED/);
    return (await (await page.request.get("/api/conversations/" + conv.id)).json());
  }
  let conv = await task();
  await page.getByRole("button", { name: "Agent 工作台", exact: true }).click();
  await page.getByRole("tab", { name: "我的 Agents", exact: true }).click();
  await page.getByTestId("agent-" + agent.id).getByRole("button", { name: "运行 Agent" }).click();
  await page.getByTestId("conversation-" + conv.id).click();
  await expect(page.getByTestId("conversation-" + conv.id)).toHaveClass(/selected/);
  const firstError = conv.messages.at(-1)?.content?.tool_result?.error;
  if (firstError?.code === "MCP_AUTH_REQUIRED") {
    expect(firstError.resume_id).toBeTruthy();
    expect(firstError.authorization_url).toBeUndefined();
    const completion = page.waitForResponse(r => r.url().endsWith("/api/identity/complete"), { timeout: 45_000 });
    await page.getByRole("button", { name: "授权并继续任务" }).click();
    await cognitoSignIn(page, credentials);
    const finished = await completion;
    expect(finished.status(), JSON.stringify(await finished.json())).toBe(200);
    await expect(page.getByLabel("选择 Agent")).toHaveValue(agent.id);
    await expect.poll(async () => (await (await page.request.get("/api/conversations/" + conv.id)).json()).runs?.at(-1)?.status,
      { timeout: 140_000, intervals: [1500] }).toBe("SUCCEEDED");
    conv = await (await page.request.get("/api/conversations/" + conv.id)).json();
    expect(conv.runs.length).toBe(2);
  }
  expect(conv.runs.at(-1).status, JSON.stringify(firstError || {})).toBe("SUCCEEDED");
  expect(conv.messages.at(-1).content.tool_result.steps).toHaveLength(4);
  const journal = await (await page.request.get(`/api/conversations/${conv.id}/runs/${conv.runs.at(-1).id}`)).json();
  expect(journal.events.some((e: any) => e.kind === "EXECUTOR_RESULT")).toBeTruthy();
  expect(JSON.stringify(conv)).not.toMatch(/authorization_url|context_id|access_token|refresh_token/);
  expect((await (await page.request.get("/api/dashboard")).json()).wallet).toEqual(before.wallet);
  await page.screenshot({ path: "../.deployment/identity-resumed-task.png", fullPage: true });
  const revoked = await page.request.delete("/api/identity/connections/internal", { headers });
  expect(revoked.ok()).toBeTruthy();
  const blocked = await task();
  expect(blocked.runs.at(-1).status).toBe("BLOCKED");
  expect(blocked.messages.at(-1).content.tool_result.error.code).toBe("IDENTITY_CONNECTION_REVOKED");
  await connect("internal");
  const restored = await task();
  expect(restored.runs.at(-1).status).toBe("SUCCEEDED");
  // Different inbound issuers have separate Vault user contexts.
  await connect("external");
  await page.screenshot({ path: "../.deployment/identity-connections.png", fullPage: true });
  expect((await (await page.request.get("/api/dashboard")).json()).wallet).toEqual(before.wallet);
});
