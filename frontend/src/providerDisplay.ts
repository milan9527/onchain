// Normalize historical provider labels for display without changing stored audit data or signed intents.
export function providerText(value: string): string {
  return value.replace(/OKX_/g, "PROVIDER_")
    .replace(/\bOKX(?: Onchain OS)?\b|Onchain OS/gi, "链上数据服务");
}

export function providerJson(value: unknown): string {
  const labels = new Set(["provider", "source", "notice", "message", "code", "preview"]);
  return JSON.stringify(value, (key, item) =>
    labels.has(key) && typeof item === "string" ? providerText(item) : item, 2);
}
