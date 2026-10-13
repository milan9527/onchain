import { test, expect, type Page } from "@playwright/test";

async function workspace(page: Page) {
  const user = { sub: "return-user", email: "return@example.test", tenant: "return-user", admin: false };
  const agents = ["a", "b"].map(id => ({
    id, name: "Agent " + id, status: "ACTIVE", skill_id: "controlled-trader",
    max_trade_units: 100000000, daily_limit_units: 500000000,
  }));
  const op: any = {
    id: "op-return", agent_id: "b", conversation_id: "b-source", kind: "TRADE",
    status: "PENDING", intent_hash: "reviewed-hash", created_at: Date.now()/1000,
    expires_at: Date.now()/1000 + 900, intent: { amount_units: 20000000, max_slippage_bps: 50 },
  };
  const snapshot = structuredClone(op);
  const histories: Record<string, any[]> = {
    a: [{ id: "a-latest", agent_id: "a", title: "A 的会话", updated_at: 300 }],
    b: [{ id: "b-latest", agent_id: "b", title: "B 较新的会话", updated_at: 200 },
        { id: "b-source", agent_id: "b", title: "发起交易的会话", updated_at: 100 }],
  };
  const fixture = { user, op, approvals: 0, executions: 0, canResume: true, authorized: true };
  await page.route("**/test-cognito", route => route.fulfill({
    contentType: "text/html; charset=utf-8", body: '<a href="/?verified_operation=op-return">完成认证</a>',
  }));
  await page.route("**/api/**", async route => {
    const pathname = new URL(route.request().url()).pathname;
    let body: any = {}, status = 200;
    if (pathname === "/api/health") body = { oauth: true, auth: "cognito" };
    else if (pathname === "/api/auth/me") body = { user: fixture.user, csrf: "fixture" };
    else if (pathname === "/api/dashboard") body = {
      agents: fixture.authorized ? agents : [{ ...agents[0], id: "c", name: "Other user's Agent" }],
      operations: fixture.authorized ? [op] : [], grants: [], events: [], subscriptions: [],
      wallet: { USDC: "10000", ETH: "0" },
    };
    else if (pathname === "/api/catalog") body = { products: [], skills: [] };
    else if (pathname.startsWith("/api/agents/")) body = { conversations: histories[pathname.split("/")[3]] || [] };
    else if (pathname === "/api/operations/op-return/oauth-stepup") body = { authorization_url: "/test-cognito" };
    else if (pathname === "/api/operations/op-return/approve") { fixture.approvals++; op.status = "APPROVED"; body = op; }
    else if (pathname === "/api/operations/op-return/execute") {
      fixture.executions++; op.status = "CONFIRMED"; op.execution = { receipt: "paper_return", received: "0.006666" };
      body = { conversation_id: op.conversation_id, status: "QUEUED" };
    } else if (pathname === "/api/operations/op-return") {
      if (fixture.authorized) body = op;
      else { status = 404; body = { error: { code: "OPERATION_NOT_FOUND", message: "Operation unavailable" } }; }
    } else if (pathname.startsWith("/api/conversations/")) {
      const id = pathname.split("/")[3];
      const row = Object.values(histories).flat().find(item => item.id === id);
      if (!row || !fixture.authorized) { status = 404; body = { error: { code: "SESSION_NOT_FOUND", message: "Conversation unavailable" } }; }
      else body = {
        ...row, can_resume: fixture.canResume, busy_until: 0, runs: [],
        messages: [{ role: "user", content: "会话消息 " + id },
          ...(id === "b-source" ? [{ role: "assistant", content: { tool_result: { operation: snapshot } } }] : [])],
      };
    }
    await route.fulfill({ status, json: body });
  });
  return fixture;
}

async function selectSource(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "Agent 工作台" }).click();
  await page.getByLabel("选择 Agent").selectOption("b");
  await page.getByTestId("conversation-b-source").click();
  await expect(page.getByText("会话消息 b-source", { exact: true })).toBeVisible();
}

test("OAuth confirmation returns to the initiating Agent and exact conversation despite another tab's selection", async ({ page }) => {
  const fixture = await workspace(page);
  await selectSource(page);
  await page.getByRole("button", { name: "查看操作详情" }).click();
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "验证并执行", exact: true }).click();
  await expect(page.getByRole("link", { name: "完成认证" })).toBeVisible();
  // Another tab can update shared localStorage while the original tab is at Cognito.
  await page.evaluate(() => {
    localStorage.setItem("onchain:last-agent:return-user", "a");
    localStorage.setItem("onchain:last-session:return-user:b", "b-latest");
  });
  await page.getByRole("link", { name: "完成认证" }).click();
  await expect(page.getByRole("heading", { name: "Agent 工作台", exact: true })).toBeVisible();
  await expect(page.getByLabel("选择 Agent")).toHaveValue("b");
  await expect(page.getByText("会话消息 b-source", { exact: true })).toBeVisible();
  await expect(page.getByTestId("operation-op-return").getByText("已完成", { exact: true })).toBeVisible();
  expect(fixture.approvals).toBe(1);
  expect(fixture.executions).toBe(1);
  await page.reload();
  await expect(page.getByRole("heading", { name: "Agent 工作台", exact: true })).toBeVisible();
  await expect(page.getByText("会话消息 b-source", { exact: true })).toBeVisible();
  expect(fixture.executions).toBe(1);
});

test("login restores this tab's conversation while preserving the server's read-only restriction", async ({ page }) => {
  const fixture = await workspace(page);
  await selectSource(page);
  fixture.canResume = false; // A new login may read history but may not resume an old login's Runtime.
  await page.evaluate(() => { localStorage.setItem("onchain:last-agent:return-user", "a"); });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Agent 工作台", exact: true })).toBeVisible();
  await expect(page.getByLabel("选择 Agent")).toHaveValue("b");
  await expect(page.getByText("会话消息 b-source", { exact: true })).toBeVisible();
  await expect(page.getByText("历史会话只读，可查看消息和运行记录。")).toBeVisible();
  await expect(page.getByLabel("输入操作意图")).toHaveCount(0);
  expect(fixture.approvals).toBe(0);
  expect(fixture.executions).toBe(0);
});

test("a different account cannot restore or execute the previous user's OAuth operation", async ({ page }) => {
  const fixture = await workspace(page);
  await selectSource(page);
  await page.getByRole("button", { name: "查看操作详情" }).click();
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "验证并执行", exact: true }).click();
  await expect(page.getByRole("link", { name: "完成认证" })).toBeVisible();
  fixture.user = { ...fixture.user, sub: "other-user", email: "other@example.test" };
  fixture.authorized = false;
  await page.getByRole("link", { name: "完成认证" }).click();
  await expect(page.getByRole("alert")).toContainText("Operation unavailable");
  await expect(page.getByText("会话消息 b-source", { exact: true })).toHaveCount(0);
  expect(fixture.approvals).toBe(0);
  expect(fixture.executions).toBe(0);
});

test("confirmation can recover the owned operation's conversation without browser return state", async ({ page }) => {
  const fixture = await workspace(page);
  await page.goto("/?verified_operation=op-return");
  await expect(page.getByRole("heading", { name: "Agent 工作台", exact: true })).toBeVisible();
  await expect(page.getByLabel("选择 Agent")).toHaveValue("b");
  await expect(page.getByText("会话消息 b-source", { exact: true })).toBeVisible();
  await expect(page.getByTestId("operation-op-return").getByText("已完成", { exact: true })).toBeVisible();
  expect(fixture.approvals).toBe(1);
  expect(fixture.executions).toBe(1);
});
