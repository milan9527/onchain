import { test, expect } from "@playwright/test";

test("market filters trade direction and seller creates an Agent from the publish dialog", async ({ page }) => {
  const tools = ["get_portfolio", "get_quote", "prepare_trade", "execute_trade"];
  const tasks = ["BUY", "SELL"].map(side => ({ id: side === "BUY" ? "eth-buy" : "eth-sell",
    name: side === "BUY" ? "ETH 买入 Agent" : "ETH 卖出 Agent", trade_side: side,
    skill_id: side === "BUY" ? "eth-buyer" : "eth-seller", mode: "prepare",
    description: "在用户自己的账户执行已确认的模拟订单。", tools, products: ["market", "portfolio", "trade"] }));
  const skills = tasks.map(t => ({ id: t.skill_id, tools, trade_sides: [t.trade_side] }));
  let agents: any[] = [], created: any, published: any;
  const listings = tasks.map((t, i) => ({ id: t.id, title: t.name, publisher: "交易发布者", description: t.description,
    price_units: (i + 1) * 1e6, agent_type: t.trade_side, strategy: "TASK", task_name: t.name,
    task_mode: "prepare", tools, products: t.products, max_trade_units: 100e6, daily_limit_units: 500e6,
    sales_count: 0, status: "PUBLISHED", is_mine: false }));
  await page.route("**/api/**", async route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    let body: any = {};
    if (path === "/api/auth/me") body = { user: { sub: "seller", email: "seller@example.test" }, csrf: "publish-csrf" };
    else if (path === "/api/catalog") body = { templates: tasks, skills, products: [] };
    else if (path === "/api/dashboard") body = { agents, operations: [], grants: [], events: [], subscriptions: [], wallet: { USDC: "10000", ETH: "0" } };
    else if (path === "/api/marketplace") body = { listings, purchases: [], sales: [], revenue_units: 0 };
    else if (path === "/api/agents" && request.method() === "POST") {
      expect(request.headers()["x-csrf-token"]).toBe("publish-csrf");
      created = request.postDataJSON(); body = { ...created, id: "new-seller", skill_id: "eth-seller", status: "ACTIVE" };
      agents = [body];
    } else if (path === "/api/marketplace/listings") {
      published = request.postDataJSON(); body = { id: "published" };
    } else if (path.endsWith("/conversations")) body = { conversations: [] };
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  await expect(page.getByRole("navigation", { name: "主要功能" }).getByRole("button")).toHaveCount(3);
  await expect(page.getByRole("button", { name: "Agent 管理", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Agent 市场", exact: true }).click();
  await page.getByRole("button", { name: "卖出 Agent", exact: true }).click();
  await expect(page.getByTestId("listing-eth-sell")).toContainText("已有 ETH 持仓");
  await expect(page.getByTestId("listing-eth-buy")).toHaveCount(0);
  await page.getByRole("button", { name: "发布 Agent", exact: true }).click();
  await page.getByRole("combobox", { name: "发布任务模板", exact: true }).selectOption("eth-sell");
  await page.getByLabel("市场名称", { exact: true }).fill("我的 ETH 卖出助手");
  await page.getByLabel("发布者名称", { exact: true }).fill("交易服务商");
  await page.getByLabel("任务介绍", { exact: true }).fill("卖出用户自己的 ETH 持仓，所有订单需要单独确认。");
  await page.getByLabel("一次性服务费（模拟 USDC）", { exact: true }).fill("2");
  await expect(page.getByLabel("发布预览")).toContainText("2 USDC");
  await page.getByRole("button", { name: "确认发布", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(created.template_id).toBe("eth-sell");
  expect(created.enable_template_products).toBe(true);
  expect(published).toMatchObject({ agent_id: "new-seller", template_id: "eth-sell", price: "2" });
  await page.getByRole("button", { name: "Agent 工作台", exact: true }).click();
  await page.getByRole("tab", { name: "我的 Agents", exact: true }).click();
  await expect(page.getByTestId("agent-new-seller")).toContainText("我的 ETH 卖出助手");
});

test("sell workspace prepares ETH quantity and shows USDC proceeds", async ({ page }) => {
  const task = { id: "eth-sell", name: "ETH 卖出 Agent", trade_side: "SELL", mode: "prepare",
    digest: "a".repeat(64), tools: ["get_portfolio", "get_quote", "prepare_trade"], products: ["trade"] };
  const agent = { id: "seller-agent", name: "卖出助手", template_id: task.id, skill_id: "eth-seller",
    status: "ACTIVE", max_trade_units: 100e6, daily_limit_units: 500e6 };
  const op = { id: "sell-op", agent_id: agent.id, conversation_id: "sell-session", kind: "TRADE",
    status: "PENDING", created_at: Date.now() / 1000, expires_at: Date.now() / 1000 + 900,
    intent: { amount_units: 15_000_001, sell_asset: "ETH", sell_amount: "0.005000000000000001", buy_asset: "USDC",
      reference_price: "3000", minimum_received: "14.925", max_slippage_bps: 50 } };
  let submitted: any;
  await page.route("**/api/**", async route => {
    const req = route.request(), path = new URL(req.url()).pathname;
    let body: any = {};
    if (path === "/api/auth/me") body = { user: { sub: "sell-owner", email: "sell@example.test" }, csrf: "sell-csrf" };
    else if (path === "/api/catalog") body = { templates: [task], products: [], skills: [{ id: agent.skill_id, trade_sides: ["SELL"], tools: [...task.tools, "execute_trade"] }] };
    else if (path === "/api/dashboard") body = { agents: [agent], operations: submitted ? [op] : [], subscriptions: ["trade"], grants: [], events: [], wallet: { USDC: "9970", ETH: "0.01" } };
    else if (path.endsWith("/conversations")) body = req.method() === "POST" ? { id: "sell-session", agent_id: agent.id } : { conversations: [] };
    else if (path.endsWith("/tasks")) { submitted = req.postDataJSON(); body = { status: "QUEUED" }; }
    else if (path === "/api/conversations/sell-session") body = { id: "sell-session", agent_id: agent.id, can_resume: true,
      runs: [], messages: [{ role: "assistant", content: { action: "task", tool_result: { operation: op } } }] };
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  await page.getByTestId("agent-seller-agent").getByRole("button", { name: "运行 Agent" }).click();
  await page.getByRole("button", { name: "交易下单", exact: true }).click();
  await expect(page.getByRole("region", { name: "卖出订单" })).toBeVisible();
  await expect(page.getByRole("button", { name: "买入", exact: true })).toHaveCount(0);
  await page.getByLabel("卖出数量（ETH）").fill("0.005000000000000001");
  await page.getByRole("button", { name: "生成待确认订单", exact: true }).click();
  await expect(page.getByTestId("operation-sell-op")).toContainText("0.005000000000000001 ETH → USDC");
  await expect(page.getByTestId("operation-sell-op")).toContainText("最少接收 14.925 USDC");
  expect(submitted).toEqual({ template_id: "eth-sell", template_digest: task.digest, parameters: { amount: "0.005000000000000001", max_slippage_bps: 50 } });
  await page.getByRole("button", { name: "查看操作详情", exact: true }).click();
  await expect(page.getByRole("dialog")).toContainText("ETH → USDC");
  await expect(page.getByRole("dialog")).toContainText("14.925 USDC");
});
