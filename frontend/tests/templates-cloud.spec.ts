import { test, expect } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { cognitoLogin } from "./cognito";

test("keyless templates run all read plans through AgentCore and preserve demo disclosure and authorization", async ({ page, browser }) => {
  test.skip(!process.env.TEST_BASE_URL?.startsWith("https://"), "Requires deployed Cognito/AgentCore");
  test.setTimeout(600_000);
  const credentials = JSON.parse(fs.readFileSync(path.resolve("../.deployment/demo-credentials.json"), "utf8"));
  await cognitoLogin(page, credentials);
  const catalog = await (await page.request.get("/api/catalog")).json();
  expect(catalog.templates).toHaveLength(13);
  expect(catalog.skills).toHaveLength(8);
  expect(catalog.products).toHaveLength(8);
  expect(catalog.onchain_data_mode).toBe("demo");
  expect(catalog.templates.every((t: any) => t.availability === "READY")).toBe(true);
  const me = await (await page.request.get("/api/auth/me")).json();
  const headers = { "X-CSRF-Token": me.csrf };
  const before = await (await page.request.get("/api/dashboard")).json();
  const agents = new Map<string, any>(before.agents.filter((a: any) => a.status === "ACTIVE").map((a: any) => [a.skill_id, a]));
  await page.getByRole("button", { name: "Agent 管理", exact: true }).click();
  await page.getByRole("button", { name: "任务模板", exact: true }).click();
  await expect(page.getByTestId("template-token-research")).toContainText("可运行 · 演示数据");
  await page.screenshot({ path: "../.deployment/keyless-task-templates.png", fullPage: true });
  if (!agents.has("token-researcher")) {
    await page.getByTestId("template-token-research").getByRole("button", { name: "使用模板" }).click();
    await page.getByLabel("Agent 名称", { exact: true }).fill("免 Key 代币尽调");
    await expect(page.getByRole("checkbox")).toBeChecked();
    await page.getByRole("button", { name: "创建模板 Agent", exact: true }).click();
  } else {
    await page.getByRole("button", { name: "Agent 工作台", exact: true }).click();
    await page.getByLabel("选择 Agent").selectOption(agents.get("token-researcher").id);
    await page.getByRole("button", { name: "新会话", exact: true }).click();
    await page.getByRole("button", { name: "运行任务模板", exact: true }).click();
    await page.getByLabel("选择任务模板").selectOption("token-research");
  }
  await expect(page.getByRole("heading", { name: "运行任务模板", exact: true })).toBeVisible();
  await expect(page.getByLabel("代币合约地址", { exact: true })).toHaveValue(catalog.templates.find((t: any) => t.id === "token-research").examples["8453"].token_address);
  await page.getByRole("combobox", { name: "网络", exact: true }).selectOption("501");
  await expect(page.getByLabel("代币合约地址", { exact: true })).toHaveValue("So11111111111111111111111111111111111111112");
  await page.getByRole("combobox", { name: "网络", exact: true }).selectOption("8453");
  await page.getByRole("button", { name: "运行任务", exact: true }).click();
  const report = page.getByRole("region", { name: "任务结果" });
  await expect(report.getByText("scan_token_risk · 已完成", { exact: true })).toBeVisible({ timeout: 110_000 });
  await expect(report).toContainText("平台演示数据");
  await report.getByText("scan_token_risk · 已完成", { exact: true }).click();
  await expect(report).toContainText('"riskLevel": "UNKNOWN"');
  const timeline = page.getByRole("region", { name: "运行过程" });
  await expect(timeline.getByText("调用 scan_token_risk", { exact: true })).toBeVisible();
  await expect(timeline.getByText("Gateway 权限检查通过", { exact: true }).first()).toBeVisible();
  const agentId = await page.getByLabel("选择 Agent").inputValue();
  const sid = await page.evaluate(key => localStorage.getItem(key), `onchain:last-session:${me.user.sub}:${agentId}`);
  const session = await (await page.request.get("/api/conversations/" + sid)).json();
  expect(session.runs.at(-1).status).toBe("SUCCEEDED");
  const state = await (await page.request.get("/api/dashboard")).json();
  agents.set("token-researcher", state.agents.find((a: any) => a.id === agentId));
  const denied = await page.request.post("/api/security/probe", {
    headers, data: { agent_id: agentId, tool: "prepare_trade", arguments: { amount: "1", buy_asset: "ETH", chain: "8453", max_slippage_bps: 50 } },
  });
  expect((await denied.json()).decision).toBe("DENY");
  await page.screenshot({ path: "../.deployment/keyless-research-run.png", fullPage: true });
  await page.getByLabel("输入操作意图").fill("请用 search_tokens 查询 Base（8453）上的 ETH，展示演示数据。");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  const toolReport = page.getByRole("region", { name: "工具结果" });
  await expect(toolReport.getByRole("heading", { name: "search_tokens", exact: true })).toBeVisible({ timeout: 130_000 });
  await expect(toolReport).toContainText("非实时行情");
  const chat = await (await page.request.get("/api/conversations/" + sid)).json();
  expect(chat.messages.at(-1).content.model_id).toBe("us.openai.gpt-6-sol");
  console.info("PASS: real Bedrock intent → Gateway → MCP demo lookup; read-only Skill blocks trade");
  // Every remaining read template exercises real signed Runtime → Gateway → MCP calls.
  for (const task of catalog.templates.filter((t: any) => t.mode === "read" && t.id !== "token-research")) {
    let agent = agents.get(task.skill_id);
    if (!agent) {
      const created = await page.request.post("/api/agents", { headers, data: {
        name: "免 Key " + task.name, template_id: task.id, enable_template_products: true,
      } });
      expect(created.ok()).toBeTruthy();
      agent = await created.json(); agents.set(task.skill_id, agent);
    }
    for (const product of task.products) {
      expect((await page.request.post(`/api/catalog/${product}/subscription`, {
        headers, data: { enabled: true },
      })).ok()).toBeTruthy();
    }
    const conversation = await (await page.request.post(`/api/agents/${agent.id}/conversations`, { headers })).json();
    const parameters = task.examples?.["8453"] || Object.fromEntries(
      Object.entries(task.parameters.properties).map(([key, value]: [string, any]) => [key, value.default]),
    );
    const queued = await page.request.post(`/api/conversations/${conversation.id}/tasks`, { headers, data: {
      template_id: task.id, template_digest: task.digest, parameters,
    } });
    expect(queued.status(), task.id).toBe(202);
    let history: any;
    await expect.poll(async () => {
      history = await (await page.request.get(`/api/conversations/${conversation.id}`)).json();
      return history.runs.at(-1)?.status;
    }, { timeout: 100_000, intervals: [2000] }).toBe("SUCCEEDED");
    const output = history.messages.at(-1).content.tool_result;
    expect(output.steps.map((s: any) => s.tool)).toEqual(task.tools);
    if (task.data_mode === "demo") {
      expect(output.steps.every((s: any) => s.result.simulated && s.result.data_mode === "demo")).toBe(true);
    }
    console.info("PASS: keyless template " + task.id);
  }
  const otherContext = await browser.newContext();
  try {
    const stranger = await otherContext.newPage();
    const otherCredentials = JSON.parse(fs.readFileSync(path.resolve("../.deployment/market-credentials.json"), "utf8")).seller;
    await cognitoLogin(stranger, otherCredentials);
    expect((await stranger.request.get("/api/conversations/" + sid)).status()).toBe(404);
    const otherMe = await (await stranger.request.get("/api/auth/me")).json();
    expect((await stranger.request.post(`/api/conversations/${sid}/tasks`, {
      headers: { "X-CSRF-Token": otherMe.csrf },
      data: { template_id: "token-research", template_digest: "0".repeat(64), parameters: {} },
    })).status()).toBe(404);
  } finally { await otherContext.close(); }
  expect((await (await page.request.get("/api/dashboard")).json()).wallet).toEqual(before.wallet);
  await page.getByRole("button", { name: "MCP / Skill 管理", exact: true }).click();
  await expect(page.getByText("演示数据 · 无需 OKX Key", { exact: true })).toHaveCount(4);
  await page.screenshot({ path: "../.deployment/keyless-mcp-skills.png", fullPage: true });
});
