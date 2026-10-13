import { test, expect } from "@playwright/test";

test("demo examples populate after an asynchronous Agent refresh and stay consistent when switching networks", async ({ page }) => {
  const base = "0x4200000000000000000000000000000000000006";
  const sol = "So11111111111111111111111111111111111111112";
  const task = { id: "token-research", name: "代币尽调报告", description: "演示研究", category: "研究",
    skill_id: "token-researcher", mode: "read", tools: ["get_token_info"], products: ["token-data"],
    availability: "READY", data_mode: "demo", digest: "a".repeat(64),
    parameters: { required: ["chain", "token_address"], properties: {
      chain: { type: "string", title: "网络", enum: ["8453", "501"], default: "8453" },
      token_address: { type: "string", title: "代币合约地址" },
    } },
    examples: { "8453": { chain: "8453", token_address: base }, "501": { chain: "501", token_address: sol } },
  };
  const agent = { id: "demo-agent", name: "Demo", status: "ACTIVE", skill_id: task.skill_id,
    template_id: task.id, max_trade_units: 100000000, daily_limit_units: 500000000 };
  let created = false, submitted: any, release: () => void = () => {};
  const delayed = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/api/**", async route => {
    const url = new URL(route.request().url()), method = route.request().method();
    let body: any = {};
    if (url.pathname === "/api/health") body = { auth: "local" };
    else if (url.pathname === "/api/auth/me") body = { user: { sub: "demo-user", email: "demo@example.test" }, csrf: "fixture" };
    else if (url.pathname === "/api/catalog") body = { onchain_data_mode: "demo", templates: [task],
      products: [{ id: "token-data", name: "代币数据" }], skills: [{ id: task.skill_id, tools: task.tools }] };
    else if (url.pathname === "/api/dashboard") {
      if (created) await delayed;
      body = { agents: created ? [agent] : [], operations: [], grants: [], events: [],
        subscriptions: ["token-data"], wallet: { USDC: "10000", ETH: "0" }, mode: "paper" };
    } else if (url.pathname === "/api/agents" && method === "POST") { created = true; body = agent; }
    else if (url.pathname === "/api/agents/demo-agent/conversations") {
      body = method === "POST" ? { id: "demo-conv", agent_id: agent.id } : { conversations: [] };
    } else if (url.pathname === "/api/conversations/demo-conv/tasks") {
      submitted = route.request().postDataJSON(); body = { status: "QUEUED" };
    } else if (url.pathname === "/api/conversations/demo-conv") {
      body = { id: "demo-conv", agent_id: agent.id, can_resume: true, messages: [], runs: [], busy_until: 0 };
    }
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "任务模板", exact: true }).click();
  await page.getByTestId("template-token-research").getByRole("button", { name: "使用模板" }).click();
  await page.getByRole("button", { name: "创建模板 Agent", exact: true }).click();
  await expect(page.getByText("当前 Skill 没有适用的任务模板，请从「任务模板」创建专用 Agent。")).toBeVisible();
  release();
  await expect(page.getByLabel("代币合约地址", { exact: true })).toHaveValue(base);
  await page.getByRole("combobox", { name: "网络", exact: true }).selectOption("501");
  await expect(page.getByLabel("代币合约地址", { exact: true })).toHaveValue(sol);
  await page.getByLabel("代币合约地址", { exact: true }).fill("");
  await expect(page.getByLabel("代币合约地址", { exact: true })).toHaveValue("");
  await page.getByRole("button", { name: "恢复演示参数" }).click();
  await expect(page.getByLabel("代币合约地址", { exact: true })).toHaveValue(sol);
  await page.getByRole("button", { name: "运行任务", exact: true }).click();
  await expect.poll(() => submitted?.parameters).toEqual({ chain: "501", token_address: sol });
  expect(submitted.template_digest).toBe(task.digest);
});
