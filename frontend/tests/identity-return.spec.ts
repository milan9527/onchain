import { test, expect } from "@playwright/test";

test("tool OAuth returns to the original conversation and continues only its pending task", async ({ page }) => {
  let completed = 0;
  const requests: string[] = [];
  await page.addInitScript(() => sessionStorage.setItem("onchain:identity-return:alice", JSON.stringify({
    resume_id: "run-original", expiresAt: Date.now() + 600_000,
  })));
  await page.route("**/api/**", async route => {
    const path = new URL(route.request().url()).pathname;
    requests.push(path);
    let body: any = {};
    if (path === "/api/auth/me") body = { user: { sub: "alice", email: "alice@example.test" }, csrf: "csrf" };
    else if (path === "/api/health") body = { oauth: true };
    else if (path === "/api/dashboard") body = {
      agents: [{ id: "agent-original", name: "工具授权测试", status: "ACTIVE", skill_id: "controlled-trader" }],
      operations: [], grants: [], events: [], subscriptions: [], wallet: { USDC: "10000", ETH: "0" },
    };
    else if (path === "/api/catalog") body = { products: [], skills: [] };
    else if (path.endsWith("/conversations")) body = { conversations: [] };
    else if (path === "/api/identity/complete") {
      completed++;
      expect(route.request().postDataJSON()).toEqual({
        session_uri: "urn:ietf:params:oauth:request_uri:original", source: "internal", resume_id: "run-original",
      });
      expect(route.request().headers()["x-csrf-token"]).toBe("csrf");
      body = { connected: true, agent_id: "agent-original", conversation_id: "session-original", queued: { status: "QUEUED" } };
    } else if (path === "/api/conversations/session-original") body = {
      id: "session-original", agent_id: "agent-original", can_resume: true, runs: [], busy_until: 0,
      messages: [{ role: "user", content: "授权前的原始任务" }],
    };
    await route.fulfill({ json: body });
  });
  await page.goto("/?identity_session=urn%3Aietf%3Aparams%3Aoauth%3Arequest_uri%3Aoriginal&identity_source=internal");
  await expect(page.getByText("授权前的原始任务", { exact: true })).toBeVisible();
  expect(completed).toBe(1);
  expect(requests.some(path => path.includes("/approve") || path.includes("/execute"))).toBe(false);
  await expect(page).toHaveURL("/");
  expect(await page.evaluate(() => sessionStorage.getItem("onchain:identity-return:alice"))).toBeNull();
});

test("an account switch cannot submit another user's pending task", async ({ page }) => {
  let resume: string | undefined;
  await page.addInitScript(() => sessionStorage.setItem("onchain:identity-return:alice", JSON.stringify({
    resume_id: "run-alice", expiresAt: Date.now() + 600_000,
  })));
  await page.route("**/api/**", async route => {
    const path = new URL(route.request().url()).pathname;
    let body: any = {}, status = 200;
    if (path === "/api/auth/me") body = { user: { sub: "bob", email: "bob@example.test" }, csrf: "csrf-bob" };
    else if (path === "/api/health") body = { oauth: true };
    else if (path === "/api/dashboard") body = {
      agents: [], operations: [], grants: [], events: [], subscriptions: [], wallet: { USDC: "10000", ETH: "0" },
    };
    else if (path === "/api/catalog") body = { products: [], skills: [] };
    else if (path === "/api/identity/complete") {
      resume = route.request().postDataJSON().resume_id;
      status = 403; body = { error: { code: "IDENTITY_CONSENT_FAILED", message: "请使用原登录账号重新授权" } };
    }
    await route.fulfill({ json: body, status });
  });
  await page.goto("/?identity_session=urn%3Aietf%3Aparams%3Aoauth%3Arequest_uri%3Aoriginal&identity_source=internal");
  await expect(page.getByRole("alert")).toContainText("请使用原登录账号重新授权");
  expect(resume).toBe("");
});
