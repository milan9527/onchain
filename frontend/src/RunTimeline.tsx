import { useEffect, useState } from "react";
import { Activity, Bot, Check, CircleAlert, Clock3, Plug, ShieldCheck, Wrench } from "lucide-react";
import { api } from "./api";

type RunSummary = { id: string; kind: string; status: string; started_at: number; finished_at?: number };
type Event = { id: string; kind: string; label: string; source: string; at: number; status: string; details: any; duration_ms?: number };
type Run = RunSummary & { duration_ms?: number; events: Event[] };
const statuses: Record<string, string> = {
  QUEUED: "排队中", RUNNING: "运行中", SUCCEEDED: "已完成", FAILED: "失败", BLOCKED: "已拦截", INTERRUPTED: "等待超时",
};
const sources: Record<string, string> = { task: "任务模板", worker: "任务服务", queue: "任务队列", model: "Bedrock", gateway: "Gateway", mcp: "MCP", executor: "工具执行服务", local: "本地工具" };
const elapsed = (ms: number) => ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
const time = (stamp: number) => new Date(stamp).toLocaleTimeString("zh-CN", { hour12: false });

export function RunTimeline({ session }: { session: { id: string; runs?: RunSummary[] } | null }) {
  const runs = session?.runs || [];
  const latest = runs.at(-1)?.id;
  const [selected, setSelected] = useState("");
  const [follow, setFollow] = useState(true);
  const [run, setRun] = useState<Run | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { setFollow(true); setSelected(""); setRun(null); setError(""); }, [session?.id]);
  useEffect(() => { if (follow || !selected) setSelected(latest || ""); }, [latest, follow, selected]);
  useEffect(() => {
    setRun(null); setError("");
    if (!session?.id || !selected) return;
    let active = true;
    const load = async () => {
      try {
        const data = await api<Run>(`/conversations/${session.id}/runs/${selected}`);
        if (active) { setRun(data); setError(""); }
      } catch (e) { if (active) setError((e as Error).message); }
    };
    load();
    const timer = setInterval(load, 2000);
    return () => { active = false; clearInterval(timer); };
  }, [session?.id, selected]);
  return <section className="panel trace-panel" aria-label="运行过程">
    <div className="history-heading"><h3><Activity size={17} />运行过程</h3>{run && <span className={"run-status "+run.status.toLowerCase()}>{statuses[run.status] || run.status}</span>}</div>
    {!session ? <div className="trace-empty"><Activity size={25} /><p>选择会话查看运行过程</p><small>意图识别、权限检查与真实工具调用</small></div>
      : !runs.length ? <div className="trace-empty"><Clock3 size={24} /><p>暂无运行明细</p><small>新请求会实时记录。早期历史会话只有消息记录。</small></div>
      : <>
        <select className="run-selector" aria-label="选择运行记录" value={selected} onChange={e => { setFollow(false); setSelected(e.target.value); }}>
          {[...runs].reverse().map(r => <option key={r.id} value={r.id}>{time(r.started_at)} · {r.kind === "execute" ? "交易执行" : r.kind === "task" ? "任务模板" : "Agent 对话"} · {statuses[r.status] || r.status}</option>)}
        </select>
        {!follow && selected !== latest && <button className="text-btn" onClick={() => setFollow(true)}>跟随最新运行</button>}
        {error && <p className="inline-error">{error}</p>}
        {!run && !error && <p className="history-empty" role="status">正在加载运行明细…</p>}
        {run && <>
          <div className="run-meta"><span>{run.events.length} 个事件</span><span><Clock3 size={12} />{elapsed(run.duration_ms ?? Math.max(0, Date.now() - run.started_at))}</span></div>
          <ol className="trace-list">{run.events.map(event => {
            const failed = ["ERROR", "BLOCKED"].includes(event.status);
            return <li key={event.id} className={failed ? "trace-failed" : ""}>
              <span className="trace-icon">{failed ? <CircleAlert size={14} /> : event.source === "model" ? <Bot size={14} /> : event.source === "gateway" ? <ShieldCheck size={14} /> : event.source === "mcp" ? <Plug size={14} /> : event.source === "executor" ? <Wrench size={14} /> : <Check size={14} />}</span>
              <div className="trace-entry"><strong>{event.label}</strong><div className="trace-meta"><span>{sources[event.source] || event.source}</span><time>{time(event.at)}</time>{event.duration_ms != null && <span>{elapsed(event.duration_ms)}</span>}</div>
                {Object.keys(event.details || {}).length > 0 && <details><summary>查看参数与结果</summary><pre>{JSON.stringify(event.details, null, 2)}</pre></details>}
              </div>
            </li>;
          })}</ol>
          {["RUNNING", "QUEUED"].includes(run.status) && <div className="trace-waiting"><i />等待下一步结果…</div>}
          <small className="trace-note">展示服务事件与结果摘要，敏感凭据已脱敏。</small>
        </>}
      </>}
  </section>;
}
