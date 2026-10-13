import { test, expect } from "@playwright/test";

test("history restores after refresh and ignores a late response from another agent", async ({ page }) => {
  const agents = ["a", "b"].map(id => ({ id, name: "Agent "+id, status: "ACTIVE", skill_id: "controlled-trader", max_trade_units: 100000000, daily_limit_units: 500000000 }));
  let release: () => void = () => {};
  const delayed = new Promise<void>(resolve => { release = resolve; });
  let canResume = true;
  const histories: Record<string, any[]> = {
    a: [{ id: "a2", agent_id: "a", title: "第二段会话", updated_at: 200, message_count: 1, kind: "chat" },
        { id: "a1", agent_id: "a", title: "第一段会话", updated_at: 100, message_count: 1, kind: "chat" }],
    b: [{ id: "b1", agent_id: "b", title: "B 的会话", updated_at: 300, message_count: 1, kind: "chat" }],
  };
  await page.route("**/api/**", async route => {
    const path = new URL(route.request().url()).pathname;
    let body: any = {};
    if (path === "/api/auth/me") body = { user: { sub: "history-user", email: "history@example.test" }, csrf: "fixture" };
    else if (path === "/api/health") body = { auth: "local" };
    else if (path === "/api/dashboard") body = { agents, operations: [], grants: [], events: [], subscriptions: [], wallet: { USDC: "10000", ETH: "0" } };
    else if (path === "/api/catalog") body = { products: [], skills: [] };
    else if (/\/agents\/[ab]\/conversations$/.test(path)) body = { conversations: histories[path.split("/")[3]] };
    else if (path.startsWith("/api/conversations/")) {
      const id = path.split("/")[3];
      if (id === "a1") await delayed;
      const record = Object.values(histories).flat().find(item => item.id === id);
      body = { ...record, can_resume: canResume, busy_until: 0, runs: [],
        messages: [{ role: "user", content: "内容 "+id }] };
    }
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Agent 工作台" }).click();
  await expect(page.getByText("内容 a2", { exact: true })).toBeVisible();
  const request = page.waitForRequest("**/api/conversations/a1");
  await page.getByTestId("conversation-a1").click();
  await request;
  await page.getByLabel("选择 Agent").selectOption("b");
  await expect(page.getByText("内容 b1", { exact: true })).toBeVisible();
  const late = page.waitForResponse("**/api/conversations/a1");
  release();
  await late;
  await expect(page.getByText("内容 b1", { exact: true })).toBeVisible();
  await expect(page.getByText("内容 a1", { exact: true })).toHaveCount(0);
  canResume = false;
  await page.reload();
  await page.getByRole("button", { name: "Agent 工作台" }).click();
  await expect(page.getByText("内容 b1", { exact: true })).toBeVisible();
  await expect(page.getByText("历史会话只读，可查看消息和运行记录。")).toBeVisible();
  await expect(page.getByLabel("输入操作意图")).toHaveCount(0);
  await page.getByRole("button", { name: "新会话", exact: true }).click();
  await expect(page.getByLabel("输入操作意图")).toBeVisible();
  await expect(page.getByTestId("conversation-b1")).toBeVisible();
});
