import { ChevronDown, FilePenLine, FilePlus2, FileText, FileX2, RotateCcw } from "lucide-react";
import Link from "next/link";
import { diffLines } from "diff";
import { useMemo, useState } from "react";
import type { MemoryVersion } from "@/lib/types";

function formatTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

function actorLabel(actor: MemoryVersion["actor"]) {
  return actor === "chat" ? "聊天" : actor === "consolidation" ? "每日整理" : "手动编辑";
}

function operationLabel(operation: MemoryVersion["operation"]) {
  return operation === "created" ? "新建" : operation === "modified" ? "更新" : "删除";
}

function operationIcon(operation: MemoryVersion["operation"]) {
  if (operation === "created") return <FilePlus2 size={15} />;
  if (operation === "modified") return <FilePenLine size={15} />;
  return <FileX2 size={15} />;
}

function Snapshot({ before, after }: { before?: string; after: string }) {
  const chunks = useMemo(() => before === undefined ? null : diffLines(before, after), [after, before]);
  if (!chunks) return <pre className="change-snapshot">{after || "（空文件）"}</pre>;
  return <div className="change-diff">{chunks.map((chunk, index) => <span className={`diff-line ${chunk.added ? "diff-added" : chunk.removed ? "diff-removed" : ""}`} key={`${index}-${chunk.value}`}>{chunk.added ? "+ " : chunk.removed ? "- " : "  "}{chunk.value}</span>)}</div>;
}

function memoryLink(change: MemoryVersion) {
  return change.operation === "deleted" ? "/memories" : `/memories?path=${encodeURIComponent(change.path)}`;
}

export function ReviewMemoryChanges({ changes, onRestore, error }: { changes: MemoryVersion[]; onRestore: (change: MemoryVersion) => void; error?: string }) {
  const [expandedPath, setExpandedPath] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const groups = useMemo(() => {
    const grouped = new Map<string, MemoryVersion[]>();
    for (const change of changes) grouped.set(change.path, [...(grouped.get(change.path) ?? []), change]);
    return Array.from(grouped, ([path, pathChanges]) => ({ path, changes: pathChanges }));
  }, [changes]);
  const actorCounts = useMemo(() => ({
    chat: changes.filter((item) => item.actor === "chat").length,
    consolidation: changes.filter((item) => item.actor === "consolidation").length,
    manual: changes.filter((item) => item.actor === "manual").length,
  }), [changes]);

  return <section className="review-card review-memory-card"><div className="card-heading"><div><span className="card-kicker">MEMORY TIMELINE</span><h2>记忆发生了什么</h2><p className="card-description">按文件归并变更，展开后查看每次快照和差异。</p></div><span className="count-pill">{error ? "—" : changes.length}</span></div>{error ? <div className="card-state card-state-error">{error}</div> : changes.length ? <div className="memory-groups">{groups.map((group) => { const latest = group.changes[0]; const expanded = expandedPath === group.path; const actorList = Array.from(new Set(group.changes.map((item) => actorLabel(item.actor)))).join(" · "); return <section className={`memory-group ${expanded ? "expanded" : ""}`} key={group.path}><div className="memory-group-header"><span className={`memory-operation operation-${latest.operation}`}>{operationIcon(latest.operation)}</span><div className="memory-change-copy"><div><Link href={memoryLink(latest)} className="memory-change-path">{group.path}</Link><span className="memory-group-count">{group.changes.length} 次</span></div><span className="memory-change-meta">最近{operationLabel(latest.operation)} · {formatTime(latest.created_at)} · {actorList}</span></div><button className="change-expand memory-group-toggle" aria-label={`${expanded ? "收起" : "展开"}${group.path}`} aria-expanded={expanded} onClick={() => setExpandedPath(expanded ? null : group.path)}><ChevronDown size={15} /></button></div>{expanded && <div className="memory-group-events">{group.changes.map((change, index) => { const eventExpanded = expandedId === change.id; const previous = group.changes[index + 1]; return <div className={`memory-group-event ${eventExpanded ? "expanded" : ""}`} key={change.id}><div className="memory-event-row"><span className={`memory-operation operation-${change.operation}`}>{operationIcon(change.operation)}</span><div className="memory-event-copy"><span className={`actor-badge actor-${change.actor}`}>{actorLabel(change.actor)}</span><span className="memory-change-meta">{operationLabel(change.operation)} · {formatTime(change.created_at)}</span></div><button className="change-expand" aria-label={`${eventExpanded ? "收起" : "展开"}${group.path} ${formatTime(change.created_at)}`} onClick={() => setExpandedId(eventExpanded ? null : change.id)}><ChevronDown size={14} /></button>{change.operation === "deleted" && <button className="restore-button" onClick={() => onRestore(change)}><RotateCcw size={12} />恢复</button>}</div>{eventExpanded && <div className="memory-change-detail"><Snapshot before={previous?.content} after={change.content} /></div>}</div>; })}</div>}</section>; })}</div> : <div className="card-empty compact-empty"><strong>这一天没有记忆变更</strong><span>当助手认为某些信息值得长期保留时，这里会出现时间线。</span></div>}<div className="change-summary"><FileText size={13} />{groups.length} 个文件 · 聊天 {actorCounts.chat} · 整理 {actorCounts.consolidation} · 手动 {actorCounts.manual}</div></section>;
}
