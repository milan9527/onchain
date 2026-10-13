import { useState } from "react";
import { ArrowUpRight, Bot, Pause, Play, Plus, Trash2 } from "lucide-react";
import { TaskTemplates, type Task } from "./TaskTemplates";
import "./agents.css";

export function AgentManagement({ agents, skills, tasks, busy, onCreate, onTemplate, onOpen, onStatus, onDelete }: {
  agents: any[]; skills: any[]; tasks: Task[]; busy: boolean;
  onCreate: () => void; onTemplate: (task: Task) => void; onOpen: (agent: any) => void;
  onStatus: (agent: any) => void; onDelete: (agent: any) => Promise<void>;
}) {
  const [templates, setTemplates] = useState(false);
  const [removing, setRemoving] = useState<any>(null);
  const [error, setError] = useState("");
  const [deleting, setDeleting] = useState(false);
  return <>
    <div className="management-toolbar"><div className="market-tabs">
      <button className={!templates ? "active" : ""} onClick={() => setTemplates(false)}>我的 Agents · {agents.length}</button>
      <button className={templates ? "active" : ""} onClick={() => setTemplates(true)}>任务模板</button>
    </div><button className="secondary" onClick={onCreate}><Plus size={16} />自定义创建</button></div>
    {templates ? <TaskTemplates tasks={tasks} onChoose={onTemplate} /> : <>
      <div className="task-intro"><Bot size={30} /><div><h2>一个 Agent，一项明确的任务</h2>
        <p>从模板创建或在市场购买 Agent，在工作台运行任务、查看历史与工具调用。</p></div>
        <button className="primary" onClick={() => setTemplates(true)}>从模板创建</button></div>
      {!agents.length && <div className="panel empty"><h3>创建你的第一个 Agent</h3><p>选择任务模板，所需 Skill 和 MCP 会一并列出。</p></div>}
      <div className="agent-grid">{agents.map(agent => {
        const task = tasks.find(t => t.id === agent.template_id);
        const skill = skills.find(s => s.id === agent.skill_id);
        return <article className="panel managed-agent" key={agent.id} data-testid={"agent-" + agent.id}>
          <div className="agent-card-top"><span className="agent-icon"><Bot size={22} /></span>
            <span className={"pill " + agent.status.toLowerCase()}><i />{agent.status === "ACTIVE" ? "可运行" : "已暂停"}</span></div>
          <h3>{agent.name}</h3><p>{task?.name || skill?.name || agent.skill_id}</p>
          <small className="muted">{agent.market_purchase_id ? "来自 Agent 市场" : "自己创建"} · {skill?.tools.length || 0} 项工具能力</small>
          <div className="tool-tags">{(task?.tools || skill?.tools || []).slice(0, 4).map((tool: string) => <code key={tool}>{tool}</code>)}</div>
          <div className="agent-actions"><button disabled={busy} onClick={() => onOpen(agent)}>
            {agent.status === "ACTIVE" ? "运行 Agent" : "查看工作台"}<ArrowUpRight size={14} /></button>
            <button aria-label={agent.status === "ACTIVE" ? "暂停 Agent" : "恢复 Agent"} disabled={busy} onClick={() => onStatus(agent)}>
              {agent.status === "ACTIVE" ? <Pause size={16} /> : <Play size={16} />}</button>
            <button aria-label={"删除 Agent " + agent.name} disabled={busy} onClick={() => { setError(""); setRemoving(agent); }}><Trash2 size={16} /></button>
          </div>
        </article>;
      })}</div>
    </>}
    {removing && <div className="modal-backdrop"><section className="modal" role="dialog" aria-modal="true" aria-label="删除 Agent">
      <h2>删除「{removing.name}」？</h2><p className="muted">将停止此 Agent，撤销持续授权并取消待执行操作。删除后不能恢复；历史会话、订单与审计记录保留。已发布的市场模板需在卖方中心单独下架。</p>
      {error && <p role="alert" className="inline-error">{error}</p>}
      <div className="row-actions"><button className="secondary" disabled={deleting} onClick={() => setRemoving(null)}>取消</button>
        <button className="danger-btn" disabled={deleting} onClick={async () => {
          setDeleting(true); setError("");
          try { await onDelete(removing); setRemoving(null); } catch (e) { setError((e as Error).message); }
          finally { setDeleting(false); }
        }}>{deleting ? "正在删除…" : "确认删除"}</button></div>
    </section></div>}
  </>;
}

export function WorkspaceActions({ agent, tasks, skills, working, onTask, onOrder }: {
  agent: any; tasks: Task[]; skills: any[]; working: boolean;
  onTask: (task: Task) => void; onOrder: () => void;
}) {
  const skill = skills.find(s => s.id === agent?.skill_id);
  const compatible = tasks.filter(t => skill && t.tools.every(tool => skill.tools.includes(tool)))
    .sort((a, b) => Number(b.id === agent?.template_id) - Number(a.id === agent?.template_id));
  if (!agent) return null;
  return <section className="workspace-actions" aria-label="当前 Agent 示例动作">
    <div><strong>{tasks.find(t => t.id === agent.template_id)?.name || skill?.name || agent.name}</strong>
      <span>选择示例动作，核对参数后运行</span></div>
    {agent.status !== "ACTIVE" && <p className="inline-error">此 Agent 已暂停。请到 Agent 管理恢复后运行。</p>}
    <div className="workspace-examples">{compatible.map(task => <button key={task.id} className="secondary compact"
      disabled={working || agent.status !== "ACTIVE"} onClick={() => task.id === "eth-order" && skill?.tools.includes("execute_trade") ? onOrder() : onTask(task)}>
      {task.name}<ArrowUpRight size={13} /></button>)}</div>
    {!compatible.length && <p className="muted">可用工具：{skill?.tools.join("、") || "暂无"}。可以在下方描述任务。</p>}
  </section>;
}
