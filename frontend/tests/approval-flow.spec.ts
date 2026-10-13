import { test, expect } from "@playwright/test";

for (const kind of ["TRADE", "GRANT"]) {
  test(`${kind}: approval continuation and recovery stay bound to the selected operation`, async ({ page }) => {
    const user = { sub: "fixture", tenant: "fixture", email: "fixture@example.test", admin: false };
    const agent = { id: "fixture-agent", name: "Regression", skill_id: "controlled-trader", status: "ACTIVE", max_trade_units: 100000000, daily_limit_units: 500000000 };
    const op: any = {
      id: "fixture-operation", agent_id: agent.id, kind, status: "PENDING", intent_hash: "fixture-hash",
      created_at: Date.now() / 1000, expires_at: Date.now() / 1000 + 900,
      intent: { amount_units: 20000000, total_units: 200000000, interval_seconds: 300, expires_at: Date.now() / 1000 + 3600, max_slippage_bps: 50 },
    };
    // The conversation deliberately retains its original PENDING snapshot.
    const original = structuredClone(op);
    let approvals = 0;
    let executionRequests = 0;
    await page.route("**/api/**", async route => {
      const path = new URL(route.request().url()).pathname;
      let body: any = {};
      let status = 200;
      if (path === "/api/health") body = { auth: "local", runtime: "local" };
      else if (path === "/api/auth/me") body = { user, csrf: "fixture" };
      else if (path === "/api/dashboard") body = { agents: [agent], operations: [op], grants: [], events: [], subscriptions: [], wallet: { USDC: "10000", ETH: "0" }, mode: "paper" };
      else if (path === "/api/catalog") body = { products: [], skills: [] };
      else if (path === `/api/agents/${agent.id}/conversations`) body = { id: "fixture-session", agent_id: agent.id, messages: [] };
      else if (path === "/api/conversations/fixture-session/messages") body = { status: "QUEUED" };
      else if (path === "/api/conversations/fixture-session") body = {
        id: "fixture-session", agent_id: agent.id, busy_until: 0,
        messages: [{ role: "assistant", content: {
          intent: { action: "prepare_trade", explanation: "仅准备交易，不会执行。" },
          tool_result: { operation: original },
        } }],
      };
      else if (path.endsWith("/stepup")) body = { verified: true };
      else if (path.endsWith("/approve")) {
        approvals++;
        op.status = kind === "GRANT" ? "CONFIRMED" : "APPROVED";
        body = op;
      } else if (path.endsWith("/execute")) {
        executionRequests++;
        if (executionRequests === 1) {
          status = 503;
          body = { error: { code: "QUEUE_UNAVAILABLE", message: "执行队列暂时不可用，请重试。" } };
        } else {
          op.status = "CONFIRMED";
          op.execution = { received: "0.00666666", receipt: "paper_fixture" };
          body = { status: "QUEUED" };
        }
      }
      await route.fulfill({ status, json: body });
    });
    await page.goto("/");
    await page.getByRole("button", { name: /授权与操作/ }).click();
    await page.getByRole("button", { name: "核对并确认" }).click();
    await page.getByRole("checkbox").check();
    await page.getByLabel("重新输入密码验证本人身份").fill("fixture-password");
    await page.getByRole("button", { name: kind === "TRADE" ? "验证并执行" : "验证并授权" }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    if (kind === "GRANT") {
      await expect(page.locator("tr").getByText("已完成")).toBeVisible();
      expect(approvals).toBe(1);
      expect(executionRequests).toBe(0);
      return;
    }
    await expect(page.getByText("执行队列暂时不可用，请重试。")).toBeVisible();
    await page.getByRole("button", { name: "Agent 工作台" }).click();
    await page.getByLabel("输入操作意图").fill("查看刚才的操作");
    await page.getByRole("button", { name: "发送", exact: true }).click();
    const card = page.getByTestId("operation-"+op.id);
    await expect(card.getByText("已批准", { exact: true })).toBeVisible();
    await expect(card.getByText("操作已准备，等待你核对和授权。")).toHaveCount(0);
    await card.getByRole("button", { name: "执行此笔交易" }).click();
    await expect(card.getByText("已完成", { exact: true })).toBeVisible();
    await expect(card.getByText(/paper_fixture/)).toBeVisible();
    await expect(page.getByText("已按你确认的范围完成模拟交易。", { exact: true })).toBeVisible();
    await expect(page.getByText("仅准备交易，不会执行。", { exact: true })).toHaveCount(0);
    expect(approvals).toBe(1);
    expect(executionRequests).toBe(2);
  });
}
