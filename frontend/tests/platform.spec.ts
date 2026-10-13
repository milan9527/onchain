import { test, expect } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const cloud = process.env.TEST_BASE_URL?.startsWith("https://");
const creds = cloud
  ? JSON.parse(fs.readFileSync(path.resolve("../.deployment/smoke-credentials.json"), "utf8"))
  : { alice: { email: "alice@example.test", password: "local-only-change-me" },
      bob: { email: "bob@example.test", password: "local-only-change-me" } };
// Keep acceptance trades separate from the Alice account shared with the platform owner.
const actor = creds.bob;
const observer = creds.alice;

test("isolated users, intent, explicit approval and controlled execution", async ({ page, browser }) => {
  test.skip(!!cloud, "Cloud authentication is covered by oauth-cloud.spec.ts; this test keeps local password fixtures.");
  test.setTimeout(360_000);
  await page.goto("/");
  await page.getByLabel("邮箱", { exact: true }).fill(actor.email);
  await page.getByLabel("密码", { exact: true }).fill(actor.password);
  await page.getByRole("button", { name: "验证并登录" }).click();
  await expect(page.getByRole("heading", { name: "Agent 管理", exact: true })).toBeVisible();
  await page.screenshot({ path: "../.deployment/overview.png", fullPage: true });

  const before = await (await page.request.get("/api/dashboard")).json();
  const reusable = before.agents.filter((a: any) => a.status === "ACTIVE" && a.name.startsWith("隔离验收 Agent")).at(-1);
  if (!reusable) {
    await page.getByRole("button", { name: "自定义创建", exact: true }).click();
    await page.getByLabel("名称", { exact: true }).fill("隔离验收 Agent "+Date.now());
    await page.getByRole("dialog").getByRole("button", { name: "创建 Agent", exact: true }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
  }
  await page.getByRole("button", { name: "Agent 工作台" }).click();
  if (reusable) await page.getByLabel("选择 Agent").selectOption(reusable.id);
  const agentId = await page.getByLabel("选择 Agent").inputValue();
  const previousHistory = await (await page.request.get(`/api/agents/${agentId}/conversations`)).json();
  const oldSessionIds = new Set(previousHistory.conversations.map((s: any) => s.id));
  await page.getByRole("button", { name: "新会话", exact: true }).click();
  await page.getByLabel("输入操作意图").fill("用 20 USDC 买入 ETH");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.getByRole("button", { name: "查看操作详情" })).toBeVisible({ timeout: 130_000 });
  await page.screenshot({ path: "../.deployment/workspace.png", fullPage: true });

  const messages = await page.request.get("/api/dashboard");
  const initial = await messages.json();
  const op = initial.operations.find((o: any) => o.status === "PENDING" && o.kind === "TRADE");
  expect(op).toBeTruthy();
  expect(op.conversation_id).toBeTruthy();
  const preparedSession = await (await page.request.get("/api/conversations/"+op.conversation_id)).json();
  const preparationRun = preparedSession.runs.at(-1);
  expect(preparationRun).toBeTruthy();
  const walletBefore = Number(initial.wallet.USDC);

  const b = await browser.newContext();
  const bob = await b.newPage();
  await bob.goto("/");
  await bob.getByLabel("邮箱", { exact: true }).fill(observer.email);
  await bob.getByLabel("密码", { exact: true }).fill(observer.password);
  await bob.getByRole("button", { name: "验证并登录" }).click();
  await expect(bob.getByRole("heading", { name: "Agent 管理", exact: true })).toBeVisible();
  const stolen = await bob.request.get("/api/operations/"+op.id);
  expect(stolen.status()).toBe(404);
  expect((await bob.request.get(`/api/agents/${op.agent_id}/conversations`)).status()).toBe(404);
  expect((await bob.request.get(`/api/conversations/${op.conversation_id}`)).status()).toBe(404);
  expect((await bob.request.get(`/api/conversations/${op.conversation_id}/runs/${preparationRun.id}`)).status()).toBe(404);
  const bdata = await (await bob.request.get("/api/dashboard")).json();
  expect(bdata.operations.find((o: any) => o.id === op.id)).toBeUndefined();
  await b.close();

  await page.getByRole("button", { name: "查看操作详情" }).click();
  await page.getByRole("checkbox").check();
  let executionRequests = 0;
  page.on("request", request => {
    if (request.url().endsWith(`/api/operations/${op.id}/execute`) && request.method() === "POST") executionRequests++;
  });
  await page.getByLabel("重新输入密码验证本人身份").fill("incorrect-test-password");
  await page.getByRole("button", { name: "验证并执行" }).click();
  await expect(page.getByRole("dialog").locator(".inline-error")).toBeVisible();
  expect(executionRequests).toBe(0);
  await page.getByLabel("重新输入密码验证本人身份").fill(actor.password);
  await page.getByRole("button", { name: "验证并执行" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  const card = page.getByTestId("operation-"+op.id);
  await expect(card.getByText("已完成", { exact: true })).toBeVisible({ timeout: 100_000 });
  await expect(card.getByText("操作已准备，等待你核对和授权。")).toHaveCount(0);
  await expect(card.getByRole("button")).toHaveCount(0);
  expect(executionRequests).toBe(1);
  const timeline = page.getByRole("region", { name: "运行过程" });
  await expect(timeline.getByText("调用 execute_trade", { exact: true })).toBeVisible();
  await expect(timeline.getByText("Gateway 权限检查通过", { exact: true })).toBeVisible();
  await expect(timeline.locator(".run-status")).toHaveText("已完成");
  const tracedSession = await (await page.request.get(`/api/conversations/${op.conversation_id}`)).json();
  expect(tracedSession.runs.length).toBe(2);
  const prepTrace = await (await page.request.get(`/api/conversations/${op.conversation_id}/runs/${preparationRun.id}`)).json();
  expect(prepTrace.events.map((e: any) => e.kind)).toEqual(expect.arrayContaining([
    "MODEL_STARTED", "INTENT_RECOGNIZED", "MCP_CONNECTED", "TOOL_CALL", "GATEWAY_ALLOWED", "TOOL_RESULT",
  ]));
  expect(JSON.stringify(prepTrace)).not.toMatch(/eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\./);
  const executionTrace = await (await page.request.get(`/api/conversations/${op.conversation_id}/runs/${tracedSession.runs.at(-1).id}`)).json();
  expect(executionTrace.events.map((e: any) => e.kind)).toEqual(expect.arrayContaining([
    "EXECUTOR_STARTED", "EXECUTOR_RESULT", "TOOL_RESULT", "RUN_FINISHED",
  ]));
  expect(tracedSession.auth_key).toBeUndefined();
  expect(tracedSession.runtime_session_id).toBeUndefined();
  await page.screenshot({ path: "../.deployment/workspace-completed.png", fullPage: true });
  await page.getByRole("button", { name: /授权与操作/ }).click();
  const row = page.locator("tr").filter({ hasText: op.id.slice(0, 15) });
  await expect(row.getByText("已完成")).toBeVisible({ timeout: 100_000 });
  const after = await (await page.request.get("/api/dashboard")).json();
  expect(Number(after.wallet.USDC)).toBe(walletBefore - 20);
  expect(after.operations.find((o: any) => o.id === op.id).execution.tx_hash).toBeNull();
  await page.screenshot({ path: "../.deployment/approvals.png", fullPage: true });

  await page.getByRole("button", { name: "安全中心" }).click();
  await page.locator(".security-grid").getByRole("combobox").first().selectOption(op.agent_id);
  await page.getByLabel("测试场景").selectOption("identity");
  await page.getByRole("button", { name: "运行验证" }).click();
  await expect(page.locator(".probe-result").getByText("INVALID_TOOL_ARGUMENTS")).toBeVisible();
  await page.screenshot({ path: "../.deployment/security.png", fullPage: true });

  await page.reload();
  await page.getByRole("button", { name: "Agent 工作台" }).click();
  await expect(page.getByTestId("operation-"+op.id).getByText("已完成", { exact: true })).toBeVisible();
  await expect(page.getByTestId("conversation-"+op.conversation_id)).toBeVisible();
  await expect(page.getByRole("region", { name: "运行过程" }).locator(".run-status")).toHaveText("已完成");
  await page.screenshot({ path: "../.deployment/history-trace.png", fullPage: true });

  await page.getByRole("button", { name: /退出登录/ }).click();
  await page.getByLabel("邮箱", { exact: true }).fill(actor.email);
  await page.getByLabel("密码", { exact: true }).fill(actor.password);
  await page.getByRole("button", { name: "验证并登录" }).click();
  await page.getByRole("button", { name: "Agent 工作台" }).click();
  await expect(page.getByText("历史会话只读，可查看消息和运行记录。")).toBeVisible();
  await page.getByRole("button", { name: "新会话", exact: true }).click();
  await page.getByRole("button", { name: "查看我的持仓", exact: true }).click();
  await expect(page.getByText("模拟资产", { exact: true })).toBeVisible({ timeout: 130_000 });
  const histories = await (await page.request.get(`/api/agents/${op.agent_id}/conversations`)).json();
  expect(histories.conversations.filter((s: any) => !oldSessionIds.has(s.id)).length).toBe(2);
  expect(histories.conversations[0].id).not.toBe(op.conversation_id);
  await page.getByTestId("conversation-"+op.conversation_id).click();
  await expect(page.getByTestId("operation-"+op.id).getByText("已完成", { exact: true })).toBeVisible();
});
