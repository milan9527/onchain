import { expect, type Page } from "@playwright/test";

export async function cognitoSignIn(page: Page, credentials: { email: string; password: string }) {
  await page.waitForURL(/\.amazoncognito\.com\//, { timeout: 25_000 });
  await page.locator('input[name="username"]').fill(credentials.email);
  await page.locator('input[name="password"]').fill(credentials.password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
}

export async function cognitoLogin(page: Page, credentials: { email: string; password: string }) {
  await page.goto("/");
  await page.getByRole("button", { name: "通过 Cognito 登录" }).click();
  await cognitoSignIn(page, credentials);
  await expect(page.getByRole("heading", { name: "Agent 工作台", exact: true })).toBeVisible();
}

export async function connectToolAccount(page: Page, credentials: { email: string; password: string }, source = "internal") {
  const info = await (await page.request.get("/api/identity/connections")).json();
  if (info.connections[source]?.active && info.connections[source]?.connected) return;
  const me = await (await page.request.get("/api/auth/me")).json();
  const response = await page.request.post("/api/identity/connections/" + source, {
    headers: { "X-CSRF-Token": me.csrf },
  });
  expect(response.ok()).toBeTruthy();
  const result = await response.json();
  if (!result.authorization_url) return;
  await page.goto(result.authorization_url);
  await cognitoSignIn(page, credentials);
  await expect(page.getByText("工具账户连接已生效。", { exact: true })).toBeVisible({ timeout: 45_000 });
}
