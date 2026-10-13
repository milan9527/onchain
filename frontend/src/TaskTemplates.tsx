import { useState } from "react";
import { ArrowUpRight, ClipboardList, LockKeyhole, Play, Search } from "lucide-react";
import { api } from "./api";
import { providerJson, providerText } from "./providerDisplay";
import "./tasks.css";

export type Task = { id: string; name: string; description: string; category: string; skill_id: string;
  mode: string; tools: string[]; products: string[]; availability: string; digest: string;
  trade_side?: "BUY" | "SELL";
  data_mode?: string; examples?: Record<string, Record<string, string | number>>;
  parameters: { properties: Record<string, any>; required: string[] } };
const chains: Record<string, string> = { "8453": "Base", "1": "Ethereum", "42161": "Arbitrum", "56": "BNB Chain", "137": "Polygon", "196": "X Layer", "501": "Solana" };

export function supportsTask(task: Task, skill: any) {
  const side = task.trade_side || (["eth-order", "eth-dca"].includes(task.id) ? "BUY" : undefined);
  return skill && task.tools.every(tool => skill.tools.includes(tool))
    && (!side || (skill.trade_sides || ["BUY"]).includes(side));
}

export function TaskTemplates({ tasks, onChoose }: { tasks: Task[]; onChoose: (task: Task) => void }) {
  const [query, setQuery] = useState(""), [category, setCategory] = useState("全部");
  const shown = tasks.filter(t => (category === "全部" || t.category === category) && `${t.name}${t.description}`.includes(query));
  return <div className="task-library"><div className="task-intro"><ClipboardList size={30} /><div><h2>从一个明确的任务开始</h2><p>选择模板创建专用 Agent。每个模板声明工具、数据来源和授权要求，运行过程会保存在会话中。</p></div></div>
    {tasks.some(t => t.data_mode === "demo") && <div className="demo-notice"><strong>无需 API 密钥 · 完整流程演示</strong><p>链上研究使用平台演示数据，非实时行情。登录验证、Agent 运行、MCP 调用和权限检查正常执行；交易在本人的模拟账户内完成。</p></div>}
    <div className="task-filters"><label className="task-search"><Search size={17} /><input aria-label="搜索任务模板" value={query} onChange={e => setQuery(e.target.value)} placeholder="搜索持仓、代币、风险、路由…" /></label>
      <select aria-label="模板分类" value={category} onChange={e => setCategory(e.target.value)}>{["全部", ...new Set(tasks.map(t => t.category))].map(c => <option key={c}>{c}</option>)}</select></div>
    <div className="task-grid">{shown.map(task => <article className="panel task-card" key={task.id} data-testid={"template-" + task.id}>
      <div className="task-tags"><span className="intent-tag">{task.category}</span><span className={task.data_mode === "demo" || task.availability !== "READY" ? "paper-badge" : "safe-tag"}>{task.availability === "READY" ? task.data_mode === "demo" ? "可运行 · 演示数据" : task.mode === "prepare" ? "下单 · 需确认" : task.mode === "approval" ? "需确认授权" : "可运行" : "待配置实时数据"}</span></div>
      <h3>{task.name}</h3><p>{task.description}</p><div className="tool-tags">{task.tools.map(tool => <code key={tool}>{tool}</code>)}</div>
      <div className="task-card-bottom"><small><LockKeyhole size={12} />{task.mode === "prepare" ? "创建订单 · 确认后成交" : task.mode === "approval" ? "Cognito 确认后启动" : "只读 · 不执行交易"}</small>
        <button className="primary compact" onClick={() => onChoose(task)}>使用模板<ArrowUpRight size={14} /></button></div>
    </article>)}</div>{!shown.length && <p className="muted">没有匹配的任务模板。</p>}
  </div>;
}

export function CreateFromTemplate({ task, products, onCreated }: {
  task: Task; products: any[]; onCreated: (agent: any) => Promise<void>;
}) {
  const [error, setError] = useState(""), [busy, setBusy] = useState(false), [enabled, setEnabled] = useState(true);
  return <form className="form-stack" onSubmit={async e => {
    e.preventDefault(); setBusy(true); setError("");
    const form = Object.fromEntries(new FormData(e.currentTarget));
    try { await onCreated(await api("/agents", "POST", {
      ...form, skill_id: task.skill_id, template_id: task.id, enable_template_products: enabled,
    })); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }}>
    <p>{task.description}</p><label>Agent 名称<input name="name" defaultValue={task.name} maxLength={60} required /></label>
    <div className="form-columns"><label>单笔交易上限（USDC）<input name="max_trade" defaultValue="100" type="number" min="0.01" max="1000" step="0.01" required /></label>
      <label>每日交易上限（USDC）<input name="daily_limit" defaultValue="500" type="number" min="0.01" max="5000" step="0.01" required /></label></div>
    <p className="muted">{task.mode !== "read" ? "这些额度仅是上限，每笔订单或自动交易范围仍需确认授权。" : "只读 Skill 不具有交易执行权限；额度用于交易预检等分析。"}创建将使用 Skill：{task.skill_id}。</p>
    <label className="checkbox"><input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} />启用此模板所需 MCP：{task.products.map(id => products.find(p => p.id === id)?.name || id).join("、")}</label>
    {task.data_mode === "demo" && <p className="demo-notice">此模板使用明确标注的演示数据，无需 API 密钥。创建后可直接使用示例参数运行。</p>}
    {task.availability !== "READY" && <p className="inline-error">此模板需要平台配置实时数据凭据后才能查询实时数据。现在可以先创建 Agent。</p>}
    {error && <p className="inline-error">{error}</p>}<button className="primary full" disabled={busy}>创建模板 Agent</button>
  </form>;
}

export function TaskRunForm({ tasks, initial, onRun }: { tasks: Task[]; initial?: string; onRun: (task: Task, parameters: any) => Promise<void> }) {
  const [chosenId, setId] = useState(initial || "");
  const [error, setError] = useState(""), [busy, setBusy] = useState(false);
  const task = tasks.find(t => t.id === chosenId) || (!chosenId ? tasks[0] : undefined);
  const [values, setValues] = useState<Record<string, string | number>>({});
  if (!task) return <p className="muted">当前 Skill 没有适用的任务模板，请从「任务模板」创建专用 Agent。</p>;
  // Agent/catalog refresh may finish after this dialog mounts. Derive examples from the
  // current task instead of capturing an empty initial render in state.
  const id = task.id, defaults = task.examples?.[String(values.chain || "8453")] || {};
  return <form className="form-stack" onSubmit={async e => {
    e.preventDefault(); setBusy(true); setError("");
    const data = new FormData(e.currentTarget), parameters: Record<string, unknown> = {};
    for (const [key, field] of Object.entries(task.parameters.properties)) {
      parameters[key] = field.type === "integer" ? Number(data.get(key)) : data.get(key);
    }
    try { await onRun(task, parameters); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }}>
    <label>任务模板<select aria-label="选择任务模板" value={id} onChange={e => { setId(e.target.value); setValues(tasks.find(t => t.id === e.target.value)?.examples?.["8453"] || {}); setError(""); }}>{tasks.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}</select></label>
    {task.data_mode === "demo" && <div className="demo-notice"><strong>演示数据 · 非实时行情</strong><p>已填入可运行的示例参数。切换网络会载入该网络的示例；数据集之外的合约将返回未知结果。</p><button type="button" className="secondary compact" onClick={() => setValues(task.examples?.[String(values.chain || "8453")] || {})}>恢复演示参数</button></div>}
    <p>{task.description}</p><div className="form-stack" key={id}>{Object.entries(task.parameters.properties).map(([key, field]) =>
      <label key={key}>{field.title || key}{field.enum ? <select name={key} value={values[key] ?? defaults[key] ?? field.default ?? field.enum[0]} onChange={e => setValues(v => key === "chain" && task.examples ? task.examples[e.target.value] : { ...v, [key]: e.target.value })}>{field.enum.map((v: string) => <option key={v} value={v}>{key === "chain" ? chains[v] || v : v}</option>)}</select>
        : <input name={key} required={task.parameters.required.includes(key)} type={field.type === "integer" ? "number" : "text"}
          value={values[key] ?? defaults[key] ?? field.default ?? ""} onChange={e => setValues(v => ({ ...v, [key]: e.target.value }))} min={field.minimum} max={field.maximum} maxLength={field.maxLength || 80}
          placeholder={key.includes("token") ? "填写该网络上的完整代币合约地址" : undefined} />}</label>)}</div>
    {task.availability !== "READY" && <p className="inline-error">待配置实时数据凭据，当前不能运行实时查询。配置完成后将显示实际数据来源。</p>}
    {error && <p className="inline-error">{error}</p>}
    <button className="primary full" disabled={busy || task.availability !== "READY"}><Play size={16} />{busy ? "正在启动…" : task.mode === "prepare" ? "生成待确认订单" : task.mode === "approval" ? "配置定投授权" : "运行任务"}</button>
    <small className="muted">{task.mode === "prepare" ? "先读取余额与报价，再生成待确认订单；验证身份前不会扣款。" : task.mode === "approval" ? "下一步核对交易范围并通过 Cognito 验证。" : "模板按固定步骤执行；每次工具调用都会重新验证权限。"}</small>
  </form>;
}

export function TaskReport({ result }: { result: any }) {
  const tool = result.tool_result;
  if (tool?.task_id) return <section className="task-report" aria-label="任务结果"><h3>{tool.task_name}</h3>
    {tool.operation ? <p className="demo-notice">模拟下单流程 · 成交状态以订单卡片为准</p> : tool.steps.some((step: any) => step.result?.simulated) && <p className="demo-notice">平台演示数据 · 非实时行情 · 不代表真实合约安全结论</p>}
    {tool.steps.map((step: any, index: number) => <details key={index} open={tool.steps.length <= 2}><summary>{step.tool} · {step.status === "OK" ? "已完成" : "已阻止"}</summary><ToolData data={step.result} /></details>)}</section>;
  if (result.tool && !["get_portfolio", "get_quote", "prepare_trade", "execute_trade", "get_operation"].includes(result.tool) && !tool?.error) {
    return <section className="task-report" aria-label="工具结果"><h3>{result.tool}</h3><ToolData data={tool} /></section>;
  }
  return null;
}

function ToolData({ data }: { data: any }) {
  if (!data) return <p className="muted">没有返回数据。</p>;
  return <div className="task-data">
    {data.simulated && <span className="paper-badge">演示数据 · 非实时行情</span>}
    {data.source && <p className="muted">来源：{providerText(data.source)}</p>}
    {data.total_value_usdc && <p>模拟资产估值：<strong>{Number(data.total_value_usdc).toFixed(2)} USDC</strong> · ETH 占比 {data.eth_weight_percent}%</p>}
    {data.checks && <ul>{data.checks.map((c: any) => <li key={c.name}>{c.name}：{c.passed ? "通过" : "未通过"}</li>)}</ul>}
    {data.notice && <p className="muted">{providerText(data.notice)}</p>}
    <pre>{providerJson(data)}</pre>
  </div>;
}
