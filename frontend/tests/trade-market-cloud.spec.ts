import { test, expect } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { cognitoLogin, cognitoSignIn } from "./cognito";

test("seller publishes buy/sell Agents; buyer purchases both and confirms a round trip through AgentCore", async ({ browser }) => {
  test.skip(!process.env.TEST_BASE_URL?.startsWith("https://"), "Requires deployed Cognito and AgentCore");
  test.setTimeout(540_000);
  const credentials = JSON.parse(fs.readFileSync(path.resolve("../.deployment/market-credentials.json"), "utf8"));
  const sc = await browser.newContext(), bc = await browser.newContext();
  const seller = await sc.newPage(), buyer = await bc.newPage();
  const purchased: string[] = [];
  try {
    await cognitoLogin(seller, credentials.seller);
    await cognitoLogin(buyer, credentials.buyer);
    const beforeBuyer = await (await buyer.request.get("/api/dashboard")).json();
    const beforeSeller = await (await seller.request.get("/api/dashboard")).json();
    let previousSession = "";
    for (const side of ["BUY", "SELL"]) {
      const template = side === "BUY" ? "eth-buy" : "eth-sell";
      const title = side === "BUY" ? "示例 · ETH 买入 Agent" : "示例 · ETH 卖出 Agent";
      await seller.getByRole("button", { name: "Agent 市场", exact: true }).click();
      let listing = (await (await seller.request.get("/api/marketplace")).json()).listings.find((l: any) => l.is_mine && l.title === title);
      if (!listing) {
        await seller.getByRole("button", { name: "发布 Agent", exact: true }).click();
        await seller.getByRole("combobox", { name: "来源 Agent", exact: true }).selectOption("new");
        await seller.getByRole("combobox", { name: "发布任务模板", exact: true }).selectOption(template);
        await seller.getByLabel("市场名称", { exact: true }).fill(title);
        await seller.getByLabel("发布者名称", { exact: true }).fill("平台交易示例");
        await seller.getByLabel("任务介绍", { exact: true }).fill(side === "BUY"
          ? "使用买方自己的 USDC 买入 ETH，先核对订单并完成 Cognito 身份验证，再执行模拟成交。"
          : "卖出买方已有 ETH 持仓换回 USDC，检查持仓、额度与滑点，每笔订单确认后执行，不允许做空。");
        await seller.getByLabel("一次性服务费（模拟 USDC）", { exact: true }).fill("1");
        await expect(seller.getByLabel("发布预览")).toContainText(title);
        await seller.getByRole("button", { name: "确认发布", exact: true }).click();
        await expect(seller.getByRole("dialog", { name: "发布 Agent", exact: true })).toHaveCount(0);
        listing = (await (await seller.request.get("/api/marketplace")).json()).listings.find((l: any) => l.is_mine && l.title === title);
      }
      expect(listing.agent_type).toBe(side);
      expect(listing.strategy).toBe("TASK");
      await buyer.getByRole("button", { name: "Agent 市场", exact: true }).click();
      await buyer.getByRole("button", { name: side === "BUY" ? "买入 Agent" : "卖出 Agent", exact: true }).click();
      await buyer.getByTestId("listing-" + listing.id).getByRole("button", { name: "购买 Agent", exact: true }).click();
      await buyer.getByRole("button", { name: "核对购买内容", exact: true }).click();
      await expect(buyer.getByRole("dialog")).toContainText("购买不包含交易执行授权");
      await buyer.getByRole("checkbox").check();
      await buyer.getByRole("button", { name: "验证并购买", exact: true }).click();
      await cognitoSignIn(buyer, credentials.buyer);
      await expect(buyer.getByRole("heading", { name: "Agent 工作台", exact: true })).toBeVisible();
      const aid = await buyer.getByLabel("选择 Agent").inputValue();
      purchased.push(aid);
      let me = await (await buyer.request.get("/api/auth/me")).json();
      const purchasedState = await (await buyer.request.get("/api/dashboard")).json();
      expect(purchasedState.agents.find((a: any) => a.id === aid).template_id).toBe(template);
      expect(purchasedState.grants).toEqual(beforeBuyer.grants);
      if (side === "SELL") {
        const probe = await buyer.request.post("/api/security/probe", {
          headers: { "X-CSRF-Token": me.csrf },
          data: { agent_id: aid, tool: "prepare_trade", arguments: {
            amount: "1.00000000000000000000000000001", buy_asset: "USDC", chain: "8453", max_slippage_bps: 50,
          } },
        });
        expect((await probe.json()).decision).toBe("DENY");
      }
      await buyer.getByRole("button", { name: "交易下单", exact: true }).click();
      await expect(buyer.getByRole("region", { name: side === "BUY" ? "买入订单" : "卖出订单" })).toBeVisible();
      await buyer.getByLabel(side === "BUY" ? "买入金额（USDC）" : "卖出数量（ETH）").fill(side === "BUY" ? "30" : "0.005");
      await buyer.getByRole("button", { name: "生成待确认订单", exact: true }).click();
      await expect(buyer.getByRole("button", { name: "查看操作详情", exact: true })).toBeVisible({ timeout: 120_000 });
      const sid = await buyer.evaluate(key => localStorage.getItem(key), `onchain:last-session:${me.user.sub}:${aid}`);
      const state = await (await buyer.request.get("/api/dashboard")).json();
      const op = state.operations.find((o: any) => o.conversation_id === sid && o.status === "PENDING");
      expect(op.intent.buy_asset).toBe(side === "BUY" ? "ETH" : "USDC");
      expect(state.wallet).toEqual(purchasedState.wallet);
      const conversation = await (await buyer.request.get("/api/conversations/" + sid)).json();
      const run = await (await buyer.request.get(`/api/conversations/${sid}/runs/${conversation.runs.at(-1).id}`)).json();
      expect(run.events.some((e: any) => e.kind === "TOOL_CALL")).toBeTruthy();
      expect(run.events.some((e: any) => e.kind === "GATEWAY_ALLOWED")).toBeTruthy();
      expect(conversation.runtime_session_id).toBeUndefined();
      expect(sid).not.toBe(previousSession);
      previousSession = sid!;
      expect((await seller.request.get("/api/conversations/" + sid)).status()).toBe(404);
      expect((await seller.request.get("/api/operations/" + op.id)).status()).toBe(404);
      expect((await buyer.request.post(`/api/operations/${op.id}/execute`, { headers: { "X-CSRF-Token": me.csrf } })).status()).toBe(409);
      await buyer.getByRole("button", { name: "查看操作详情", exact: true }).click();
      await expect(buyer.getByRole("dialog")).toContainText(side === "BUY" ? "USDC → ETH" : "ETH → USDC");
      await buyer.getByRole("checkbox").check();
      await buyer.getByRole("button", { name: "验证并执行", exact: true }).click();
      await cognitoSignIn(buyer, credentials.buyer);
      await expect(buyer.getByLabel("选择 Agent")).toHaveValue(aid);
      await expect(buyer.getByTestId("operation-" + op.id).getByText("已完成", { exact: true })).toBeVisible({ timeout: 120_000 });
      const after = await (await buyer.request.get("/api/dashboard")).json();
      const fill = after.operations.find((o: any) => o.id === op.id);
      expect(fill.execution.received_asset).toBe(side === "BUY" ? "ETH" : "USDC");
      expect(Number(after.wallet.USDC)).toBe(Number(purchasedState.wallet.USDC) + (side === "BUY" ? -30 : 15));
      expect(Number(after.wallet.ETH)).toBeCloseTo(Number(purchasedState.wallet.ETH) + (side === "BUY" ? 0.01 : -0.005), 12);
      if (side === "SELL") {
        me = await (await buyer.request.get("/api/auth/me")).json();
        const replay = await buyer.request.post(`/api/operations/${op.id}/execute`, { headers: { "X-CSRF-Token": me.csrf } });
        expect(replay.status()).toBe(202);
        const job = await replay.json();
        await expect.poll(async () => (await (await buyer.request.get(`/api/conversations/${job.conversation_id}`)).json()).runs
          .find((r: any) => r.id === job.run_id)?.status, { timeout: 100_000 }).toBe("SUCCEEDED");
        expect((await (await buyer.request.get("/api/dashboard")).json()).wallet).toEqual(after.wallet);
        const headers = { "X-CSRF-Token": me.csrf };
        const chat = await (await buyer.request.post(`/api/agents/${aid}/conversations`, { headers })).json();
        const message = await buyer.request.post(`/api/conversations/${chat.id}/messages`, {
          headers, data: { message: "在 Base 上卖出 0.001 ETH 换成 USDC，仅准备订单供我确认。" },
        });
        expect(message.status()).toBe(202);
        await expect.poll(async () => (await (await buyer.request.get(`/api/conversations/${chat.id}`)).json()).runs
          .at(-1)?.status, { timeout: 150_000 }).toBe("SUCCEEDED");
        const completed = await (await buyer.request.get(`/api/conversations/${chat.id}`)).json();
        const proposal = completed.messages.at(-1).content;
        expect(proposal.intent.action).toBe("prepare_trade");
        expect(proposal.intent.trade).toMatchObject({ amount: "0.001", buy_asset: "USDC" });
        expect(proposal.tool_result.operation.status).toBe("PENDING");
        expect((await buyer.request.post(`/api/operations/${proposal.tool_result.operation.id}/reject`, { headers })).ok()).toBeTruthy();
        expect((await (await buyer.request.get("/api/dashboard")).json()).wallet).toEqual(after.wallet);
      }
      await buyer.getByRole("button", { name: "交易下单", exact: true }).click();
      await expect(buyer.getByTestId("order-" + op.id)).toContainText("已成交");
      await buyer.screenshot({ path: `../.deployment/market-${side.toLowerCase()}-completed.png`, fullPage: true });
    }
    const afterBuyer = await (await buyer.request.get("/api/dashboard")).json();
    const afterSeller = await (await seller.request.get("/api/dashboard")).json();
    expect(Number(afterBuyer.wallet.USDC)).toBe(Number(beforeBuyer.wallet.USDC) - 17);
    expect(Number(afterSeller.wallet.USDC)).toBe(Number(beforeSeller.wallet.USDC) + 2);
    await buyer.getByRole("tab", { name: "我的 Agents", exact: true }).click();
    await expect(buyer.getByRole("navigation", { name: "主要功能" }).getByRole("button")).toHaveCount(3);
    for (const aid of purchased) {
      const card = buyer.getByTestId("agent-" + aid);
      await card.getByRole("button", { name: "暂停 Agent", exact: true }).click();
      await expect(card).toContainText("已暂停");
      await card.getByRole("button", { name: "恢复 Agent", exact: true }).click();
      await expect(card).toContainText("可运行");
    }
    await buyer.evaluate(() => window.scrollTo(0, 0));
    await buyer.screenshot({ path: "../.deployment/unified-agent-workspace.png", fullPage: true });
    await seller.getByRole("tab", { name: "卖方中心", exact: true }).click();
    await seller.screenshot({ path: "../.deployment/trade-agent-seller.png", fullPage: true });
    await buyer.getByRole("button", { name: "Agent 市场", exact: true }).click();
    await expect(buyer.getByRole("heading", { name: "示例 · ETH 卖出 Agent", exact: true })).toBeVisible();
    await buyer.evaluate(() => window.scrollTo(0, 0));
    await buyer.screenshot({ path: "../.deployment/trade-agent-market.png", fullPage: true });
    await buyer.setViewportSize({ width: 390, height: 844 });
    await buyer.screenshot({ path: "../.deployment/trade-agent-market-mobile.png", fullPage: true });
    expect(await buyer.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    console.info("PASS: seller UI publishing, market filters, two Cognito purchases, confirmed buy/sell fills, replay, private sessions, unified workspace.");
  } finally {
    if (purchased.length) {
      const me = await (await buyer.request.get("/api/auth/me")).json();
      for (const aid of purchased) {
        const response = await buyer.request.delete("/api/agents/" + aid, { headers: { "X-CSRF-Token": me.csrf } });
        expect(response.ok()).toBeTruthy();
      }
    }
    await sc.close(); await bc.close();
  }
});
