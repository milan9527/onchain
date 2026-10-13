import { test, expect } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { cognitoLogin, cognitoSignIn } from "./cognito";

test("paper order: Gateway preparation, Cognito confirmation, single settlement, history and cancellation", async ({ page, browser }) => {
  test.skip(!process.env.TEST_BASE_URL?.startsWith("https://"), "Requires deployed Cognito and AgentCore");
  test.setTimeout(360_000);
  const credentials = JSON.parse(fs.readFileSync(path.resolve("../.deployment/demo-credentials.json"), "utf8"));
  await cognitoLogin(page, credentials);
  const catalog = await (await page.request.get("/api/catalog")).json();
  const template = catalog.templates.find((t: any) => t.id === "eth-order");
  expect(template.mode).toBe("prepare");
  const initial = await (await page.request.get("/api/dashboard")).json();
  let agent = initial.agents.find((a: any) => a.name === "下单交易验收 Agent");
  if (!agent) {
    const me = await (await page.request.get("/api/auth/me")).json();
    const response = await page.request.post("/api/agents", { headers: { "X-CSRF-Token": me.csrf },
      data: { name: "下单交易验收 Agent", template_id: "eth-order", enable_template_products: true } });
    expect(response.ok()).toBeTruthy(); agent = await response.json(); await page.reload();
  }
  await page.getByRole("button", { name: "Agent 工作台", exact: true }).click();
  await page.getByLabel("选择 Agent").selectOption(agent.id);
  await page.getByRole("button", { name: "交易下单", exact: true }).click();
  const agentId = agent.id;
  const before = await (await page.request.get("/api/dashboard")).json();
  let me = await (await page.request.get("/api/auth/me")).json();
  await page.getByLabel("买入金额（USDC）").fill("20");
  await page.screenshot({ path: "../.deployment/order-desk.png", fullPage: true });
  await page.getByRole("button", { name: "生成待确认订单", exact: true }).click();
  await expect(page.getByRole("button", { name: "查看操作详情", exact: true })).toBeVisible({ timeout: 110_000 });
  const sid = await page.evaluate(key => localStorage.getItem(key), `onchain:last-session:${me.user.sub}:${agentId}`);
  const prepared = await (await page.request.get("/api/dashboard")).json();
  const op = prepared.operations.find((o: any) => o.conversation_id === sid && o.status === "PENDING");
  expect(op).toBeTruthy();
  expect(op.intent.reference_price).toBe("3000");
  expect(Number(op.intent.minimum_received)).toBeGreaterThan(0);
  expect(prepared.wallet).toEqual(before.wallet);
  const session = await (await page.request.get("/api/conversations/" + sid)).json();
  const plan = session.messages.at(-1).content.tool_result;
  expect(plan.steps.map((s: any) => s.tool)).toEqual(["get_portfolio", "get_quote", "prepare_trade"]);
  expect(plan.operation.id).toBe(op.id);
  const unapproved = await page.request.post(`/api/operations/${op.id}/execute`, { headers: { "X-CSRF-Token": me.csrf } });
  expect(unapproved.status()).toBe(409);
  expect((await unapproved.json()).error.code).toBe("APPROVAL_REQUIRED");
  const otherContext = await browser.newContext();
  try {
    const stranger = await otherContext.newPage();
    const seller = JSON.parse(fs.readFileSync(path.resolve("../.deployment/market-credentials.json"), "utf8")).seller;
    await cognitoLogin(stranger, seller);
    const otherMe = await (await stranger.request.get("/api/auth/me")).json();
    expect((await stranger.request.get(`/api/operations/${op.id}`)).status()).toBe(404);
    expect((await stranger.request.get(`/api/conversations/${sid}`)).status()).toBe(404);
    expect((await stranger.request.post(`/api/operations/${op.id}/reject`, {
      headers: { "X-CSRF-Token": otherMe.csrf },
    })).status()).toBe(404);
  } finally { await otherContext.close(); }
  await page.getByRole("button", { name: "查看操作详情", exact: true }).click();
  await expect(page.getByRole("dialog")).toContainText("订单参考价");
  await expect(page.getByRole("dialog")).toContainText("最少接收");
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "验证并执行", exact: true }).click();
  await cognitoSignIn(page, credentials);
  await expect(page.getByRole("heading", { name: "Agent 工作台", exact: true })).toBeVisible();
  await expect(page.getByLabel("选择 Agent")).toHaveValue(agentId);
  await expect(page.getByTestId("conversation-" + sid)).toHaveClass(/selected/);
  await expect(page.getByTestId("operation-" + op.id).getByText("已完成", { exact: true })).toBeVisible({ timeout: 100_000 });
  const after = await (await page.request.get("/api/dashboard")).json();
  const fill = after.operations.find((o: any) => o.id === op.id);
  expect(Number(after.wallet.USDC)).toBe(Number(before.wallet.USDC) - 20);
  expect(Number(after.wallet.ETH)).toBeCloseTo(Number(before.wallet.ETH) + Number(fill.execution.received), 12);
  expect(fill.execution.tx_hash).toBeNull();
  expect(fill.execution.receipt).toBe("paper_" + op.id);
  await page.screenshot({ path: "../.deployment/order-confirmed-session.png", fullPage: true });
  me = await (await page.request.get("/api/auth/me")).json();
  const replay = await page.request.post(`/api/operations/${op.id}/execute`, { headers: { "X-CSRF-Token": me.csrf } });
  expect(replay.status()).toBe(202);
  const replayRun = await replay.json();
  await expect.poll(async () => {
    const history = await (await page.request.get(`/api/conversations/${replayRun.conversation_id}`)).json();
    return history.runs.find((r: any) => r.id === replayRun.run_id)?.status;
  }, { timeout: 90_000 }).toBe("SUCCEEDED");
  expect((await (await page.request.get("/api/dashboard")).json()).wallet).toEqual(after.wallet);
  await page.getByRole("button", { name: "交易下单", exact: true }).click();
  await expect(page.getByTestId("order-" + op.id)).toContainText("已成交");
  await expect(page.getByTestId("order-" + op.id)).toContainText(fill.execution.receipt);
  await page.screenshot({ path: "../.deployment/order-history.png", fullPage: true });
  await page.reload();
  await expect(page.getByRole("heading", { name: "Agent 工作台", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "交易下单", exact: true }).click();
  await expect(page.getByTestId("order-" + op.id)).toContainText("已成交");
  // A second fresh order can be withdrawn without affecting the first fill.
  await page.getByLabel("买入金额（USDC）").fill("5");
  await page.getByRole("button", { name: "生成待确认订单", exact: true }).click();
  await expect(page.getByRole("button", { name: "查看操作详情", exact: true })).toBeVisible({ timeout: 110_000 });
  const nextSid = await page.evaluate(key => localStorage.getItem(key), `onchain:last-session:${me.user.sub}:${agentId}`);
  expect(nextSid).not.toBe(sid);
  const next = await (await page.request.get("/api/dashboard")).json();
  const cancellation = next.operations.find((o: any) => o.conversation_id === nextSid && o.status === "PENDING");
  expect(cancellation).toBeTruthy();
  await page.getByRole("button", { name: "交易下单", exact: true }).click();
  await page.getByTestId("order-" + cancellation.id).getByRole("button", { name: "撤单", exact: true }).click();
  await expect(page.getByTestId("order-" + cancellation.id)).toContainText("已撤单");
  const cancelledExecution = await page.request.post(`/api/operations/${cancellation.id}/execute`, { headers: { "X-CSRF-Token": me.csrf } });
  expect(cancelledExecution.status()).toBe(409);
  const overLimit = await page.request.post(`/api/conversations/${nextSid}/tasks`, {
    headers: { "X-CSRF-Token": me.csrf }, data: {
      template_id: template.id, template_digest: template.digest,
      parameters: { amount: "101", max_slippage_bps: 50 },
    },
  });
  expect(overLimit.status()).toBe(403);
  expect((await overLimit.json()).error.code).toBe("TRADE_LIMIT");
  expect((await (await page.request.get("/api/dashboard")).json()).wallet).toEqual(after.wallet);
  console.info("PASS: prepared order, Cognito return, single fill, cross-user isolation, cancel and budget rejection");
});
