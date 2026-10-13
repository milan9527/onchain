import { test, expect } from "@playwright/test";

test("four modules, task-specific examples, Agent-scoped orders and deletion cleanup", async ({ page }) => {
  const tasks = [
    { id: "token-risk", name: "代币风险扫描", skill_id: "risk-reviewer", mode: "read", tools: ["get_token_risk"], products: ["risk"] },
    { id: "eth-order", name: "ETH 买入下单", skill_id: "controlled-trader", mode: "prepare", tools: ["prepare_trade"], products: ["trade"] },
  ].map(t => ({ ...t, availability: "READY", digest: "a".repeat(64), description: t.name, category: t.mode,
    parameters: { required: [], properties: {} } }));
  let agents: any[] = [
    { id: "research", name: "风险研究", skill_id: "risk-reviewer", template_id: "token-risk", status: "ACTIVE" },
    { id: "trader", name: "交易助手", skill_id: "controlled-trader", template_id: "eth-order", status: "ACTIVE", max_trade_units: 100000000, daily_limit_units: 500000000 },
  ];
  let deleted = "", deletedCsrf = "";
  await page.route("**/api/**", async route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    let body: any = {};
    if (path === "/api/health") body = { auth: "local" };
    else if (path === "/api/auth/me") body = { user: { sub: "module-user", email: "modules@example.test" }, csrf: "module-csrf" };
    else if (path === "/api/catalog") body = { templates: tasks, products: [], skills: [
      { id: "risk-reviewer", name: "风险审查", tools: ["get_token_risk"] },
      { id: "controlled-trader", name: "受控交易", tools: ["prepare_trade", "execute_trade"] },
    ] };
    else if (path === "/api/dashboard") body = { agents, operations: [
      { id: "other-agent-order", agent_id: "elsewhere", kind: "TRADE", status: "PENDING", intent: { amount_units: 1000000 }, expires_at: Date.now()/1000+900 },
    ], subscriptions: ["trade"], grants: [], events: [], wallet: { USDC: "10000", ETH: "0" } };
    else if (path.endsWith("/conversations")) body = { conversations: [] };
    else if (request.method() === "DELETE") {
      deleted = path.split("/").at(-1)!; deletedCsrf = request.headers()["x-csrf-token"];
      agents = agents.filter(a => a.id !== deleted); body = { id: deleted, status: "DELETED" };
    }
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  await expect(page.getByRole("navigation", { name: "主要功能" }).getByRole("button")).toHaveCount(4);
  await page.getByTestId("agent-research").getByRole("button", { name: "运行 Agent" }).click();
  await expect(page.getByRole("region", { name: "当前 Agent 示例动作" }).getByRole("button", { name: "代币风险扫描" })).toBeVisible();
  await expect(page.getByRole("button", { name: "ETH 买入下单" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "买入下单", exact: true })).toHaveCount(0);
  await page.getByLabel("选择 Agent").selectOption("trader");
  await page.getByRole("button", { name: "ETH 买入下单" }).click();
  await expect(page.getByRole("region", { name: "买入订单" })).toContainText("交易助手");
  await expect(page.getByTestId("order-other-agent-order")).toHaveCount(0);
  await page.getByRole("button", { name: "Agent 管理", exact: true }).click();
  await page.getByRole("button", { name: "删除 Agent 交易助手", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "删除 Agent" })).toContainText("撤销持续授权");
  await page.getByRole("button", { name: "确认删除", exact: true }).click();
  await expect(page.getByTestId("agent-trader")).toHaveCount(0);
  expect(deleted).toBe("trader");
  expect(deletedCsrf).toBe("module-csrf");
  await page.getByRole("button", { name: "Agent 工作台", exact: true }).click();
  await expect(page.getByLabel("选择 Agent")).toHaveValue("research");
  await expect(page.getByRole("button", { name: "买入下单", exact: true })).toHaveCount(0);
});
