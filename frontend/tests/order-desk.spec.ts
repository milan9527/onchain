import { test, expect } from "@playwright/test";

test("order desk uses an execution-capable Agent, prepares an immutable task and allows cancellation without executing", async ({ page }) => {
  const task = { id: "eth-order", name: "ETH 买入下单", mode: "prepare", digest: "a".repeat(64),
    tools: ["get_portfolio", "get_quote", "prepare_trade"], products: ["market", "portfolio", "trade"] };
  const agents = [
    { id: "research", name: "只读研究", skill_id: "research", status: "ACTIVE" },
    { id: "trader", name: "交易助手", skill_id: "controlled-trader", status: "ACTIVE", max_trade_units: 100_000_000, daily_limit_units: 500_000_000 },
  ];
  const op: any = { id: "order-fixture", kind: "TRADE", status: "PENDING", agent_id: "trader",
    conversation_id: "order-session", created_at: Date.now() / 1000, expires_at: Date.now() / 1000 + 900,
    intent_hash: "b".repeat(64), intent: { amount_units: 20_000_000, reference_price: "3000",
      minimum_received: "0.006633333333333333", max_slippage_bps: 50 } };
  let submitted: any, executions = 0, cancelled = false;
  await page.route("**/api/**", async route => {
    const path = new URL(route.request().url()).pathname, method = route.request().method();
    let body: any = {};
    if (path === "/api/health") body = { auth: "local" };
    else if (path === "/api/auth/me") body = { user: { sub: "order-user", email: "order@example.test" }, csrf: "fixture" };
    else if (path === "/api/catalog") body = { templates: [task], products: [], onchain_data_mode: "demo",
      skills: [{ id: "research", tools: ["get_portfolio"] }, { id: "controlled-trader", tools: [...task.tools, "execute_trade"] }] };
    else if (path === "/api/dashboard") body = { agents, operations: submitted ? [op] : [], grants: [], events: [],
      subscriptions: task.products, wallet: { USDC: "10000", ETH: "0" }, mode: "paper" };
    else if (/\/api\/agents\/.*\/conversations$/.test(path)) body = method === "POST"
      ? { id: "order-session", agent_id: "trader" } : { conversations: [] };
    else if (path === "/api/conversations/order-session/tasks") { submitted = route.request().postDataJSON(); body = { status: "QUEUED" }; }
    else if (path === "/api/conversations/order-session") body = { id: "order-session", agent_id: "trader",
      can_resume: true, busy_until: 0, runs: [], messages: [{ role: "assistant", content: {
        action: "task", tool_result: { task_id: task.id, task_name: task.name, operation: op,
          steps: [{ tool: "prepare_trade", status: "OK", result: { status: "approval_required" } }] },
      } }] };
    else if (path === "/api/operations/order-fixture/reject") { op.status = "REJECTED"; cancelled = true; body = op; }
    else if (path.endsWith("/execute")) executions++;
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Agent 工作台", exact: true }).click();
  await expect(page.getByRole("button", { name: "交易下单", exact: true })).toHaveCount(0);
  await page.getByLabel("选择 Agent").selectOption("trader");
  await page.getByRole("button", { name: "交易下单", exact: true }).click();
  await expect(page.getByRole("region", { name: "买入订单" })).toContainText("交易助手");
  await expect(page.getByRole("combobox", { name: "下单 Agent" })).toHaveCount(0);
  await expect(page.getByLabel("买入金额（USDC）")).toHaveValue("20");
  await page.getByRole("button", { name: "生成待确认订单", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Agent 工作台", exact: true })).toBeVisible();
  await expect(page.getByTestId("operation-order-fixture")).toContainText("待确认");
  await expect(page.getByTestId("operation-order-fixture")).toContainText("3,000");
  expect(submitted).toEqual({ template_id: task.id, template_digest: task.digest, parameters: { amount: "20", max_slippage_bps: 50 } });
  expect(executions).toBe(0);
  await page.getByRole("button", { name: "交易下单", exact: true }).click();
  await page.getByRole("button", { name: "刷新", exact: true }).click();
  await page.getByTestId("order-order-fixture").getByRole("button", { name: "撤单", exact: true }).click();
  await expect(page.getByTestId("order-order-fixture")).toContainText("已撤单");
  expect(cancelled).toBe(true);
  expect(executions).toBe(0);
  await page.getByRole("combobox", { name: "筛选订单状态" }).selectOption("CONFIRMED");
  await expect(page.getByTestId("order-order-fixture")).toHaveCount(0);
});

test("order templates create a fresh conversation instead of reusing an existing Runtime session", async ({ page }) => {
  const agent = { id: "trader", name: "交易助手", skill_id: "controlled-trader", template_id: "eth-order",
    status: "ACTIVE", max_trade_units: 100_000_000, daily_limit_units: 500_000_000 };
  const task = { id: "eth-order", name: "ETH 买入下单", mode: "prepare", digest: "a".repeat(64),
    tools: ["get_portfolio", "get_quote", "prepare_trade"], products: [], availability: "READY",
    parameters: { required: ["amount", "max_slippage_bps"], properties: {
      amount: { type: "string", default: "20" }, max_slippage_bps: { type: "integer", default: 50 },
    } } };
  let submittedTo = "", created = 0;
  await page.route("**/api/**", async route => {
    const path = new URL(route.request().url()).pathname;
    let body: any = {};
    if (path === "/api/health") body = { auth: "local" };
    else if (path === "/api/auth/me") body = { user: { sub: "fresh-order", email: "orders@example.test" }, csrf: "fixture" };
    else if (path === "/api/catalog") body = { templates: [task], products: [], skills: [{ id: agent.skill_id, tools: [...task.tools, "execute_trade"] }] };
    else if (path === "/api/dashboard") body = { agents: [agent], operations: [], grants: [], events: [],
      subscriptions: [], wallet: { USDC: "10000", ETH: "0" } };
    else if (path === "/api/agents/trader/conversations") {
      if (route.request().method() === "POST") { created++; body = { id: "fresh-session", agent_id: agent.id }; }
      else body = { conversations: [{ id: "old-session", agent_id: agent.id, title: "原来的会话", updated_at: 100, message_count: 1 }] };
    } else if (path.endsWith("/tasks")) { submittedTo = path; body = { status: "QUEUED" }; }
    else if (path.startsWith("/api/conversations/")) body = { id: path.split("/")[3], agent_id: agent.id,
      can_resume: true, busy_until: 0, runs: [], messages: [{ role: "user", content: "原来的会话内容" }] };
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Agent 工作台", exact: true }).click();
  await expect(page.getByText("原来的会话内容", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "运行任务模板", exact: true }).click();
  await page.getByRole("button", { name: "生成待确认订单", exact: true }).click();
  await expect.poll(() => submittedTo).toBe("/api/conversations/fresh-session/tasks");
  expect(created).toBe(1);
});
