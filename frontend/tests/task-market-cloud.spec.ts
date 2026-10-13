import { test, expect } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { cognitoLogin, cognitoSignIn } from "./cognito";

test("seller publishes a task, buyer purchases and runs it through MCP, then deletes the isolated Agent", async ({ browser }) => {
  test.skip(!process.env.TEST_BASE_URL?.startsWith("https://"), "Requires Cognito and AgentCore");
  test.setTimeout(360_000);
  const credentials = JSON.parse(fs.readFileSync(path.resolve("../.deployment/market-credentials.json"), "utf8"));
  const sc = await browser.newContext(), bc = await browser.newContext();
  const seller = await sc.newPage(), buyer = await bc.newPage();
  let purchasedId = "", buyerCsrf = "";
  try {
    await cognitoLogin(seller, credentials.seller);
    const sellerMe = await (await seller.request.get("/api/auth/me")).json();
    const beforeSeller = await (await seller.request.get("/api/dashboard")).json();
    const title = "示例 · 持仓健康任务";
    let source = beforeSeller.agents.find((a: any) => a.name === "市场任务发布验收 Agent");
    if (!source) {
      await seller.getByRole("tab", { name: "任务模板", exact: true }).click();
      await seller.getByTestId("template-portfolio-health").getByRole("button", { name: "使用模板" }).click();
      await seller.getByLabel("Agent 名称", { exact: true }).fill("市场任务发布验收 Agent");
      await seller.getByRole("button", { name: "创建模板 Agent", exact: true }).click();
      await expect(seller.getByRole("dialog", { name: "运行任务模板" })).toBeVisible();
      await seller.getByRole("button", { name: "关闭", exact: true }).click();
      source = (await (await seller.request.get("/api/dashboard")).json()).agents.find((a: any) => a.name === "市场任务发布验收 Agent");
    }
    await seller.getByRole("button", { name: "Agent 市场", exact: true }).click();
    let listing = (await (await seller.request.get("/api/marketplace")).json()).listings.find((l: any) => l.is_mine && l.title === title);
    if (!listing) {
      await seller.getByRole("button", { name: "发布 Agent", exact: true }).click();
      await seller.getByRole("combobox", { name: "来源 Agent" }).selectOption(source.id);
      await seller.getByRole("combobox", { name: "发布任务模板" }).selectOption("portfolio-health");
      await seller.getByLabel("市场名称", { exact: true }).fill(title);
      await seller.getByLabel("发布者名称", { exact: true }).fill("平台任务发布者");
      await seller.getByLabel("任务介绍", { exact: true }).fill("检查本人模拟持仓与额度，买方独立运行任务，私有会话与数据仅本人可见。");
      await seller.getByLabel("一次性服务费（模拟 USDC）", { exact: true }).fill("1");
      await seller.getByRole("button", { name: "确认发布", exact: true }).click();
      await expect(seller.getByRole("dialog", { name: "发布 Agent", exact: true })).toHaveCount(0);
      listing = (await (await seller.request.get("/api/marketplace")).json()).listings.find((l: any) => l.is_mine && l.title === title);
    }
    expect(listing.strategy).toBe("TASK");
    await cognitoLogin(buyer, credentials.buyer);
    let me = await (await buyer.request.get("/api/auth/me")).json(); buyerCsrf = me.csrf;
    const beforeBuyer = await (await buyer.request.get("/api/dashboard")).json();
    await buyer.getByRole("button", { name: "Agent 市场", exact: true }).click();
    await buyer.getByTestId("listing-" + listing.id).getByRole("button", { name: "购买 Agent", exact: true }).click();
    await expect(buyer.getByRole("dialog")).not.toContainText("每笔交易（USDC）");
    await buyer.getByRole("button", { name: "核对购买内容", exact: true }).click();
    await expect(buyer.getByRole("dialog", { name: "确认购买任务 Agent" })).toContainText("购买不包含交易执行授权");
    await buyer.getByRole("checkbox").check();
    await buyer.getByRole("button", { name: "验证并购买", exact: true }).click();
    await cognitoSignIn(buyer, credentials.buyer);
    await expect(buyer.getByRole("heading", { name: "Agent 工作台", exact: true })).toBeVisible();
    me = await (await buyer.request.get("/api/auth/me")).json(); buyerCsrf = me.csrf;
    purchasedId = await buyer.getByLabel("选择 Agent").inputValue();
    expect(purchasedId).not.toBe(source.id);
    const after = await (await buyer.request.get("/api/dashboard")).json();
    const owned = after.agents.find((a: any) => a.id === purchasedId);
    expect(owned.template_id).toBe("portfolio-health");
    expect(after.grants).toEqual(beforeBuyer.grants);
    expect(Number(after.wallet.USDC)).toBe(Number(beforeBuyer.wallet.USDC)-1);
    expect(Number((await (await seller.request.get("/api/dashboard")).json()).wallet.USDC)).toBe(Number(beforeSeller.wallet.USDC)+1);
    await expect(buyer.getByRole("button", { name: "交易下单", exact: true })).toHaveCount(0);
    await buyer.getByRole("region", { name: "当前 Agent 示例动作" }).getByRole("button", { name: "持仓健康检查" }).click();
    await buyer.getByRole("button", { name: "运行任务", exact: true }).click();
    await expect(buyer.getByRole("region", { name: "任务结果" })).toContainText("analyze_portfolio", { timeout: 120_000 });
    const sid = await buyer.evaluate(key => localStorage.getItem(key), `onchain:last-session:${me.user.sub}:${purchasedId}`);
    const conversation = await (await buyer.request.get("/api/conversations/" + sid)).json();
    expect(conversation.messages.at(-1).content.tool_result.steps.map((s: any) => [s.tool, s.status])).toEqual([
      ["get_portfolio", "OK"], ["analyze_portfolio", "OK"],
    ]);
    const run = await (await buyer.request.get(`/api/conversations/${sid}/runs/${conversation.runs.at(-1).id}`)).json();
    expect(run.events.some((e: any) => e.kind === "TOOL_CALL")).toBeTruthy();
    expect((await seller.request.get("/api/conversations/" + sid)).status()).toBe(404);
    expect((await seller.request.delete("/api/agents/" + purchasedId, { headers: { "X-CSRF-Token": sellerMe.csrf } })).status()).toBe(404);
    await buyer.screenshot({ path: "../.deployment/task-agent-workspace.png", fullPage: true });
    await buyer.getByRole("tab", { name: "我的 Agents", exact: true }).click();
    await buyer.screenshot({ path: "../.deployment/agent-management.png", fullPage: true });
    await buyer.getByTestId("agent-" + purchasedId).getByRole("button", { name: "删除 Agent " + title, exact: true }).click();
    await buyer.getByRole("button", { name: "确认删除", exact: true }).click();
    await expect(buyer.getByTestId("agent-" + purchasedId)).toHaveCount(0);
    const deleted = await (await buyer.request.get("/api/conversations/" + sid)).json();
    expect(deleted.can_resume).toBe(false);
    expect(deleted.messages.length).toBe(conversation.messages.length);
    expect((await buyer.request.post(`/api/agents/${purchasedId}/conversations`, { headers: { "X-CSRF-Token": buyerCsrf } })).status()).toBe(410);
    expect((await buyer.request.post(`/api/agents/${purchasedId}/status`, {
      headers: { "X-CSRF-Token": buyerCsrf }, data: { status: "ACTIVE" },
    })).status()).toBe(404);
    expect((await buyer.request.post(`/api/conversations/${sid}/messages`, {
      headers: { "X-CSRF-Token": buyerCsrf }, data: { message: "查看持仓" },
    })).status()).toBe(410);
    const market = await (await buyer.request.get("/api/marketplace")).json();
    expect(market.purchases.find((p: any) => p.agent_id === purchasedId).status).toBe("DELETED");
    purchasedId = "";
    await buyer.getByRole("button", { name: "Agent 市场", exact: true }).click();
    await buyer.screenshot({ path: "../.deployment/task-agent-market.png", fullPage: true });
    console.info("PASS: template creation, task publishing, Cognito purchase, private MCP task, deletion and isolation");
  } finally {
    if (purchasedId && buyerCsrf) await buyer.request.delete("/api/agents/" + purchasedId, { headers: { "X-CSRF-Token": buyerCsrf } });
    await sc.close(); await bc.close();
  }
});
