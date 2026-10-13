import { useEffect, useRef, useState } from "react";
import { ArrowUpRight, Bot, Check, Clock3, Plus, Search, Store, Wallet, X } from "lucide-react";
import { api } from "./api";
import type { Task } from "./TaskTemplates";
import "./marketplace.css";

type Agent = { id: string; name: string; skill_id: string; status: string; template_id?: string };
type Listing = {
  id: string; title: string; publisher: string; description: string; price_units: number;
  strategy?: string; task_name?: string; task_mode?: string; template_id?: string; tools?: string[]; products?: string[];
  max_trade_units: number; daily_limit_units: number; sales_count: number; status: string; is_mine: boolean;
};
type Order = { id: string; title: string; publisher: string; price_units: number; status: string;
  strategy?: string; agent_id?: string; grant_id?: string; expires_at?: number; created_at: number };
type Market = { listings: Listing[]; purchases: Order[]; sales: Order[]; revenue_units: number };
const money = (units: number) => (units / 1e6).toLocaleString("en-US", { maximumFractionDigits: 6 });

export function PurchaseSummary({ intent }: { intent: any }) {
  return <div className="approval-summary"><span className="paper-badge">模拟购买 · Agent 服务</span>
    <h3>{intent.title}</h3><p>发布者：{intent.publisher}</p>
    <div className="approval-amount">{money(intent.price_units)}<small>USDC 服务费 / 次</small></div>
    {intent.strategy === "TASK" ? <><dl><dt>任务模板</dt><dd>{intent.task_name}</dd><dt>任务类型</dt><dd>{intent.task_mode === "prepare" ? "准备订单，成交需另行确认" : "只读任务"}</dd><dt>将启用 MCP</dt><dd>{intent.products?.join("、")}</dd><dt>Skill</dt><dd>{intent.skill_id}</dd></dl>
      <div className="tool-tags">{intent.tools?.map((tool: string) => <code key={tool}>{tool}</code>)}</div><p className="muted">验证后支付一次性模拟服务费，创建你独有的 Agent。在工作台选择示例动作并运行任务；购买不包含交易执行授权。服务费不退还。</p></> : <><dl><dt>自动交易策略</dt><dd>Base · 定期用 USDC 买入 ETH</dd>
      <dt>每笔金额</dt><dd>{money(intent.amount_units)} USDC</dd>
      <dt>交易累计上限</dt><dd>{money(intent.total_units)} USDC</dd>
      <dt>每日上限</dt><dd>{money(intent.daily_limit_units)} USDC</dd>
      <dt>执行间隔</dt><dd>{intent.interval_seconds / 60} 分钟</dd>
      <dt>最大滑点</dt><dd>{intent.max_slippage_bps / 100}%</dd>
      <dt>授权到期</dt><dd>{new Date(intent.expires_at * 1000).toLocaleString("zh-CN")}</dd></dl>
    <p className="muted">验证后支付一次性模拟服务费，并创建你独有的 Agent。首笔交易进入自动调度，后续在授权范围内执行。可随时停止自动交易；服务费不退还。卖方下架不影响已购买的策略版本。</p></>}
  </div>;
}

export function AgentMarketplace({ userId, agents, grants, tasks, skills, onReview, onOpen, onChanged }: {
  userId: string; agents: Agent[]; grants: any[]; tasks: Task[]; skills: any[];
  onReview: (op: any) => void; onOpen: (id: string) => void; onChanged: () => Promise<void>;
}) {
  const [data, setData] = useState<Market | null>(null);
  const [tab, setTab] = useState<"discover" | "purchases" | "seller">("discover");
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [publish, setPublish] = useState(false);
  const [buy, setBuy] = useState<Listing | null>(null);
  const active = useRef(true);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  async function reload() { const value = await api<Market>("/marketplace"); if (active.current) setData(value); }
  useEffect(() => {
    let active = true;
    const load = async () => {
      try { const value = await api<Market>("/marketplace"); if (active) setData(value); }
      catch (e) { if (active) setError((e as Error).message); }
    };
    load();
    const timer = setInterval(load, 7000);
    return () => { active = false; clearInterval(timer); };
  }, [userId]);
  async function act(fn: () => Promise<unknown>) {
    setBusy(true); setError("");
    try { await fn(); if (!active.current) return; await reload(); await onChanged(); } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  const own = data?.listings.filter(l => l.is_mine) || [];
  const listings = (data?.listings || []).filter(l => l.status === "PUBLISHED"
    && `${l.title} ${l.publisher} ${l.description}`.toLowerCase().includes(query.toLowerCase()));
  return <div className="agent-market">
    <div className="market-hero"><div><span className="hero-tag"><Store size={15} /> AGENT MARKETPLACE</span>
      <h2>找到适合任务的 Agent。</h2><p>卖家发布任务与能力，买家购买独立实例。研究、分析、下单与定投，在各自工作台完成。</p>
      <div className="market-promises"><span><Check size={14} /> 独立 Agent 与会话</span><span><Check size={14} /> 买卖双方订单</span><span><Check size={14} /> 随时停止自动执行</span></div>
    </div><div className="market-hero-stat"><Bot size={30} /><strong>{data?.listings.filter(l => l.status === "PUBLISHED").length || 0}</strong><span>在售任务 Agent</span></div></div>
    <div className="market-toolbar"><div className="market-tabs" role="tablist" aria-label="Agent 市场视图">
      {([["discover", "发现 Agent"], ["purchases", "我购买的"], ["seller", "卖方中心"]] as const).map(([id, title]) =>
        <button role="tab" aria-selected={tab === id} className={tab === id ? "active" : ""} key={id} onClick={() => setTab(id)}>{title}</button>)}
    </div><button className="primary" onClick={() => setPublish(true)}><Plus size={16} />发布 Agent</button></div>
    {error && <div role="alert" className="banner error">{error}</div>}
    {!data ? <p className="muted">正在加载市场…</p> : tab === "discover" ? <>
      <label className="market-search"><Search size={17} /><input aria-label="搜索市场 Agent" placeholder="搜索任务或发布者" value={query} onChange={e => setQuery(e.target.value)} /></label>
      <div className="market-listings">{listings.map(l => <article className="panel listing-card" key={l.id} data-testid={"listing-" + l.id}>
        <div className="listing-top"><span className="agent-icon"><Bot size={24} /></span><span className="paper-badge">{l.strategy === "TASK" ? l.task_mode === "prepare" ? "模拟下单" : "研究与分析" : "模拟 · ETH 定投"}</span></div>
        <h3>{l.title}</h3><span className="muted">由 {l.publisher} 发布{l.is_mine ? " · 你的发布" : ""}</span><p>{l.description}</p>
        <div className="listing-limits">{l.strategy === "TASK" ? <span>任务模板<b>{l.task_name}</b></span> : <><span>单笔上限<b>{money(l.max_trade_units)} USDC</b></span><span>每日上限<b>{money(l.daily_limit_units)} USDC</b></span></>}</div>
        <div className="listing-footer"><div><strong>{money(l.price_units)} <small>USDC</small></strong><span>一次性服务费 · 已售 {l.sales_count}</span></div>
          <button className="primary compact" disabled={l.is_mine} onClick={() => setBuy(l)}>{l.is_mine ? "我的发布" : l.strategy === "TASK" ? "购买 Agent" : "购买并授权"}<ArrowUpRight size={14} /></button></div>
      </article>)}</div>
      {!listings.length && <div className="panel empty"><Store size={32} /><h3>市场等待第一位发布者</h3><p>发布任务 Agent，让其他用户购买独立实例完成任务。</p></div>}
    </> : tab === "purchases" ? <div className="market-orders">
      {!data.purchases.length && <div className="panel empty"><Bot /><h3>还没有购买的 Agent</h3><p>在市场选择任务，确认服务费与能力范围后开始运行。</p></div>}
      {data.purchases.map(order => {
        const grant = grants.find(g => g.id === order.grant_id);
        const status = order.status === "DELETED" ? "已删除" : order.status === "REVOKED" || grant?.status === "REVOKED" ? "已停止"
          : order.expires_at && order.expires_at * 1000 <= Date.now() ? "已到期"
          : grant && grant.used_units >= grant.total_units ? "额度已用完"
          : agents.find(a => a.id === order.agent_id)?.status === "PAUSED" ? "已暂停" : order.strategy === "TASK" ? "可运行任务" : "自动运行中";
        return <article className="panel market-order" key={order.id}><div><span className="safe-tag">{status}</span><h3>{order.title}</h3><p className="muted">{order.publisher} · 服务费 {money(order.price_units)} USDC</p>
          {grant && <p>已交易 {money(grant.used_units)} / {money(grant.total_units)} USDC · 每 {grant.interval_seconds / 60} 分钟 {money(grant.amount_units)} USDC</p>}
          <small className="muted">订单 {order.id}</small></div><div className="row-actions"><button className="secondary" disabled={!agents.some(a => a.id === order.agent_id)} onClick={() => onOpen(order.agent_id!)}>查看 Agent 与运行记录</button>
          {!["REVOKED", "DELETED"].includes(order.status) && <button className="danger-btn" disabled={busy} onClick={() => act(() => api(`/marketplace/purchases/${order.id}/revoke`, "POST"))}>{order.strategy === "TASK" ? "停止任务 Agent" : "停止自动交易"}</button>}</div></article>;
      })}
    </div> : <>
      <div className="stats-grid market-stats"><div className="stat"><span><Wallet size={18} /> 模拟服务收入</span><strong>{money(data.revenue_units)}<small>USDC</small></strong></div>
        <div className="stat"><span>服务订单</span><strong>{data.sales.length}</strong></div><div className="stat"><span>我的发布</span><strong>{own.length}</strong></div></div>
      <section className="panel"><div className="panel-heading"><h3>我的上架 Agent</h3><span className="muted">下架停止新销售，已售版本继续按买方授权运行</span></div>
        {!own.length && <p className="muted">选择自己的 Agent 和任务模板，填写介绍与价格即可发布。</p>}
        {own.map(l => <div className="market-seller-row" key={l.id}><div><strong>{l.title}</strong><p className="muted">{money(l.price_units)} USDC · 已售 {l.sales_count} · {l.status === "PUBLISHED" ? "在售" : "已下架"}</p></div>
          <button className="secondary" disabled={busy} onClick={() => act(() => api(`/marketplace/listings/${l.id}/status`, "POST", { status: l.status === "PUBLISHED" ? "WITHDRAWN" : "PUBLISHED" }))}>{l.status === "PUBLISHED" ? "下架" : "重新上架"}</button></div>)}</section>
      <section className="panel"><div className="panel-heading"><h3>销售订单</h3><span className="muted">订单不包含买方私有会话或持仓</span></div>
        {!data.sales.length && <p className="muted">首笔购买完成后，收入与订单会出现在这里。</p>}
        {data.sales.map(o => <div className="market-seller-row" key={o.id}><div><strong>{o.title}</strong><p className="muted">{o.id}</p></div><span>+{money(o.price_units)} USDC · 已收款</span></div>)}</section>
    </>}
    {publish && <MarketModal title="发布 Agent" close={() => setPublish(false)}><PublishForm agents={agents} tasks={tasks} skills={skills} onPublish={async body => {
      await api("/marketplace/listings", "POST", body); await reload(); setPublish(false); setTab("seller");
    }} /></MarketModal>}
    {buy && <MarketModal title={"购买 " + buy.title} close={() => setBuy(null)}><BuyForm listing={buy} onPrepare={async body => {
      const operation = await api(`/marketplace/listings/${buy.id}/purchase`, "POST", body);
      if (!active.current) return;
      setBuy(null); await onChanged(); if (active.current) onReview(operation);
    }} /></MarketModal>}
  </div>;
}

function MarketModal({ title, close, children }: { title: string; close: () => void; children: React.ReactNode }) {
  return <div className="modal-backdrop"><section role="dialog" aria-modal="true" aria-label={title} className="modal">
    <div className="modal-heading"><h2>{title}</h2><button className="icon-btn" aria-label="关闭" onClick={close}><X size={20} /></button></div>{children}</section></div>;
}

function PublishForm({ agents, tasks, skills, onPublish }: { agents: Agent[]; tasks: Task[]; skills: any[]; onPublish: (body: any) => Promise<void> }) {
  const compatible = (a: Agent) => tasks.filter(t => skills.find(s => s.id === a.skill_id)?.tools && t.tools.every(tool => skills.find(s => s.id === a.skill_id).tools.includes(tool)));
  const eligible = agents.filter(a => a.status === "ACTIVE" && compatible(a).length > 0);
  const [agentId, setAgentId] = useState(eligible[0]?.id || "");
  const agent = eligible.find(a => a.id === agentId) || eligible[0];
  const available = agent ? compatible(agent) : [];
  const preferred = agent?.template_id || (agent?.skill_id === "controlled-trader" ? "eth-dca" : available[0]?.id);
  const [error, setError] = useState(""), [busy, setBusy] = useState(false);
  return <form className="form-stack" onSubmit={async e => {
    e.preventDefault(); setBusy(true); setError("");
    try { await onPublish(Object.fromEntries(new FormData(e.currentTarget))); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }}>
    <p className="muted">发布当前 Agent 支持的已审核任务模板。买方获得独立 Agent，使用自己的会话、数据和授权。</p>
    <label>来源 Agent<select name="agent_id" value={agent?.id || ""} onChange={e => setAgentId(e.target.value)} required>{eligible.map(a => <option value={a.id} key={a.id}>{a.name}</option>)}</select></label>
    <label>发布任务模板<select name="template_id" key={agent?.id} defaultValue={preferred} required>{available.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}</select></label>
    {!eligible.length && <p className="inline-error">请先在 Agent 管理中从任务模板创建一个可运行的 Agent。</p>}
    <label>市场名称<input name="title" minLength={2} maxLength={60} required placeholder="例如：ETH 稳健定投助手" /></label>
    <label>发布者名称<input name="publisher" minLength={2} maxLength={40} required /></label>
    <label>任务介绍<textarea name="description" minLength={10} maxLength={600} rows={3} required placeholder="介绍任务目标、适用场景和能力边界。请勿承诺收益。" /></label>
    <label>一次性服务费（模拟 USDC）<input name="price" type="number" min="0.01" max="1000" step="0.01" defaultValue="5" required /></label>
    <p className="muted">发布后价格和策略版本固定。修改方案需发布新条目，已售策略保持原版本。</p>
    {error && <p className="inline-error">{error}</p>}<button className="primary full" disabled={busy || !eligible.length}>确认发布</button>
  </form>;
}

function BuyForm({ listing, onPrepare }: { listing: Listing; onPrepare: (body: any) => Promise<void> }) {
  const [error, setError] = useState(""), [busy, setBusy] = useState(false);
  return <form className="form-stack" onSubmit={async e => {
    e.preventDefault(); setBusy(true); setError("");
    const values = Object.fromEntries(new FormData(e.currentTarget));
    try { await onPrepare(listing.strategy === "TASK" ? {} : { ...values, interval_seconds: Number(values.interval_seconds), hours: Number(values.hours) }); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }}>
    <p>一次性模拟服务费 <strong>{money(listing.price_units)} USDC</strong> · 发布者 {listing.publisher}</p>
    {listing.strategy === "TASK" ? <><p>任务：<strong>{listing.task_name}</strong></p><div className="tool-tags">{listing.tools?.map(tool => <code key={tool}>{tool}</code>)}</div>
      <p className="muted">将启用 MCP：{listing.products?.join("、")}。购买后在工作台运行任务。若任务需要下单，每笔交易仍须独立确认。</p></> : <><div className="form-columns"><label>每笔交易（USDC）<input name="amount" type="number" min="0.01" max={listing.max_trade_units / 1e6} step="0.01" defaultValue={Math.min(20, listing.max_trade_units / 1e6)} required /></label>
      <label>交易累计上限（USDC）<input name="total" type="number" min="0.01" max="100000" step="0.01" defaultValue="100" required /></label></div>
    <label>每日交易上限（USDC）<input name="daily_limit" type="number" min="0.01" max={listing.daily_limit_units / 1e6} step="0.01" defaultValue={Math.min(100, listing.daily_limit_units / 1e6)} required /></label>
    <div className="form-columns"><label>执行间隔<select name="interval_seconds" defaultValue="3600"><option value="300">每 5 分钟</option><option value="3600">每小时</option><option value="86400">每天</option></select></label>
      <label>授权有效期（小时）<input name="hours" type="number" min="1" max="720" defaultValue="24" required /></label></div>
    <p className="muted"><Clock3 size={14} /> 完成 Cognito 验证后，首笔交易将由调度器启动。服务费与交易额度分别计算，可随时停止后续交易。</p></>}
    {error && <p className="inline-error">{error}</p>}<button className="primary full" disabled={busy}>{listing.strategy === "TASK" ? "核对购买内容" : "核对订单与自动交易授权"}</button>
  </form>;
}
