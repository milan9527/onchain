import { Clock3, MessageSquare, Plus, Search, Play } from "lucide-react";
import { useState } from "react";

export type Conversation = {
  id: string; agent_id: string; title: string; kind: string; updated_at: number;
  message_count: number; last_run_status?: string;
};

export function ConversationHistory({ items, selected, loading, onSelect, onNew }: {
  items: Conversation[]; selected?: string; loading: boolean;
  onSelect: (id: string) => void; onNew: () => void;
}) {
  const [query, setQuery] = useState("");
  const visible = items.filter(item => item.title?.toLowerCase().includes(query.toLowerCase()));
  return <section className="panel history-panel" aria-label="历史会话">
    <div className="history-heading"><h3><Clock3 size={16} />历史会话</h3><span>{items.length}</span></div>
    <button className="primary full" onClick={onNew}><Plus size={15} />新建会话</button>
    <label className="history-search"><Search size={14} /><input aria-label="搜索历史会话" value={query} onChange={e => setQuery(e.target.value)} placeholder="搜索会话" /></label>
    <div className="history-list">
      {loading && <p className="history-empty">正在加载历史…</p>}
      {!loading && !visible.length && <p className="history-empty">{query ? "没有匹配的会话" : "还没有会话，发送第一条消息开始。"}</p>}
      {visible.map(item => <button key={item.id} data-testid={"conversation-"+item.id}
        className={"history-item "+(selected === item.id ? "selected" : "")} onClick={() => onSelect(item.id)}>
        {item.kind !== "chat" ? <Play size={14} /> : <MessageSquare size={14} />}
        <span><strong>{item.title || "新会话"}</strong><small>
          {new Date(item.updated_at * 1000).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })}
          {" · "}{item.message_count} 条消息
        </small></span>
        {["RUNNING", "QUEUED"].includes(item.last_run_status || "") && <i className="history-live" />}
      </button>)}
    </div>
    <small className="history-retention">按 Agent 隔离 · 最近 100 个会话<br />会话与运行记录保留 30 天</small>
  </section>;
}
