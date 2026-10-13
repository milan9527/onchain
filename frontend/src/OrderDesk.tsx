import { useState } from "react";
import { ArrowUpRight, CircleCheck, ShoppingCart, Wallet } from "lucide-react";
import type { Task } from "./TaskTemplates";
import "./orders.css";

const number = (value: any, digits = 6) => Number(value).toLocaleString("en-US", { maximumFractionDigits: digits });
const statuses: Record<string, string> = {
  PENDING: "待确认", APPROVED: "已批准", EXECUTING: "执行中", CONFIRMED: "已成交",
  REJECTED: "已撤单", CANCELLED: "已取消", EXPIRED: "已过期",
};

export function OrderDesk({ agents, skills, orders, wallet, subscriptions, executions, task, selected,
  onPrepare, onCreate, onReview, onExecute, onCancel, onOpen, onMcp }: {
  agents: any[]; skills: any[]; orders: any[]; wallet: { USDC: string; ETH: string };
  subscriptions: string[]; executions: Record<string, any>; task?: Task; selected: string;
  onPrepare: (agentId: string, task: Task, parameters: any) => Promise<void>;
  onCreate: (task: Task) => void; onReview: (op: any) => Promise<void>;
  onExecute: (op: any) => Promise<void>; onCancel: (op: any) => Promise<void>;
  onOpen: (op: any) => Promise<void>; onMcp: () => void;
}) {
  const eligible = agents.filter(a => a.status === "ACTIVE" && skills.some(s => s.id === a.skill_id
    && [...(task?.tools || []), "execute_trade"].every(tool => s.tools.includes(tool))));
  const agent = eligible.find(a => a.id === selected);
  const [amount, setAmount] = useState("20"), [slippage, setSlippage] = useState("50");
  const [filter, setFilter] = useState("ALL"), [error, setError] = useState(""), [busy, setBusy] = useState(false);
  const missing = task?.products.filter(p => !subscriptions.includes(p)) || [];
  function status(op: any) {
    if (executions[op.id]?.status === "EXECUTING" && op.status === "APPROVED") return "EXECUTING";
    return ["PENDING", "APPROVED"].includes(op.status) && op.expires_at * 1000 <= Date.now() ? "EXPIRED" : op.status;
  }
  const trades = orders.filter(op => op.kind === "TRADE" && op.agent_id === selected);
  const shown = trades.filter(op => filter === "ALL" || status(op) === filter);
  async function perform(action: () => Promise<void>) {
    setBusy(true); setError("");
    try { await action(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  return <div className="order-desk">
    <div className="order-intro"><ShoppingCart size={29} /><div><h2>从下单到成交，全程留有记录</h2><p>填写买入金额 → 核对订单并验证身份 → 自动执行 → 查看成交回执。当前为模拟交易，无需 OKX Key。</p></div></div>
    {error && <p role="alert" className="inline-error">{error}</p>}
    <div className="order-layout"><section className="panel order-ticket" aria-label="买入订单">
      <div className="panel-heading"><h2>买入 ETH</h2><span className="paper-badge">模拟市价单</span></div>
      <div className="order-pair"><strong>USDC <ArrowUpRight size={19} /> ETH</strong><span>Base · 本人模拟账户</span></div>
      {!agent ? <div className="order-empty"><p>当前 Agent 需要处于运行状态并具备交易执行权限才能下单。</p>
        <button className="primary" disabled={!task} onClick={() => task && onCreate(task)}>创建交易 Agent</button></div>
        : <form className="form-stack" onSubmit={e => { e.preventDefault(); if (agent && task) perform(() => onPrepare(agent.id, task, { amount, max_slippage_bps: Number(slippage) })); }}>
          <p className="muted">下单 Agent：<strong>{agent.name}</strong></p>
          <label>买入金额（USDC）<input name="order_amount" type="number" min="0.000001" step="0.000001"
            max={agent.max_trade_units / 1e6} value={amount} onChange={e => setAmount(e.target.value)} required /></label>
          <div className="order-presets">{["5", "20", "100"].map(value => <button key={value} type="button" className="secondary compact"
            disabled={Number(value) > agent.max_trade_units / 1e6 || Number(value) > Number(wallet.USDC)} onClick={() => setAmount(value)}>{value} USDC</button>)}</div>
          <label>最大滑点<select aria-label="最大滑点" value={slippage} onChange={e => setSlippage(e.target.value)}>
            <option value="0">0%</option><option value="50">0.5%</option><option value="100">1%</option>
          </select></label>
          <p className="muted">单笔上限 {number(agent.max_trade_units / 1e6)} USDC · 每日上限 {number(agent.daily_limit_units / 1e6)} USDC。报价与最低接收数量会写入待确认订单。</p>
          {missing.length > 0 && <div className="demo-notice">所需 MCP 尚未启用：{missing.join("、")}。<button type="button" className="text-btn" onClick={onMcp}>前往启用 MCP</button></div>}
          <button className="primary full" disabled={busy || !task || missing.length > 0}>{busy ? "正在准备订单…" : "生成待确认订单"}</button>
          <small className="muted">生成订单不会扣款。核对并通过 Cognito 确认后才会执行；价格超出批准的滑点范围时会阻止成交。</small>
        </form>}
    </section><section className="panel order-account"><Wallet size={24} /><h3>我的模拟账户</h3>
      <dl><dt>可用 USDC</dt><dd>{number(wallet.USDC)} <small>USDC</small></dd><dt>ETH 持仓</dt><dd>{number(wallet.ETH, 8)} <small>ETH</small></dd></dl>
      <div className="order-checks"><p><CircleCheck size={15} />成交后自动更新余额与持仓</p><p><CircleCheck size={15} />每笔订单需要单独确认</p><p><CircleCheck size={15} />待确认或尚未执行的订单可撤回</p></div>
      <p className="muted">账户余额由本人所有 Agent 共用。下方只显示当前 Agent 的订单；模拟成交不签名或广播链上交易。</p>
    </section></div>
    <section className="panel" aria-label="订单历史"><div className="panel-heading"><h2>订单历史 <small>{trades.length}</small></h2>
      <select aria-label="筛选订单状态" className="order-filter" value={filter} onChange={e => setFilter(e.target.value)}>
        <option value="ALL">全部订单</option>{Object.entries(statuses).map(([value, text]) => <option value={value} key={value}>{text}</option>)}
      </select></div>
      {!shown.length ? <p className="muted">暂无符合条件的订单。准备订单后可在这里跟踪状态。</p> : <div className="order-list">{shown.map(op => {
        const current = status(op);
        return <article className="order-record" data-testid={"order-" + op.id} key={op.id}>
          <div className="order-record-title"><div><strong>买入 ETH · {number(op.intent.amount_units / 1e6)} USDC</strong>
            <small>{agents.find(a => a.id === op.agent_id)?.name || "交易 Agent"} · {new Date(op.created_at * 1000).toLocaleString("zh-CN")}</small></div>
            <span className={"pill " + current.toLowerCase()}>{statuses[current] || current}</span></div>
          <small className="order-id">订单号：{op.id}</small>
          {op.execution ? <dl className="order-fill"><dt>成交数量</dt><dd>{number(op.execution.received, 8)} ETH</dd>
            <dt>成交价格</dt><dd>{number(op.execution.price)} USDC / ETH</dd><dt>模拟回执</dt><dd>{op.execution.receipt}</dd></dl>
            : op.intent.reference_price && <p className="muted">参考价 {number(op.intent.reference_price)} USDC / ETH · 最少接收 {number(op.intent.minimum_received, 8)} ETH</p>}
          <div className="row-actions">
            {current === "PENDING" && <button className="primary compact" disabled={busy} onClick={() => perform(() => onReview(op))}>核对并确认</button>}
            {current === "APPROVED" && <button className="primary compact" disabled={busy} onClick={() => perform(() => onExecute(op))}>执行已批准订单</button>}
            {["PENDING", "APPROVED"].includes(current) && <button className="text-btn" disabled={busy} onClick={() => perform(() => onCancel(op))}>撤单</button>}
            {op.conversation_id && <button className="text-btn" disabled={busy} onClick={() => perform(() => onOpen(op))}>查看会话与执行过程<ArrowUpRight size={14} /></button>}
          </div>
        </article>;
      })}</div>}
    </section>
  </div>;
}
