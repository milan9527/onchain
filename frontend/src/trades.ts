const number = (value: string | number, digits = 8) =>
  Number(value).toLocaleString("en-US", { maximumFractionDigits: digits });

export const isSell = (intent: any) => intent.sell_asset === "ETH" || intent.buy_asset === "USDC";
export const receiveAsset = (intent: any) => isSell(intent) ? "USDC" : "ETH";
export const tradeAmount = (intent: any) => isSell(intent)
  ? `${intent.sell_amount} ETH` : `${number(intent.amount_units / 1e6, 6)} USDC`;
export const tradeTitle = (intent: any) => isSell(intent) ? "卖出 ETH" : "买入 ETH";
