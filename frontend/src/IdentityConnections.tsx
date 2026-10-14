import { useEffect, useState } from "react";
import { Fingerprint, ShieldCheck } from "lucide-react";
import { api } from "./api";

const returnKey = (owner: string) => "onchain:identity-return:" + owner;

export function identityReturn(owner: string): string {
  try {
    const saved = JSON.parse(sessionStorage.getItem(returnKey(owner)) || "null");
    return saved?.expiresAt > Date.now() ? saved.resume_id : "";
  } catch { return ""; }
}

export function clearIdentityReturn(owner: string) {
  sessionStorage.removeItem(returnKey(owner));
}

export function IdentityAuthorization({ error }: { error: { message?: string; resume_id?: string } }) {
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState("");
  async function authorize() {
    setBusy(true); setFailure("");
    try {
      const { user } = await api("/auth/me");
      const result = await api("/identity/authorize/" + error.resume_id, "POST");
      sessionStorage.setItem(returnKey(user.sub), JSON.stringify({ resume_id: error.resume_id, expiresAt: Date.now() + 600_000 }));
      window.location.assign(result.authorization_url);
    } catch (e) { setFailure((e as Error).message); setBusy(false); }
  }
  return <div className="operation-card">
    <strong><Fingerprint size={18} /> 连接工具账户</strong>
    <p>请使用当前登录账号完成工具授权。返回后会继续此会话的任务；交易仍需要单独确认。</p>
    {error.resume_id && <button className="primary compact" disabled={busy} onClick={authorize}>{busy ? "正在跳转…" : "授权并继续任务"}</button>}
    {failure && <p className="inline-error" role="alert">{failure}</p>}
  </div>;
}

export function IdentityConnections() {
  const [info, setInfo] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const load = () => api("/identity/connections").then(setInfo);
  useEffect(() => { load().catch(e => setError(e.message)); }, []);
  async function change(source: string, revoke: boolean) {
    setBusy(true); setError(""); setNotice("");
    try {
      if (revoke) { await api("/identity/connections/" + source, "DELETE"); await load(); }
      else {
        const { user } = await api("/auth/me");
        const result = await api("/identity/connections/" + source, "POST");
        clearIdentityReturn(user.sub);
        if (result.authorization_url) window.location.assign(result.authorization_url);
        else { setNotice(result.message); await load(); }
      }
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  if (info && !info.enabled) return null;
  return <section className="panel oauth-panel">
    <div className="panel-heading"><h2>工具账户连接</h2><ShieldCheck size={20} /></div>
    <p className="muted">授权 Agent 使用本人账户访问工具。凭证由 AgentCore Identity 保管，所有调用仍受会话、Skill 和交易授权限制。撤销后会阻止后续调用，包括已有凭证的调用。</p>
    {(["internal", "external"] as const).map(source => {
      const record = info?.connections?.[source];
      return <div className="panel-heading" key={source}>
        <div><strong>{source === "internal" ? "平台内 Agent" : "外部 MCP 客户端"}</strong><p className="muted">{record?.pending ? "等待完成授权" : record?.active && record?.connected ? "已授权" : record?.active === false ? "已撤销" : "等待授权"}</p></div>
        <div className="button-row">
          <button className="secondary compact" disabled={!info || busy} onClick={() => change(source, false)}>{record?.active === false ? "恢复工具访问" : record?.connected ? "检查连接" : source === "external" ? "启用工具访问" : "连接账户"}</button>
          {(record?.active || record?.pending) && <button className="danger-btn" disabled={busy} onClick={() => change(source, true)}>撤销工具授权</button>}
        </div>
      </div>;
    })}
    {error && <p className="inline-error" role="alert">{error}</p>}
    {notice && <p role="status">{notice}</p>}
  </section>;
}
