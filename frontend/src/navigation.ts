export type View = "overview" | "workspace" | "market" | "agents" | "templates" | "trading" | "approvals" | "security";
export type Navigation = { view: View; agentId: string; conversationId?: string };

const views: View[] = ["overview", "workspace", "market", "agents", "templates", "trading", "approvals", "security"];

function read(key: string): any {
  try { return JSON.parse(sessionStorage.getItem(key) || "null"); } catch { return null; }
}

function valid(value: any): value is Navigation {
  return value && views.includes(value.view) && typeof value.agentId === "string"
    && (value.conversationId === undefined || typeof value.conversationId === "string");
}

export function navigationFor(owner: string): Navigation | null {
  const value = read("onchain:navigation:" + owner);
  return valid(value) ? value : null;
}

export function rememberNavigation(owner: string, navigation: Navigation) {
  // UI identifiers only. Permissions and conversation ownership still come from the API.
  try { sessionStorage.setItem("onchain:navigation:" + owner, JSON.stringify(navigation)); } catch { /* Optional persistence. */ }
}

export function rememberAuthReturn(owner: string, operationId: string, navigation: Navigation) {
  try {
    sessionStorage.setItem("onchain:auth-return:" + owner,
      JSON.stringify({ ...navigation, operationId, expiresAt: Date.now() + 10 * 60_000 }));
  } catch { /* The operation's server-side conversation remains the fallback. */ }
}

export function takeAuthReturn(owner: string, operationId?: string): Navigation | null {
  const key = "onchain:auth-return:" + owner;
  const value = read(key);
  const expiresAt = value?.expiresAt, savedOperationId = value?.operationId;
  try { sessionStorage.removeItem(key); } catch { /* Optional persistence. */ }
  if (!valid(value) || typeof expiresAt !== "number" || expiresAt <= Date.now()
      || (operationId && savedOperationId !== operationId)) return null;
  return value;
}
