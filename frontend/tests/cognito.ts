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
