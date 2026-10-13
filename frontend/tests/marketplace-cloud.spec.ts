import { test, expect } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { cognitoLogin, cognitoSignIn } from "./cognito";

test("seller publishes, buyer purchases with Cognito and isolated automation settles once through Gateway/MCP", async ({ browser }) => {
  test.skip(!process.env.TEST_BASE_URL?.startsWith("https://"), "Requires deployed Cognito and AgentCore");
  test.setTimeout(300_000);
  const credentials = JSON.parse(fs.readFileSync(path.resolve("../.deployment/market-credentials.json"), "utf8"));
  const sellerContext = await browser.newContext(), buyerContext = await browser.newContext();
  const seller = await sellerContext.newPage(), buyer = await buyerContext.newPage();
  let purchase: any, buyerCsrf: string;
  try {
    await cognitoLogin(seller, credentials.seller);
    const sellerMe = await (await seller.request.get("/api/auth/me")).json();
    const sellerBefore = await (await seller.request.get("/api/dashboard")).json();
    let source = sellerBefore.agents.find((a: any) => a.name === "市场示例发布 Agent");
    if (!source) {
      const response = await seller.request.post("/api/agents", {
        headers: { "X-CSRF-Token": sellerMe.csrf },
        data: { name: "市场示例发布 Agent", max_trade: "100", daily_limit: "500" },
      });
      expect(response.ok()).toBeTruthy();
      source = await response.json();
    }
    await seller.reload();
    await seller.getByRole("button", { name: "Agent 市场", exact: true }).click();
    const existing = await (await seller.request.get("/api/marketplace")).json();
    let listing = existing.listings.find((l: any) => l.is_mine && l.title === "示例 · ETH 自动定投");
    if (!listing) {
      await seller.getByRole("button", { name: "发布 Agent", exact: true }).click();
      await seller.getByLabel("来源 Agent").selectOption(source.id);
      await seller.getByLabel("市场名称").fill("示例 · ETH 自动定投");
      await seller.getByLabel("发布者名称").fill("平台演示发布者");
      await seller.getByLabel("任务介绍").fill("平台模拟市场示例：按买方设定的金额和间隔定投 ETH，支持累计额度和到期自动停止，不承诺收益。");
      await seller.getByLabel("一次性服务费（模拟 USDC）").fill("1");
      await seller.getByRole("button", { name: "确认发布", exact: true }).click();
      await expect(seller.getByRole("heading", { name: "发布 Agent", exact: true })).toHaveCount(0);
      listing = (await (await seller.request.get("/api/marketplace")).json()).listings.find((l: any) => l.title === "示例 · ETH 自动定投");
    }
    expect(listing).toBeTruthy();
    await cognitoLogin(buyer, credentials.buyer);
    buyerCsrf = (await (await buyer.request.get("/api/auth/me")).json()).csrf;
    const buyerBefore = await (await buyer.request.get("/api/dashboard")).json();
    await buyer.getByRole("button", { name: "Agent 市场", exact: true }).click();
    await buyer.getByTestId("listing-" + listing.id).getByRole("button", { name: "购买并授权" }).click();
    await buyer.getByLabel("每笔交易（USDC）").fill("1");
    await buyer.getByLabel("交易累计上限（USDC）").fill("1");
    await buyer.getByLabel("每日交易上限（USDC）").fill("1");
    await buyer.getByRole("button", { name: "核对订单与自动交易授权" }).click();
    await expect(buyer.getByRole("heading", { name: "确认购买与自动交易授权" })).toBeVisible();
    const prepared = await (await buyer.request.get("/api/dashboard")).json();
    purchase = prepared.operations.find((o: any) => o.kind === "MARKET_PURCHASE" && o.status === "PENDING");
    expect(purchase).toBeTruthy();
    const denied = await buyer.request.post(`/api/operations/${purchase.id}/approve`, {
      headers: { "X-CSRF-Token": buyerCsrf }, data: { intent_hash: purchase.intent_hash },
    });
    expect(denied.status()).toBe(401);
    await buyer.getByRole("checkbox").check();
    await buyer.getByRole("button", { name: "验证并购买", exact: true }).click();
    await cognitoSignIn(buyer, credentials.buyer);
    await expect(buyer.getByRole("heading", { name: "Agent 市场", exact: true })).toBeVisible();
    await expect(buyer.getByRole("status")).toContainText("购买成功");
    await buyer.getByRole("tab", { name: "我购买的", exact: true }).click();
    await expect(buyer.getByText(purchase.id, { exact: false })).toBeVisible();
    const after = await (await buyer.request.get("/api/dashboard")).json();
    const order = after.operations.find((o: any) => o.id === purchase.id);
    expect(order.status).toBe("CONFIRMED");
    expect(order.agent_id).not.toBe(source.id);
    expect(Number(after.wallet.USDC)).toBeLessThanOrEqual(Number(buyerBefore.wallet.USDC) - 1);
    expect((await seller.request.get(`/api/agents/${order.agent_id}/conversations`)).status()).toBe(404);
    const sellerAfter = await (await seller.request.get("/api/dashboard")).json();
    expect(Number(sellerAfter.wallet.USDC)).toBe(Number(sellerBefore.wallet.USDC) + 1);
    const market = await (await seller.request.get("/api/marketplace")).json();
    const sale = market.sales.find((o: any) => o.id === purchase.id);
    expect(sale).toBeTruthy();
    expect(sale.agent_id).toBeUndefined();
    expect(sale.grant_id).toBeUndefined();
    await expect.poll(async () => {
      const state = await (await buyer.request.get("/api/dashboard")).json();
      return state.operations.filter((o: any) => o.grant_id === purchase.id && o.status === "CONFIRMED").length;
    }, { timeout: 160_000, intervals: [3000] }).toBe(1);
    const traded = await (await buyer.request.get("/api/dashboard")).json();
    expect(Number(traded.wallet.USDC)).toBe(Number(buyerBefore.wallet.USDC) - 2);
    expect(traded.grants.find((g: any) => g.id === purchase.id).used_units).toBe(1_000_000);
    await buyer.locator("article").filter({ hasText: purchase.id }).getByRole("button", { name: "查看 Agent 与运行记录" }).click();
    await expect(buyer.getByRole("region", { name: "运行过程" }).getByText("Gateway 权限检查通过", { exact: true })).toBeVisible();
    await expect(buyer.getByRole("region", { name: "运行过程" }).getByText("调用 execute_trade", { exact: true })).toBeVisible();
    const history = await (await buyer.request.get(`/api/agents/${order.agent_id}/conversations`)).json();
    expect((await seller.request.get(`/api/conversations/${history.conversations[0].id}`)).status()).toBe(404);
    await buyer.getByRole("button", { name: "Agent 市场", exact: true }).click();
    await buyer.getByRole("tab", { name: "我购买的", exact: true }).click();
    await buyer.locator("article").filter({ hasText: purchase.id }).getByRole("button", { name: "停止自动交易", exact: true }).click();
    await expect(buyer.locator("article").filter({ hasText: purchase.id }).getByText("已停止", { exact: true })).toBeVisible();
    await buyer.reload();
    expect(Number((await (await buyer.request.get("/api/dashboard")).json()).wallet.USDC)).toBe(Number(traded.wallet.USDC));
    await buyer.getByRole("tab", { name: "发现 Agent", exact: true }).click();
    await buyer.screenshot({ path: "../.deployment/agent-marketplace.png", fullPage: true });
    await seller.getByRole("tab", { name: "卖方中心", exact: true }).click();
    await seller.screenshot({ path: "../.deployment/agent-marketplace-seller.png", fullPage: true });
  } finally {
    if (purchase) {
      const me = await (await buyer.request.get("/api/auth/me")).json();
      await buyer.request.post(`/api/marketplace/purchases/${purchase.id}/revoke`, { headers: { "X-CSRF-Token": me.csrf } });
      const final = await (await buyer.request.get(`/api/operations/${purchase.id}`)).json();
      if (final.status === "CONFIRMED" && final.agent_id) {
        await buyer.request.delete(`/api/agents/${final.agent_id}`, { headers: { "X-CSRF-Token": me.csrf } });
      }
    }
    await sellerContext.close(); await buyerContext.close();
  }
});
