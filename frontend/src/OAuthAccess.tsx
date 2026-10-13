import { useEffect, useState } from "react";
import { ArrowUpRight, Fingerprint, Layers3, LockKeyhole, ShieldCheck } from "lucide-react";
import { api } from "./api";

export function CognitoLogin() {
  const error = new URLSearchParams(window.location.search).get("auth_error");
  const review = new URLSearchParams(window.location.search).get("review_operation");
  return <div className="login-page">
    <div className="login-story"><div className="brand"><span className="brand-icon"><Layers3 /></span><span>onchain<span className="brand-sub">CONTROL</span></span></div>
      <div><span className="eyebrow">YOUR INTENT. YOUR CONTROL.</span><h1>把执行交给 Agent。<br />把控制留给你。</h1><p>独立会话、明确授权、逐次验证。<br />身份与敏感操作统一由 Cognito 验证。</p></div>
      <small>Powered by Amazon Bedrock AgentCore</small></div>
    <div className="login-form-wrap"><div className="login-form"><span className="login-lock"><LockKeyhole size={27} /></span><h2>登录工作空间</h2>
      <p>前往 Cognito 登录、注册或恢复账号。启用验证器后，登录也需要动态验证码。</p>
      {error && <div role="alert" className="inline-error">认证未完成（{error}），请重新登录。</div>}
      <button className="primary full" onClick={() => { window.location.assign("/api/auth/oauth/start" + (review ? "?review_operation=" + encodeURIComponent(review) : "")); }}>通过 Cognito 登录 <ArrowUpRight size={17} /></button>
      <div className="login-note"><ShieldCheck size={15} />密码只提交给 Cognito，不进入平台或模型。</div>
    </div></div>
  </div>;
}

export function McpConnections({ agents }: { agents: { id: string; name: string; status: string }[] }) {
  const [info, setInfo] = useState<any>(null);
  const [agent, setAgent] = useState(agents.find(a => a.status === "ACTIVE")?.id || "");
  const [prepare, setPrepare] = useState(false);
  const [execute, setExecute] = useState(false);
  const [market, setMarket] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const load = () => api("/mcp/connections").then(setInfo);
  useEffect(() => { load().catch(e => setError(e.message)); }, []);
  async function authorize() {
    setBusy(true); setError("");
    try {
      const scopes = ["onchain/mcp.invoke", "onchain/portfolio.read",
        ...(market ? ["onchain/market.read"] : []),
        ...(prepare ? ["onchain/trade.prepare"] : []), ...(execute ? ["onchain/trade.execute"] : [])];
      const result = await api("/mcp/connections", "POST", { client_id: info.client_id, agent_id: agent, scopes });
      window.location.assign(result.authorization_url);
    } catch (e) { setError((e as Error).message); setBusy(false); }
  }
  const active = info?.connection?.active && info.connection.expires_at > Date.now() / 1000;
  return <section className="panel oauth-panel">
    <div className="panel-heading"><h2>外部 MCP · Cognito OAuth</h2><span className="muted">授权码 + PKCE</span></div>
    <p className="muted">为外部客户端选择一个 Agent 和工具权限。连接授权有效期为 24 小时；交易仍需逐笔核对和确认。</p>
    {!info && !error && <p className="muted" role="status">正在读取连接配置…</p>}
    {info && <><dl className="oauth-endpoints"><dt>MCP 地址</dt><dd><code>{info.gateway_url}</code></dd><dt>OAuth Client ID</dt><dd><code>{info.client_id}</code></dd><dt>回调地址</dt><dd><code>{info.redirect_uri}</code></dd></dl>
      <details><summary>接入参数</summary><dl className="oauth-endpoints"><dt>Authorization</dt><dd><code>{info.authorization_endpoint}</code></dd><dt>Token</dt><dd><code>{info.token_endpoint}</code></dd><dt>Resource / audience</dt><dd><code>{info.gateway_url}</code></dd><dt>可申请 scope</dt><dd><code>{info.scopes.join(" ")}</code></dd></dl><p className="muted">使用预注册公共客户端。回调地址须精确匹配；当前不提供动态客户端注册。</p></details>
      {active && <div className="banner success"><ShieldCheck size={18} /><span>已绑定 {agents.find(a => a.id === info.connection.agent_id)?.name || "Agent"} · {info.connection.scopes.join(" · ")}</span>
        <button className="danger-btn" disabled={busy} onClick={async () => { setBusy(true); try { await api("/mcp/connections/" + info.client_id, "DELETE"); await load(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); } }}>撤销连接</button></div>}
      <div className="form-stack"><label>授权给哪个 Agent<select aria-label="MCP 绑定 Agent" value={agent} onChange={e => setAgent(e.target.value)}><option value="" disabled>选择 Agent</option>{agents.filter(a => a.status === "ACTIVE").map(a => <option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
        <span className="muted">默认允许查询持仓、行情和操作记录。</span>
        <label className="checkbox"><input type="checkbox" checked={market} onChange={e => setMarket(e.target.checked)} />允许 链上公共行情、风险和路由研究</label>
        <label className="checkbox"><input type="checkbox" checked={prepare} onChange={e => setPrepare(e.target.checked)} />允许准备交易</label>
        <label className="checkbox"><input type="checkbox" checked={execute} onChange={e => setExecute(e.target.checked)} />允许执行我已明确批准的交易</label>
        <button className="primary" disabled={!agent || busy} onClick={authorize}><Fingerprint size={17} />{busy ? "正在跳转…" : active ? "验证并替换连接授权" : "验证并授权连接"}</button>
      </div></>}
    {error && <p role="alert" className="inline-error">{error}</p>}
  </section>;
}
