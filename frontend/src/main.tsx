import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  ArrowUpRight, Bot, Check, ChevronRight, CircleCheck, Fingerprint, Layers3,
  LayoutDashboard, LockKeyhole, LogOut, MessageSquare, Pause, Play, Plus, Send,
  ShieldCheck, ShieldAlert, Wallet, X, RefreshCw, Boxes, Activity, Download,
  KeyRound, Clock3, SlidersHorizontal, Store, ClipboardList, ShoppingCart,
} from "lucide-react";
import { api, setCsrf, ApiError } from "./api";
import { AccountAccess, MfaSettings } from "./AccountAccess";
import { ConversationHistory, type Conversation } from "./ConversationHistory";
import { RunTimeline } from "./RunTimeline";
import { CognitoLogin, McpConnections } from "./OAuthAccess";
import { AgentMarketplace, PurchaseSummary } from "./AgentMarketplace";
import { TaskTemplates, CreateFromTemplate, TaskRunForm, TaskReport, supportsTask, type Task } from "./TaskTemplates";
import { OrderDesk } from "./OrderDesk";
import { isSell, receiveAsset, tradeAmount, tradeTitle } from "./trades";
import { AgentManagement, WorkspaceActions } from "./AgentManagement";
import { navigationFor, rememberNavigation, rememberAuthReturn, takeAuthReturn, type Navigation, type View } from "./navigation";
import "./styles.css";
import "./history.css";

type User = { sub: string; tenant: string; email: string; admin: boolean };
type Agent = { id: string; name: string; skill_id: string; template_id?: string; status: string; max_trade_units: number; daily_limit_units: number };
type Operation = { id: string; kind: string; status: string; agent_id: string; conversation_id?: string; intent: any; intent_hash: string; created_at: number; expires_at: number; execution?: any };
type Execution = { startedAt: number; status: "EXECUTING" | "UNKNOWN" };
type State = { agents: Agent[]; operations: Operation[]; grants: any[]; events: any[]; subscriptions: string[]; wallet: { USDC: string; ETH: string }; mode: string; model_id: string };
const fmt = (v: string | number, digits = 2) => Number(v).toLocaleString("en-US", { maximumFractionDigits: digits });
const when = (v: number) => new Date(v * 1000).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
const labels: Record<string, string> = { PENDING: "待确认", APPROVED: "已批准", EXECUTING: "执行中", EXPIRED: "已过期", CONFIRMED: "已完成", REJECTED: "已拒绝", CANCELLED: "已取消", ACTIVE: "运行中", PAUSED: "已暂停", REVOKED: "已撤销" };

function Pill({ status }: { status: string }) {
  return <span className={"pill " + status.toLowerCase()}><i />{labels[status] || status}</span>;
}

function App() {
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);
  const [health, setHealth] = useState<any>({});
  const [state, setState] = useState<State | null>(null);
  const [catalog, setCatalog] = useState<any>({ products: [], skills: [] });
  const [view, setView] = useState<View>("workspace");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [create, setCreate] = useState(false);
  const [createSkill, setCreateSkill] = useState("controlled-trader");
  const [workspacePanel, setWorkspacePanel] = useState<"agents" | "templates" | "conversation" | "orders">("agents");
  const [orderTemplate, setOrderTemplate] = useState("eth-order");
  const [mcpTab, setMcpTab] = useState("all");
  const [templateCreate, setTemplateCreate] = useState<Task | null>(null);
  const [taskRun, setTaskRun] = useState<{ agentId: string; templateId?: string } | null>(null);
  const [selected, setSelected] = useState("");
  const [session, setSession] = useState<any>(null);
  const [input, setInput] = useState("");
  const [working, setWorking] = useState(false);
  const [approval, setApproval] = useState<Operation | null>(null);
  const [schedule, setSchedule] = useState(false);
  const [busy, setBusy] = useState(false);
  const [executions, setExecutions] = useState<Record<string, Execution>>({});
  const submitting = useRef(new Set<string>());
  const executing = Object.values(executions).some(e => e.status === "EXECUTING");
  const [history, setHistory] = useState<Conversation[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const identity = useRef(user?.sub);
  identity.current = user?.sub;
  const scope = useRef("");
  scope.current = `${user?.sub || ""}:${selected}`;
  const currentSession = useRef<string | undefined>(session?.id);
  currentSession.current = session?.id;
  const selectionVersion = useRef(0);
  const manualNew = useRef(false);
  const oauthReturnHandled = useRef(false);
  const restoringNavigation = useRef(false);

  function clearIdentity() {
    identity.current = undefined; scope.current = ""; currentSession.current = undefined; selectionVersion.current++;
    setUser(null); setState(null); setSession(null); setHistory([]); setSelected("");
    setApproval(null); setSchedule(false); setCreate(false); setWorking(false); setExecutions({});
    setTemplateCreate(null); setTaskRun(null); setWorkspacePanel("conversation"); setCreateSkill("controlled-trader"); setMcpTab("all");
    setCatalog({ products: [], skills: [] }); setInput(""); setNotice(""); setView("workspace"); setWorkspacePanel("agents");
    setCsrf(""); manualNew.current = false;
    oauthReturnHandled.current = false; restoringNavigation.current = false;
  }

  function fail(e: unknown) {
    setError(e instanceof Error ? e.message : "操作失败");
    if (e instanceof ApiError && ["SESSION_EXPIRED", "UNAUTHENTICATED", "OAUTH_LOGIN_REQUIRED", "OAUTH_EXCHANGE_FAILED"].includes(e.code)) {
      clearIdentity();
    }
  }
  async function refresh() {
    const owner = identity.current;
    try {
      const [s, c] = await Promise.all([api<State>("/dashboard"), api("/catalog")]);
      if (owner !== identity.current) return;
      setState(s); setCatalog(c);
      const saved = (owner && navigationFor(owner)?.agentId) || localStorage.getItem("onchain:last-agent:"+owner);
      setSelected(old => old || s.agents.find(agent => agent.id === saved)?.id || s.agents[0]?.id || "");
    } catch (e) { if (owner === identity.current) throw e; }
  }
  async function logout() {
    try { await api("/auth/logout", "POST"); clearIdentity(); }
    catch (e) { fail(e); }
  }
  function selectAgent(id: string) {
    currentSession.current = undefined; selectionVersion.current++; manualNew.current = false;
    setSelected(id); setSession(null); setWorking(false); setHistory([]); setWorkspacePanel("conversation");
  }
  function newConversation() {
    currentSession.current = undefined; selectionVersion.current++; manualNew.current = true;
    setSession(null); setWorking(false); setInput("");
    localStorage.removeItem("onchain:last-session:"+scope.current);
  }
  async function openConversation(id: string) {
    const currentScope = scope.current;
    const version = ++selectionVersion.current;
    try {
      const conversation = await api("/conversations/"+id);
      if (scope.current !== currentScope || selectionVersion.current !== version) return;
      currentSession.current = conversation.id;
      setSession(conversation); setWorking(conversation.busy_until > Date.now()/1000);
      manualNew.current = false;
      localStorage.setItem("onchain:last-session:"+currentScope, id);
    } catch (e) { if (scope.current === currentScope && version === selectionVersion.current) fail(e); }
  }
  async function restoreConversation(agentId: string, sid: string) {
    const owner = identity.current;
    const version = ++selectionVersion.current;
    const conversation = await api("/conversations/" + sid);
    if (identity.current !== owner || selectionVersion.current !== version) return false;
    if (conversation.agent_id !== agentId) throw new Error("会话与 Agent 不匹配，请重新选择会话。");
    // Apply the verified Agent and conversation together; never depend on a stale React selection.
    const nextScope = `${owner}:${agentId}`;
    if (scope.current !== nextScope) setHistory([]);
    scope.current = nextScope;
    currentSession.current = conversation.id;
    setSelected(agentId); setSession(conversation);
    setWorking(conversation.busy_until > Date.now()/1000); setView("workspace"); setWorkspacePanel("conversation");
    manualNew.current = false;
    localStorage.setItem("onchain:last-agent:" + owner, agentId);
    localStorage.setItem("onchain:last-session:" + scope.current, sid);
    if (owner) rememberNavigation(owner, { view: "workspace", agentId, conversationId: sid });
    return true;
  }
  useEffect(() => {
    if (user && selected) localStorage.setItem("onchain:last-agent:"+user.sub, selected);
  }, [user?.sub, selected]);
  useEffect(() => {
    if (!user || !selected) return;
    let active = true;
    const currentScope = scope.current;
    setHistoryLoading(true);
    const load = async () => {
      try {
        const result = await api(`/agents/${selected}/conversations`);
        if (!active || scope.current !== currentScope) return;
        const items: Conversation[] = result.conversations || [];
        setHistory(items); setHistoryLoading(false);
        if (!currentSession.current && !manualNew.current && !restoringNavigation.current) {
          const tab = navigationFor(user.sub);
          const saved = (tab?.agentId === selected && tab.conversationId) || localStorage.getItem("onchain:last-session:"+currentScope);
          const resume = items.find(item => item.id === saved) || items[0];
          if (resume) await openConversation(resume.id);
        }
      } catch (e) { if (active && scope.current === currentScope) { setHistoryLoading(false); fail(e); } }
    };
    load();
    const timer = setInterval(load, 5000);
    return () => { active = false; clearInterval(timer); };
  }, [user?.sub, selected]);
  useEffect(() => {
    api("/health").then(setHealth).catch(() => {});
    api("/auth/me").then(r => { setCsrf(r.csrf); setUser(r.user); }).catch(() => {}).finally(() => setReady(true));
  }, []);
  useEffect(() => {
    if (!user) return;
    refresh().catch(fail);
    const timer = setInterval(() => refresh().catch(fail), executing ? 1500 : 7000);
    return () => clearInterval(timer);
  }, [user, executing]);
  useEffect(() => {
    if (!user || oauthReturnHandled.current) return;
    const params = new URLSearchParams(window.location.search);
    const verified = params.get("verified_operation");
    const review = params.get("review_operation");
    oauthReturnHandled.current = true;
    const saved = navigationFor(user.sub);
    if (saved) { setView(["overview", "templates", "trading"].includes(saved.view) ? "workspace" : saved.view); setWorkspacePanel(saved.view === "templates" ? "templates" : saved.view === "overview" ? "agents" : "conversation"); }
    if (params.get("auth_error")) setError("认证未完成（" + params.get("auth_error") + "），操作尚未批准。");
    if (params.get("mcp") === "connected") { setView("market"); setNotice("MCP 连接授权已生效，可随时撤销。"); }
    if (verified || review) {
      const owner = user.sub;
      const origin = verified ? takeAuthReturn(owner, verified) : null;
      restoringNavigation.current = true;
      (async () => {
        const op = await api<Operation>("/operations/" + (verified || review));
        if (identity.current !== owner) return;
        const destination: Navigation = op.kind === "MARKET_PURCHASE" ? { view: "agents", agentId: "" }
          : origin && origin.agentId === op.agent_id ? origin
          : op.conversation_id ? { view: "workspace", agentId: op.agent_id, conversationId: op.conversation_id }
          : { view: "approvals", agentId: op.agent_id };
        if (destination.view === "workspace" && destination.conversationId) {
          await restoreConversation(op.agent_id, destination.conversationId);
          if (identity.current !== owner) return;
        } else {
          selectAgent(op.agent_id); setView(destination.view);
        }
        if (!verified) { setApproval(op); return; }
        const approved = await api<Operation>(`/operations/${op.id}/approve`, "POST", { intent_hash: op.intent_hash });
        if (identity.current !== owner) return;
        if (approved.kind === "TRADE") await executeOperation(approved, destination.view === "workspace");
        else { await refresh(); if (approved.kind === "MARKET_PURCHASE") { selectAgent(approved.agent_id); setView("workspace"); } setNotice(approved.kind === "MARKET_PURCHASE" ? approved.intent.strategy === "TASK" ? "购买成功，请在 Agent 工作台运行你的任务。" : "购买成功，你的独立 Agent 已进入自动调度。" : "定投授权已生效。"); }
      })().catch(e => { if (identity.current === owner) fail(e); })
        .finally(() => { if (identity.current === owner) restoringNavigation.current = false; });
    }
    window.history.replaceState({}, "", "/");
  }, [user]);
  useEffect(() => {
    if (!user || !state?.agents.some(agent => agent.id === selected) || restoringNavigation.current) return;
    const previous = navigationFor(user.sub);
    rememberNavigation(user.sub, {
      view, agentId: selected,
      conversationId: session?.agent_id === selected ? session.id
        : !manualNew.current && previous?.agentId === selected ? previous.conversationId : undefined,
    });
  }, [user?.sub, state, view, selected, session?.id]);
  useEffect(() => {
    if (!user) { setExecutions({}); submitting.current.clear(); return; }
    if (!state) return;
    setExecutions(previous => {
      const next = { ...previous };
      let changed = false;
      for (const [id, execution] of Object.entries(previous)) {
        const op = state.operations.find(o => o.id === id);
        if (op && ["CONFIRMED", "REJECTED", "CANCELLED"].includes(op.status)) {
          delete next[id]; changed = true;
        } else if (execution.status === "EXECUTING" && Date.now() - execution.startedAt > 180_000) {
          next[id] = { ...execution, status: "UNKNOWN" }; changed = true;
        }
      }
      return changed ? next : previous;
    });
  }, [state, user]);
  useEffect(() => {
    if (!session?.id || !user) return;
    let active = true;
    const sid = session.id;
    const timer = setInterval(async () => {
      try {
        const s = await api("/conversations/" + sid);
        if (!active || currentSession.current !== sid) return;
        setSession(s); setWorking(s.busy_until > Date.now() / 1000);
      } catch (e) { if (active && currentSession.current === sid) { fail(e); setSession(null); setWorking(false); } }
    }, 1500);
    return () => { active = false; clearInterval(timer); };
  }, [session?.id, user]);
  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(() => setNotice(""), 5000);
    return () => clearTimeout(timer);
  }, [notice]);

  async function action(fn: () => Promise<unknown>, message?: string) {
    const owner = identity.current;
    setError(""); setBusy(true);
    try { await fn(); if (owner !== identity.current) return; await refresh(); if (message) setNotice(message); }
    catch (e) { if (owner === identity.current) fail(e); }
    finally { setBusy(false); }
  }
  async function executeOperation(op: Operation, followConversation = false) {
    if (submitting.current.has(op.id) || executions[op.id]?.status === "EXECUTING") return;
    submitting.current.add(op.id);
    const owner = identity.current;
    const navigationVersion = selectionVersion.current;
    setError("");
    setExecutions(previous => ({ ...previous, [op.id]: { status: "EXECUTING", startedAt: Date.now() } }));
    try {
      const queued = await api(`/operations/${op.id}/execute`, "POST");
      if (owner !== identity.current) return;
      if (queued.conversation_id && followConversation && selectionVersion.current === navigationVersion) await restoreConversation(op.agent_id, queued.conversation_id);
      else if (queued.conversation_id && scope.current === `${owner}:${op.agent_id}`) await openConversation(queued.conversation_id);
      setNotice("交易已提交，正在执行。成交结果会自动更新。");
    } catch (e) {
      if (owner !== identity.current) return;
      setExecutions(previous => { const next = { ...previous }; delete next[op.id]; return next; });
      fail(e);
    } finally {
      submitting.current.delete(op.id);
    }
    if (owner === identity.current) await refresh().catch(fail);
  }
  async function send(text = input) {
    if (!text.trim() || !selected || working || session?.can_resume === false) return;
    const currentScope = scope.current;
    setError(""); setWorking(true);
    try {
      let s = session;
      if (!s || s.agent_id !== selected) {
        s = await api(`/agents/${selected}/conversations`, "POST");
        if (scope.current !== currentScope) return;
        currentSession.current = s.id;
        setSession(s);
        localStorage.setItem("onchain:last-session:"+currentScope, s.id);
      }
      await api(`/conversations/${s.id}/messages`, "POST", { message: text.trim() });
      setInput("");
      const updated = await api(`/conversations/${s.id}`);
      if (scope.current === currentScope && currentSession.current === s.id) setSession(updated);
    } catch (e) { if (scope.current === currentScope) { fail(e); setWorking(false); } }
  }
  async function runTask(task: Task, parameters: any) {
    if (!taskRun) return;
    if (task.mode === "approval") { setTaskRun(null); setSchedule(true); return; }
    const owner = identity.current, aid = taskRun.agentId;
    let active = session;
    if (task.mode === "prepare" || !active || active.agent_id !== aid || active.can_resume === false) {
      active = await api(`/agents/${aid}/conversations`, "POST");
    }
    if (identity.current !== owner || scope.current !== `${owner}:${aid}`) return;
    await api(`/conversations/${active.id}/tasks`, "POST", {
      template_id: task.id, template_digest: task.digest, parameters,
    });
    if (identity.current !== owner || scope.current !== `${owner}:${aid}`) return;
    setTaskRun(null);
    await restoreConversation(aid, active.id);
  }
  async function prepareOrder(agentId: string, task: Task, parameters: any) {
    const owner = identity.current, version = selectionVersion.current;
    const conversation = await api(`/agents/${agentId}/conversations`, "POST");
    if (identity.current !== owner) return;
    await api(`/conversations/${conversation.id}/tasks`, "POST", {
      template_id: task.id, template_digest: task.digest, parameters,
    });
    if (identity.current !== owner || selectionVersion.current !== version) return;
    await restoreConversation(agentId, conversation.id);
  }
  async function openOrder(op: Operation, review = false) {
    const owner = identity.current;
    if (op.conversation_id) {
      if (!await restoreConversation(op.agent_id, op.conversation_id)) return;
    } else { selectAgent(op.agent_id); setView("workspace"); }
    if (review && identity.current === owner) setApproval(op);
  }

  if (!ready) return <div className="loading"><Layers3 /><p>正在验证会话…</p></div>;
  if (!user) return <Login health={health} onLogin={r => { setCsrf(r.csrf); setUser(r.user); setError(""); }} />;

  const pending = state?.operations.filter(o => o.status === "PENDING" && o.expires_at * 1000 > Date.now()) || [];
  const currentAgent = state?.agents.find(a => a.id === selected);
  const currentSkill = catalog.skills.find((s: any) => s.id === currentAgent?.skill_id);
  const canOrder = currentSkill?.tools.includes("execute_trade");
  const nav: [View, string, React.ReactNode][] = [
    ["agents", "Agent 市场", <Store size={18} />],
    ["workspace", "Agent 工作台", <MessageSquare size={18} />],
    ["market", "MCP / Skill 管理", <Boxes size={18} />],
  ];
  const auxiliary: [View, string, React.ReactNode][] = [
    ["approvals", "授权与操作", <Fingerprint size={18} />],
    ["security", "安全中心", <ShieldCheck size={18} />],
  ];
  const title = [...nav, ...auxiliary].find(n => n[0] === view)?.[1];

  return <div className="shell">
    <aside className="sidebar">
      <a className="brand" onClick={() => { setView("workspace"); setWorkspacePanel("agents"); }}><span className="brand-icon"><Layers3 size={23} /></span><span>onchain<span className="brand-sub">CONTROL</span></span></a>
      <div className="workspace-tag"><span className="workspace-avatar">{user.email[0].toUpperCase()}</span><div>个人工作空间<small>独立身份 · 独立会话</small></div><LockKeyhole size={14} /></div>
      <span className="nav-label">WORKSPACE</span>
      <nav aria-label="主要功能">{nav.map(([id, label, icon]) => <button key={id} className={view === id ? "active" : ""} onClick={() => { setView(id); if (id === "workspace") setWorkspacePanel(selected ? "conversation" : "agents"); }}>{icon}<span>{label}</span>{id === "approvals" && pending.length > 0 && <b>{pending.length}</b>}</button>)}</nav>
      <nav className="aux-nav" aria-label="账户控制">{auxiliary.map(([id, label, icon]) => <button key={id} className={view === id ? "active" : ""} onClick={() => { setView(id); if (id === "workspace") setWorkspacePanel(selected ? "conversation" : "agents"); }}>{icon}<span>{label}</span>{id === "approvals" && pending.length > 0 && <b>{pending.length}</b>}</button>)}</nav>
      <div className="sidebar-bottom"><div className="infra-status"><i /><span>{health.runtime === "aws" ? "AWS AgentCore" : "本地开发环境"}</span></div><small>Bedrock · GPT-6 Sol</small><button className="profile" onClick={logout}><span className="avatar">{user.email[0].toUpperCase()}</span><span>{user.email}<small>退出登录</small></span><LogOut size={16} /></button></div>
    </aside>
    <main>
      <header className="topbar"><div className="breadcrumb">工作空间 <ChevronRight size={14} /> <strong>{title}</strong></div><div className="top-right"><span className="paper-badge">{catalog?.onchain_data_mode === "demo" ? "演示数据 · 模拟交易" : "模拟交易"}</span><span className="session-badge"><LockKeyhole size={13} /> Session 隔离</span><button className="icon-btn" aria-label="刷新" onClick={() => refresh().catch(fail)}><RefreshCw size={16} /></button></div></header>
      <div className="content">
        {error && <div role="alert" className="banner error"><ShieldAlert size={18} /><span>{error}</span><button onClick={() => setError("")}><X size={16} /></button></div>}
        {notice && <div role="status" className="banner success"><CircleCheck size={18} />{notice}</div>}
        <div className="page-heading"><div><span className="eyebrow">ONCHAIN CONTROL PLATFORM</span><h1>{title}</h1><p>{{ overview: "创建、运行与管理你的 Agent，按任务选择模板。", trading: "准备订单、确认授权、查看成交与持仓变化。", agents: "卖家发布任务 Agent，买家购买独立实例完成任务。", templates: "选择任务、配置参数，让 Agent 按已审核步骤运行。", workspace: "创建、管理和运行 Agent，查看任务、交易与完整会话记录。", market: "选择工具能力，明确每一个 Skill 的权限。", approvals: "查看操作内容，确认授权，随时停止自动执行。", security: "每次调用都有身份，每次拦截都有依据。" }[view]}</p></div>

          {view === "approvals" && <button className="primary" disabled={!selected} onClick={() => setSchedule(true)}><Clock3 size={17} />创建定投授权</button>}
        </div>
        {!state ? <div className="loading-inline">正在加载工作空间…</div> : <>
          {view === "agents" && <AgentMarketplace key={user.sub} userId={user.sub} agents={state.agents} grants={state.grants} tasks={catalog.templates || []} skills={catalog.skills} onReview={setApproval} onChanged={refresh} onOpen={id => { selectAgent(id); setView("workspace"); }} />}
          {view === "workspace" && <>
            <div className="workspace-toolbar"><div className="market-tabs" role="tablist" aria-label="工作台功能">
              <button role="tab" aria-selected={workspacePanel === "agents"} className={workspacePanel === "agents" ? "active" : ""} onClick={() => setWorkspacePanel("agents")}>我的 Agents</button>
              <button role="tab" aria-selected={workspacePanel === "templates"} className={workspacePanel === "templates" ? "active" : ""} onClick={() => setWorkspacePanel("templates")}>任务模板</button>
              <button role="tab" aria-selected={workspacePanel === "conversation"} className={workspacePanel === "conversation" ? "active" : ""} onClick={() => setWorkspacePanel("conversation")}>任务与会话</button>
              {canOrder && <button className={workspacePanel === "orders" ? "active" : ""} onClick={() => setWorkspacePanel("orders")}>交易下单</button>}
            </div>{["conversation", "orders"].includes(workspacePanel) && <select aria-label="选择 Agent" value={selected} onChange={e => selectAgent(e.target.value)}><option value="" disabled>选择 Agent</option>{state.agents.map(a => <option key={a.id} value={a.id}>{a.name} · {labels[a.status]}</option>)}</select>}</div>
            {["agents", "templates"].includes(workspacePanel) ? <AgentManagement agents={state.agents} skills={catalog.skills} tasks={catalog.templates || []} busy={busy} templates={workspacePanel === "templates"} onTemplates={() => setWorkspacePanel("templates")}
            onCreate={() => { setCreateSkill("controlled-trader"); setCreate(true); }} onTemplate={setTemplateCreate}
            onOpen={agent => { selectAgent(agent.id); setView("workspace"); }}
            onStatus={agent => action(() => api(`/agents/${agent.id}/status`, "POST", { status: agent.status === "ACTIVE" ? "PAUSED" : "ACTIVE" }))}
            onDelete={async agent => {
              const owner = identity.current;
              await api(`/agents/${agent.id}`, "DELETE");
              if (identity.current !== owner) return;
              localStorage.removeItem("onchain:last-session:" + owner + ":" + agent.id);
              if (selected === agent.id) { selectAgent(""); localStorage.removeItem("onchain:last-agent:" + owner); }
              await refresh(); setWorkspacePanel("agents"); setNotice("Agent 已删除，持续授权和待执行操作已撤销。");
            }} /> : <>
            {workspacePanel === "orders" && canOrder ? <OrderDesk key={selected} agents={currentAgent ? [currentAgent] : []} skills={catalog.skills}
              orders={state.operations} wallet={state.wallet} subscriptions={state.subscriptions} executions={executions}
              tasks={(catalog.templates || []).filter((t: Task) => t.mode === "prepare" && supportsTask(t, currentSkill))} initialTemplate={currentAgent?.template_id === "eth-sell" ? "eth-sell" : orderTemplate} selected={selected}
              onPrepare={prepareOrder} onCreate={setTemplateCreate} onReview={op => openOrder(op, true)}
              onExecute={op => executeOperation(op, true)}
              onCancel={op => action(() => api(`/operations/${op.id}/reject`, "POST"), "订单已撤回，不会执行。")}
              onOpen={op => openOrder(op)} onMcp={() => setView("market")} /> : <div className="work-grid">

            <ConversationHistory items={history} selected={session?.id} loading={historyLoading} onSelect={openConversation} onNew={newConversation} />
            <section className="panel chat-panel"><div className="chat-header"><span className="agent-icon small"><Bot size={18} /></span><strong>{currentAgent?.name || "请选择 Agent"}</strong><button className="text-btn" disabled={!selected || working || currentAgent?.status !== "ACTIVE"} onClick={newConversation}>新会话</button></div>
              <WorkspaceActions agent={currentAgent} tasks={catalog.templates || []} skills={catalog.skills} working={working}
                onTask={task => setTaskRun({ agentId: selected, templateId: task.id })} onOrder={task => { setOrderTemplate(task.id); setWorkspacePanel("orders"); }} />
              <button className="text-btn task-launch" disabled={!selected || working || currentAgent?.status !== "ACTIVE"} onClick={() => setTaskRun({ agentId: selected, templateId: currentAgent?.template_id })}><ClipboardList size={14} />运行任务模板</button>
              {session?.title && <div className="chat-title">{session.title}</div>}
              <div className="messages">{!session?.messages?.length ? <div className="chat-empty"><span className="big-symbol"><Layers3 size={35} /></span><h2>你想让 Agent 做什么？</h2><p>先理解意图，再验证权限，最后执行。</p>{!selected && <button className="primary" onClick={() => setCreate(true)}>先创建一个 Agent</button>}</div> : session.messages.map((m: any, i: number) => <div className={"message " + m.role} key={i}><div className="message-icon">{m.role === "user" ? user.email[0].toUpperCase() : <Bot size={17} />}</div><div className="message-body"><span className="message-author">{m.role === "user" ? "你" : m.content?.action === "task" ? "Agent · 任务模板" : "Agent · GPT-6 Sol"}</span>{typeof m.content === "string" ? <p>{m.content}</p> : <Result result={m.content} operations={state.operations} executions={executions} onReview={setApproval} onExecute={executeOperation} />}</div></div>)}
                {working && <div className="thinking"><span /><span /><span />{session?.runs?.at(-1)?.kind === "execute" ? "正在执行已授权的交易…" : "正在识别意图与调用工具…"}</div>}
              </div>
              {session?.can_resume === false ? <div className="history-readonly">历史会话只读，可查看消息和运行记录。<br /><button className="text-btn" onClick={newConversation}>新建会话继续</button></div> : <form className="composer" onSubmit={e => { e.preventDefault(); send(); }}><textarea aria-label="输入操作意图" value={input} onChange={e => setInput(e.target.value)} placeholder={canOrder ? "描述交易目标，例如：用 20 USDC 买入 ETH" : "描述当前 Agent 的任务，或选择上方的示例动作"} maxLength={4000} onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} /><div><small><LockKeyhole size={12} /> 身份与会话由后端绑定</small><button className="send-btn" aria-label="发送" disabled={!selected || working || currentAgent?.status !== "ACTIVE" || !input.trim()}><Send size={17} /></button></div></form>}
            </section>
            <RunTimeline session={session} />
          </div>}</>}</>}
          {view === "market" && <>
            <div className="market-tabs mcp-tabs">{[["all", "全部能力"], ["mcp", "MCP 服务"], ["skills", "Skills"], ["oauth", "OAuth 连接"]].map(([id, name]) => <button key={id} className={mcpTab === id ? "active" : ""} onClick={() => setMcpTab(id)}>{name}</button>)}</div>
            {health.oauth && ["all", "oauth"].includes(mcpTab) && <McpConnections agents={state.agents} />}
            {mcpTab === "oauth" && !health.oauth && <p className="muted">外部 MCP OAuth 连接在 Cognito 云端环境启用。</p>}
            {["all", "mcp"].includes(mcpTab) && <>
            <div className="section-caption"><h2>MCP 服务</h2><span>平台托管 · 固定版本 · 逐次鉴权</span></div>
            <div className="market-grid">{catalog.products.map((p: any) => <div className="panel product-card" key={p.id}><div className="product-top"><span className={"product-icon "+p.id}>{p.id === "trade" ? <Fingerprint /> : p.id === "portfolio" ? <Wallet /> : <Activity />}</span><span className="version">v{p.version}</span></div><h3>{p.name}</h3><p>{p.description}</p><span className={"risk-label "+p.risk}>{p.risk === "asset-write" ? "资金操作 · 需授权" : p.risk === "private-read" ? "私有数据 · 仅本人" : "只读能力"}</span><div className="provider-state">{p.data_mode === "demo" ? "演示数据 · 无需 OKX Key" : p.availability === "REQUIRES_OKX" ? "实时数据：待配置 OKX 凭据" : "可用 · 按当前数据源运行"}</div><div className="tool-tags">{p.tools.map((t: string) => <code key={t}>{t}</code>)}</div><div className="product-footer"><small><CircleCheck size={13} />{p.publisher}</small><button className={state.subscriptions.includes(p.id) ? "secondary" : "primary"} disabled={busy} onClick={() => action(() => api(`/catalog/${p.id}/subscription`, "POST", { enabled: !state.subscriptions.includes(p.id) }), "MCP 权限已更新")}>{state.subscriptions.includes(p.id) ? "停用" : "启用"}</button></div></div>)}</div>
            </>}{["all", "skills"].includes(mcpTab) && <><div className="section-caption"><h2>已审核 Skills</h2><span>Skill 只声明能力，实际权限由 Gateway 执行</span></div>
            <div className="skills-grid">{catalog.skills.map((s: any) => <div className="panel skill-card" key={s.id}><span className="agent-icon"><Layers3 /></span><div><h3>{s.name} <small>v{s.version}</small></h3><p>{s.description}</p><div className="tool-tags">{s.tools.map((t: string) => <code key={t}>{t}</code>)}</div><small className="hash">SHA-256 {s.digest.slice(0, 24)}…</small>{s.instructions && <details className="skill-instructions"><summary>查看执行规则</summary><ol>{s.instructions.map((rule: string) => <li key={rule}>{rule}</li>)}</ol></details>}<div className="skill-binding"><span>已绑定 {state.agents.filter(a => a.skill_id === s.id).length} 个 Agent</span><button className="text-btn" onClick={() => { setCreateSkill(s.id); setCreate(true); }}>使用此 Skill 创建 Agent</button></div></div></div>)}</div></>}
          </>}
          {view === "approvals" && <>
            <section className="panel"><div className="panel-heading"><h2>操作记录</h2><button className="text-btn" onClick={async () => { const data = await api("/export"); const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" })); const a = document.createElement("a"); a.href = url; a.download = "onchain-export.json"; a.click(); URL.revokeObjectURL(url); }}><Download size={15} />导出</button></div>
              {!state.operations.length ? <Empty icon={<Fingerprint />} title="暂无待确认的操作" text="Agent 准备的交易和持续授权会显示在这里。" /> :
                <div className="table-wrap"><table><thead><tr><th>操作</th><th>范围</th><th>创建时间</th><th>状态</th><th /></tr></thead><tbody>{state.operations.map(op => {
                  const running = executions[op.id]?.status === "EXECUTING" && op.status === "APPROVED";
                  const expired = ["PENDING", "APPROVED"].includes(op.status) && op.expires_at * 1000 <= Date.now();
                  return <tr key={op.id}><td><strong>{op.kind === "MARKET_PURCHASE" ? "购买 Agent 服务" : op.kind === "GRANT" ? "定投授权" : tradeTitle(op.intent)}</strong><small>{op.id.slice(0, 15)}…</small></td><td>{op.kind === "MARKET_PURCHASE" ? fmt(op.intent.price_units / 1e6) + " USDC" : tradeAmount(op.intent)}<small>{op.kind === "MARKET_PURCHASE" ? op.intent.title : "Base · 模拟交易"}</small></td><td>{when(op.created_at)}</td><td><Pill status={running ? "EXECUTING" : expired ? "EXPIRED" : op.status} />{executions[op.id]?.status === "UNKNOWN" && <small>尚未确认成交，请刷新状态；可安全重试。</small>}</td><td><div className="row-actions">
                    {op.status === "PENDING" && !expired && <button className="primary compact" onClick={() => setApproval(op)}>核对并确认</button>}
                    {op.status === "APPROVED" && !expired && <button className="primary compact" disabled={busy || running} onClick={() => executeOperation(op)}>{running ? "执行中…" : "执行"}</button>}
                    {["PENDING", "APPROVED"].includes(op.status) && !running && <button className="text-btn" onClick={() => action(() => api(`/operations/${op.id}/reject`, "POST"))}>拒绝</button>}
                  </div></td></tr>;
                })}</tbody></table></div>}
            </section>
            <section className="panel"><div className="panel-heading"><h2>持续授权</h2><span className="muted">用户离线后，仍按已确认的范围执行</span></div>{!state.grants.length ? <Empty icon={<Clock3 />} title="没有生效的自动交易授权" text="创建定投授权并完成身份验证后，调度器才会执行。" /> : state.grants.map(g => <div className="grant-row" key={g.id}><Clock3 size={20} /><div><strong>每 {g.interval_seconds / 60} 分钟买入 {fmt(g.amount_units / 1e6)} USDC 的 ETH</strong><small>累计 {fmt(g.used_units / 1e6)} / {fmt(g.total_units / 1e6)} USDC · 到期 {when(g.expires_at)}</small></div><Pill status={g.status} />{g.status === "ACTIVE" && <button className="danger-btn" onClick={() => action(() => api(`/grants/${g.id}/revoke`, "POST"), "授权已撤销，后续执行将被阻止")}>撤销授权</button>}</div>)}</section>
          </>}
          {view === "security" && <>
            <div className="security-summary"><ShieldCheck size={36} /><div><h2>确定规则，明确边界</h2><p>Gateway 拦截器校验身份、工具权限和用户意图。执行服务再次核验额度与授权。</p></div><span className="safe-tag">NO GUARDRAILS</span></div>
            <MfaSettings email={user.email} enabled={health.auth === "cognito"} /><div className="security-grid"><section className="panel"><div className="panel-heading"><h2>拦截验证</h2><span className="muted">只检查，不执行工具</span></div><p className="muted">使用当前用户的真实权限，验证越权参数、禁用工具和交易额度。</p><Probe agents={state.agents} onRun={(body: any) => api("/security/probe", "POST", body)} /></section><section className="panel"><h3>运行中的控制</h3><ul className="checklist">{["身份绑定用户与租户", "不同用户使用独立 Runtime session", "Gateway 工具调用逐次校验", "操作内容与用户批准记录绑定", "日额度在数据库内原子更新", "重复执行返回原操作结果", "暂停 Agent 使旧调用凭据失效"].map(t => <li key={t}><Check size={16} />{t}</li>)}</ul></section></div>
            <section className="panel"><div className="panel-heading"><h2>审计事件</h2><span className="muted">仅当前用户可见 · 最近 150 条</span></div>{state.events.length ? <div className="audit-list">{state.events.map(e => <div className={"audit-row "+e.severity} key={e.id}>{e.severity === "info" ? <CircleCheck size={17} /> : <ShieldAlert size={17} />}<div><strong>{e.kind}</strong><p>{e.detail}</p></div><time>{when(e.at)}</time></div>)}</div> : <Empty icon={<ShieldCheck />} title="暂无审计事件" text="创建 Agent 或发起操作后，事件会出现在这里。" />}</section>
          </>}
        </>}
        <footer>ONCHAIN CONTROL <span>用户掌握授权，Agent 在边界内执行。</span></footer>
      </div>
    </main>
    {create && <Modal title="创建 Agent" onClose={() => setCreate(false)}><CreateAgent initialSkill={createSkill} skills={catalog.skills} onCreate={async (body: any) => { const agent = await api<Agent>("/agents", "POST", body); selectAgent(agent.id); await refresh(); setCreate(false); setView("workspace"); }} /></Modal>}
    {templateCreate && <Modal title={"从模板创建：" + templateCreate.name} onClose={() => setTemplateCreate(null)}><CreateFromTemplate task={templateCreate} products={catalog.products} onCreated={async agent => {
      if (identity.current !== user.sub) return;
      selectAgent(agent.id); setView("workspace");
      setTaskRun({ agentId: agent.id, templateId: templateCreate.id }); setTemplateCreate(null);
      await refresh();
    }} /></Modal>}
    {taskRun && <Modal title="运行任务模板" onClose={() => setTaskRun(null)}><TaskRunForm key={taskRun.agentId} initial={taskRun.templateId} tasks={(catalog.templates || []).filter((t: Task) => {
      const agent = state?.agents.find(a => a.id === taskRun.agentId);
      const skill = catalog.skills.find((s: any) => s.id === agent?.skill_id);
      return supportsTask(t, skill);
    })} onRun={runTask} /></Modal>}
    {approval && <Modal title={approval.kind === "MARKET_PURCHASE" ? (approval.intent.strategy === "TASK" ? "确认购买任务 Agent" : "确认购买与自动交易授权") : approval.kind === "TRADE" ? "核对并执行交易" : "核对并批准授权"} onClose={() => setApproval(null)}><Approval operation={approval} user={user} oauth={!!health.oauth} returnTo={{ view, agentId: selected, conversationId: session?.id }} onDone={async approved => {
      if (identity.current !== user.sub) return;
      setApproval(null);
      setState(previous => previous && ({ ...previous, operations: previous.operations.map(op => op.id === approved.id ? approved : op) }));
      if (approved.kind === "TRADE") await executeOperation(approved);
      else { await refresh(); if (approved.kind === "MARKET_PURCHASE") { selectAgent(approved.agent_id); setView("workspace"); } setNotice(approved.kind === "MARKET_PURCHASE" ? approved.intent.strategy === "TASK" ? "购买成功，请在 Agent 工作台运行你的任务。" : "购买成功，你的独立 Agent 已进入自动调度。" : "定投授权已生效，将按已确认的计划运行。"); }
    }} /></Modal>}
    {schedule && <Modal title="创建定投授权" onClose={() => setSchedule(false)}><Schedule agents={state?.agents || []} selected={selected} onCreate={async (body: any) => { const op = await api<Operation>("/grants/prepare", "POST", body); await refresh(); setSchedule(false); setApproval(op); }} /></Modal>}
  </div>;
}

function Login({ health, onLogin }: { health: any; onLogin: (r: any) => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [challenge, setChallenge] = useState<any>(null);
  const [code, setCode] = useState("");
  async function submit(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setError("");
    try {
      const r = challenge ? await api("/auth/challenge", "POST", { challenge_id: challenge.challenge_id, code }) : await api("/auth/login", "POST", { email, password });
      setPassword("");
      if (r.challenge_id) setChallenge(r); else onLogin(r);
    } catch (e) { setError(e instanceof Error ? e.message : "登录失败"); }
    finally { setBusy(false); }
  }
  if (health.oauth) return <CognitoLogin />;
  return <div className="login-page"><div className="login-story"><div className="brand"><span className="brand-icon"><Layers3 /></span><span>onchain<span className="brand-sub">CONTROL</span></span></div><div><span className="eyebrow">YOUR INTENT. YOUR CONTROL.</span><h1>把执行交给 Agent。<br />把控制留给你。</h1><p>独立会话、明确授权、逐次验证。<br />一个为可信链上操作构建的工作空间。</p><div className="login-features"><span><Fingerprint />身份认证</span><span><Boxes />MCP 工具</span><span><ShieldCheck />行为拦截</span></div></div><small>Powered by Amazon Bedrock AgentCore</small></div><div className="login-form-wrap"><form className="login-form" onSubmit={submit}><span className="login-lock"><LockKeyhole size={27} /></span><h2>登录工作空间</h2><p>{challenge ? "输入你的多因素认证验证码。" : "验证身份后，进入你的独立会话。"}</p>{error && <div role="alert" className="inline-error">{error}</div>}{challenge ? <label>验证码<input aria-label="验证码" inputMode="numeric" autoComplete="one-time-code" value={code} onChange={e => setCode(e.target.value)} required /></label> : <><label>邮箱<input type="email" autoComplete="username" placeholder="you@example.com" value={email} onChange={e => setEmail(e.target.value)} required /></label><label>密码<input type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} required /></label></>}<button className="primary full" disabled={busy}>{busy ? "正在验证…" : "验证并登录"}<ArrowUpRight size={17} /></button><div className="login-note"><ShieldCheck size={15} />凭据由认证服务验证，不发送给模型。</div>{health.auth === "local" && <div className="local-note"><strong>本地开发账号</strong><p>alice@example.test / bob@example.test<br />密码：local-only-change-me</p></div>}</form><AccountAccess enabled={health.auth === "cognito"} onEmail={setEmail} /></div></div>;
}

function Stat({ label, value, unit, icon }: any) {
  return <div className="stat"><div><span>{label}</span><span className="stat-icon">{icon}</span></div><strong>{value}<small>{unit}</small></strong></div>;
}
function Empty({ icon, title, text, action, actionText }: any) {
  return <div className="empty"><span>{icon}</span><h3>{title}</h3><p>{text}</p>{action && <button className="secondary" onClick={action}>{actionText}</button>}</div>;
}
function Modal({ title, onClose, children }: any) {
  return <div className="modal-backdrop" onClick={onClose}><section role="dialog" aria-modal="true" aria-label={title} className="modal" onClick={e => e.stopPropagation()}><div className="modal-heading"><h2>{title}</h2><button className="icon-btn" aria-label="关闭" onClick={onClose}><X size={20} /></button></div>{children}</section></div>;
}
function CreateAgent({ onCreate, skills = [], initialSkill = "controlled-trader" }: any) {
  const [name, setName] = useState("我的交易助手");
  const [skill, setSkill] = useState(initialSkill);
  const [limit, setLimit] = useState("100");
  const [daily, setDaily] = useState("500");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  return <form className="form-stack" onSubmit={async e => { e.preventDefault(); setBusy(true); try { await onCreate({ name, skill_id: skill, max_trade: limit, daily_limit: daily }); } catch (e) { setError((e as Error).message); } finally { setBusy(false); } }}>
    <label>名称<input value={name} onChange={e => setName(e.target.value)} maxLength={60} required /></label><label>Skill<select value={skill} onChange={e => setSkill(e.target.value)}>{skills.map((s: any) => <option key={s.id} value={s.id}>{s.name} · {s.tools.includes("execute_trade") ? "含已授权交易执行" : "无交易执行权限"}</option>)}</select></label><div className="form-columns"><label>单笔上限（USDC）<input type="number" min="0.01" max="1000" step="0.01" value={limit} onChange={e => setLimit(e.target.value)} required /></label><label>日额度（USDC）<input type="number" min="0.01" max="5000" step="0.01" value={daily} onChange={e => setDaily(e.target.value)} required /></label></div><p className="muted">这些额度是权限上限。创建 Agent 不会自动授予交易批准。</p>{error && <p className="inline-error">{error}</p>}<button className="primary full" disabled={busy}>创建 Agent</button>
  </form>;
}
function Approval({ operation: op, user, oauth, returnTo, onDone }: { operation: Operation; user: User; oauth: boolean; returnTo: Navigation; onDone: (approved: Operation) => Promise<void> }) {
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [challenge, setChallenge] = useState<any>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [checked, setChecked] = useState(false);
  async function approve(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setError("");
    try {
      if (oauth) {
        const result = await api(`/operations/${op.id}/oauth-stepup`, "POST", { intent_hash: op.intent_hash });
        rememberAuthReturn(user.sub, op.id, returnTo);
        window.location.assign(result.authorization_url);
        return;
      }
      const r = challenge ? await api("/auth/challenge", "POST", { challenge_id: challenge.challenge_id, code }) : await api(`/operations/${op.id}/stepup`, "POST", { email: user.email, password });
      setPassword("");
      if (r.challenge_id) { setChallenge(r); return; }
      const approved = await api<Operation>(`/operations/${op.id}/approve`, "POST", { intent_hash: op.intent_hash });
      await onDone(approved);
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  if (op.kind === "MARKET_PURCHASE") return <form className="form-stack" onSubmit={approve}>
    <PurchaseSummary intent={op.intent} />
    <label className="checkbox"><input type="checkbox" checked={checked} onChange={e => setChecked(e.target.checked)} />{op.intent.strategy === "TASK" ? "我已核对服务费、任务与 MCP 范围，同意购买此 Agent。" : "我已核对服务费，同意购买此 Agent 并在上述范围内自动交易。"}</label>
    {oauth ? <p className="muted">{op.intent.strategy === "TASK" ? "将通过 Cognito 重新验证身份，支付模拟服务费并创建任务 Agent。" : "将通过 Cognito 重新验证身份，确认后支付模拟服务费并启动自动交易。"}</p> : challenge ? <label>多因素验证码<input autoComplete="one-time-code" value={code} onChange={e => setCode(e.target.value)} required /></label> : <label>重新输入密码验证本人身份<input type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} required /></label>}
    {error && <p className="inline-error">{error}</p>}
    <button className="primary full" disabled={!checked || busy}><Fingerprint size={17} />{busy ? "正在验证…" : "验证并购买"}</button>
  </form>;
  return <form className="form-stack" onSubmit={approve}><div className="approval-summary"><span className="paper-badge">模拟交易</span><h3>{op.kind === "GRANT" ? "授权自动定投" : isSell(op.intent) ? "ETH → USDC" : "USDC → ETH"}</h3><div className="approval-amount">{tradeAmount(op.intent)}<small>/ 笔</small></div><dl><dt>网络</dt><dd>Base</dd><dt>最大滑点</dt><dd>{op.intent.max_slippage_bps / 100}%</dd><dt>收款账户</dt><dd>本人模拟账户</dd>{op.intent.reference_price && <><dt>订单参考价</dt><dd>{fmt(op.intent.reference_price)} USDC / ETH</dd><dt>最少接收</dt><dd>{fmt(op.intent.minimum_received, 8)} {receiveAsset(op.intent)}</dd></>}{op.kind === "GRANT" && <><dt>累计额度</dt><dd>{fmt(op.intent.total_units / 1e6)} USDC</dd><dt>间隔</dt><dd>{op.intent.interval_seconds / 60} 分钟</dd><dt>授权到期</dt><dd>{when(op.intent.expires_at)}</dd></>}<dt>确认截止</dt><dd>{when(op.expires_at)}</dd></dl></div><label className="checkbox"><input type="checkbox" checked={checked} onChange={e => setChecked(e.target.checked)} />{op.kind === "TRADE" ? "我已核对操作内容，同意验证后立即执行这笔模拟交易。" : "我已核对并同意上述自动定投范围。"}</label>{oauth ? <p className="muted">将跳转至 Cognito 重新验证身份，然后继续当前操作。</p> : challenge ? <label>多因素验证码<input autoComplete="one-time-code" value={code} onChange={e => setCode(e.target.value)} required /></label> : <label>重新输入密码验证本人身份<input type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} required /></label>}{error && <p className="inline-error">{error}</p>}<button className="primary full" disabled={!checked || busy}><Fingerprint size={17} />{busy ? "正在验证…" : op.kind === "TRADE" ? "验证并执行" : "验证并授权"}</button><small className="muted">批准仅适用于当前操作内容，无法被其他用户或操作复用。{op.kind === "TRADE" && "验证通过后自动提交，成交结果将在当前页面更新。"}</small></form>;
}
function Schedule({ agents, selected, onCreate }: any) {
  const [agent, setAgent] = useState(selected);
  const [amount, setAmount] = useState("20");
  const [total, setTotal] = useState("200");
  const [minutes, setMinutes] = useState("60");
  const [hours, setHours] = useState("24");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  return <form className="form-stack" onSubmit={async e => { e.preventDefault(); setBusy(true); try { await onCreate({ agent_id: agent, intent: { amount, buy_asset: "ETH", chain: "8453", max_slippage_bps: 50 }, total, interval_seconds: Number(minutes) * 60, hours: Number(hours) }); } catch (e) { setError((e as Error).message); } finally { setBusy(false); } }}><label>Agent<select value={agent} onChange={e => setAgent(e.target.value)}>{agents.map((a: Agent) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></label><div className="form-columns"><label>每笔 USDC<input type="number" min="1" value={amount} onChange={e => setAmount(e.target.value)} /></label><label>累计 USDC<input type="number" min="1" value={total} onChange={e => setTotal(e.target.value)} /></label><label>间隔（分钟）<input type="number" min="5" max="1440" value={minutes} onChange={e => setMinutes(e.target.value)} /></label><label>有效期（小时）<input type="number" min="1" max="720" value={hours} onChange={e => setHours(e.target.value)} /></label></div><p className="muted">固定买入 ETH，Base 网络，滑点上限 0.5%。下一步核对范围并重新认证。</p>{error && <p className="inline-error">{error}</p>}<button className="primary full" disabled={busy}>准备授权</button></form>;
}
function Result({ result, operations, executions, onReview, onExecute }: {
  result: any; operations: Operation[]; executions: Record<string, Execution>;
  onReview: (op: Operation) => void; onExecute: (op: Operation) => Promise<void>;
}) {
  if (result.error) return <div className="tool-error"><ShieldAlert size={17} />{result.error.message || result.error.code}</div>;
  const tool = result.tool_result;
  const op: Operation | undefined = operations.find(o => o.id === tool?.operation?.id) || tool?.operation;
  const running = op && executions[op.id]?.status === "EXECUTING" && op.status !== "CONFIRMED";
  const expired = op && ["PENDING", "APPROVED"].includes(op.status) && op.expires_at * 1000 <= Date.now();
  const explanation = op?.status === "CONFIRMED" ? "已按你确认的范围完成模拟交易。"
    : running ? "正在执行你已确认的模拟交易。"
    : expired ? "这笔操作已过期，需要重新准备。"
    : op?.status === "APPROVED" ? "这笔交易已通过身份验证和批准，可以继续执行。"
    : op?.status === "PENDING" && result.action === "task" ? "订单已生成，核对金额、参考价与最低接收数量后验证身份，即可自动执行。"
    : result.action === "task" ? (tool?.error ? "任务调用被阻止，请查看原因。" : "已按模板完成工具调用，结果如下。") : result.intent?.explanation || (result.action === "execute" ? "执行服务已返回操作状态。" : "请求已处理。");
  return <><p>{explanation}</p>{result.intent?.action && <span className="intent-tag">意图 · {result.intent.action}</span>}{tool?.error && <div className="tool-error"><ShieldAlert size={16} />{tool.error.message || JSON.stringify(tool.error)}</div>}{tool?.wallet && <div className="tool-result"><span>模拟资产</span><strong>{fmt(tool.wallet.USDC)} USDC</strong><strong>{fmt(tool.wallet.ETH, 6)} ETH</strong></div>}{tool?.price && <div className="tool-result"><span>ETH / USD</span><strong>${fmt(tool.price)}</strong><small>{tool.source}</small></div>}
    {op && <div className="operation-card" data-testid={"operation-" + op.id}>
      <div><Fingerprint size={20} /><strong>{tradeAmount(op.intent)} → {receiveAsset(op.intent)}</strong></div>
      <Pill status={running ? "EXECUTING" : expired ? "EXPIRED" : op.status} />
      {op.intent.reference_price && <p className="muted">订单参考价 {fmt(op.intent.reference_price)} USDC / ETH · 最少接收 {fmt(op.intent.minimum_received, 8)} {receiveAsset(op.intent)}</p>}
      {op.status === "CONFIRMED" ? <><p>模拟交易已完成，已收到 {fmt(op.execution?.received, 8)} {receiveAsset(op.intent)}。</p><small>成交价：{fmt(op.execution?.price)} USDC / ETH</small><small>成交回执：{op.execution?.receipt}</small></>
        : running ? <p role="status">交易已提交，正在执行。成交结果会自动更新。</p>
        : expired ? <p>操作已过期，请重新提出交易意图。</p>
        : op.status === "PENDING" ? <><p>操作已准备，等待你核对和授权。</p><button className="primary compact" onClick={() => onReview(op)}>查看操作详情</button></>
        : op.status === "APPROVED" ? <><p>{executions[op.id]?.status === "UNKNOWN" ? "尚未确认成交，请刷新状态；重试不会重复扣款。" : "身份验证和批准已完成，点击执行此笔交易即可继续。"}</p><button className="primary compact" onClick={() => onExecute(op)}>执行此笔交易</button></>
        : <p>该操作{labels[op.status] || op.status}。</p>}
    </div>}
    {tool?.status === "CONFIRMED" && <div className="tool-result"><CircleCheck size={18} /><strong>模拟操作已完成</strong><small>{tool.execution?.receipt}</small></div>}<TaskReport result={result} /></>;
}
function Probe({ agents, onRun }: any) {
  const [agent, setAgent] = useState(agents[0]?.id || "");
  const [kind, setKind] = useState("amount");
  const [result, setResult] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const cases: Record<string, any> = {
    amount: { tool: "prepare_trade", arguments: { amount: "99999", buy_asset: "ETH", chain: "8453", max_slippage_bps: 50 } },
    identity: { tool: "get_portfolio", arguments: { userId: "another-user" } },
    tool: { tool: "sign_arbitrary_transaction", arguments: {} },
    allowed: { tool: "get_quote", arguments: { symbol: "ETH" } },
  };
  return <div className="form-stack"><label>Agent<select value={agent} onChange={e => setAgent(e.target.value)}>{agents.map((a: Agent) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></label><label>测试场景<select value={kind} onChange={e => setKind(e.target.value)}><option value="amount">交易金额超过授权上限</option><option value="identity">注入其他用户身份参数</option><option value="tool">调用未批准的签名工具</option><option value="allowed">正常行情查询</option></select></label><button className="secondary" disabled={!agent || busy} onClick={async () => { setBusy(true); try { setResult(await onRun({ agent_id: agent, ...cases[kind] })); } catch (e) { setResult({ decision: "ERROR", detail: { message: (e as Error).message } }); } finally { setBusy(false); } }}><SlidersHorizontal size={16} />运行验证</button>{result && <div className={"probe-result " + result.decision.toLowerCase()}><strong>{result.decision}</strong><p>{typeof result.detail === "string" ? result.detail : result.detail?.message}</p>{result.detail?.data?.code && <code>{result.detail.data.code}</code>}</div>}</div>;
}

createRoot(document.getElementById("root")!).render(<React.StrictMode><App /></React.StrictMode>);
