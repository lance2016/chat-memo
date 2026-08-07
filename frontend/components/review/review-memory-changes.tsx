import { ChevronDown, FilePenLine, FilePlus2, FileText, FileX2, RotateCcw } from "lucide-react";
import Link from "next/link";
import { diffLines } from "diff";
import { useMemo, useState } from "react";
import type { MemoryVersion } from "@/lib/types";
import { useI18n } from "@/components/i18n-provider";

function operationIcon(operation: MemoryVersion["operation"]) {
  if (operation === "created") return <FilePlus2 size={15} />;
  if (operation === "modified") return <FilePenLine size={15} />;
  return <FileX2 size={15} />;
}

function Snapshot({ before, after }: { before?: string; after: string }) {
  const { t } = useI18n();
  const chunks = useMemo(() => before === undefined ? null : diffLines(before, after), [after, before]);
  if (!chunks) return <pre className="change-snapshot">{after || t("review.memory.emptyFile")}</pre>;
  return <div className="change-diff">{chunks.map((chunk, index) => <span className={`diff-line ${chunk.added ? "diff-added" : chunk.removed ? "diff-removed" : ""}`} key={`${index}-${chunk.value}`}>{chunk.added ? "+ " : chunk.removed ? "- " : "  "}{chunk.value}</span>)}</div>;
}

function memoryLink(change: MemoryVersion) {
  return change.operation === "deleted" ? "/memories" : `/memories?path=${encodeURIComponent(change.path)}`;
}

export function ReviewMemoryChanges({ changes, onRestore, error }: { changes: MemoryVersion[]; onRestore: (change: MemoryVersion) => void; error?: string }) {
  const { locale, t } = useI18n();
  const [expandedPath, setExpandedPath] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [showAllGroups, setShowAllGroups] = useState(false);
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
  const formatTime = (value: string) => new Intl.DateTimeFormat(locale, { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
  const actorLabel = (actor: MemoryVersion["actor"]) => actor === "chat" ? t("review.memory.chat") : actor === "consolidation" ? t("review.memory.consolidation") : t("review.memory.manual");
  const operationLabel = (operation: MemoryVersion["operation"]) => operation === "created" ? t("review.memory.created") : operation === "modified" ? t("review.memory.modified") : t("review.memory.deleted");
  const visibleGroups = showAllGroups ? groups : groups.slice(0, 6);
  const hiddenGroupCount = groups.length - visibleGroups.length;

  return <section className="review-card review-memory-card">
    <div className="card-heading">
      <div><span className="card-kicker">{t("review.memory.kicker")}</span><h2>{t("review.memory.title")}</h2><p className="card-description">{t("review.memory.description")}</p></div>
      <span className="count-pill">{error ? "—" : groups.length}</span>
    </div>
    {error ? <div className="card-state card-state-error">{error}</div> : <>
      {changes.length ? <>
        <div className="memory-groups">{visibleGroups.map((group) => {
          const latest = group.changes[0];
          const expanded = expandedPath === group.path;
          const actorList = Array.from(new Set(group.changes.map((item) => actorLabel(item.actor)))).join(" · ");
          return <section className={`memory-group ${expanded ? "expanded" : ""}`} key={group.path}>
            <div className="memory-group-header">
              <span className={`memory-operation operation-${latest.operation}`}>{operationIcon(latest.operation)}</span>
              <div className="memory-change-copy">
                <div><Link href={memoryLink(latest)} className="memory-change-path">{group.path}</Link><span className="memory-group-count">{t("review.memory.times", { count: group.changes.length })}</span></div>
                <span className="memory-change-meta">{t("review.memory.latest", { operation: operationLabel(latest.operation), time: formatTime(latest.created_at), actors: actorList })}</span>
              </div>
              <button className="change-expand memory-group-toggle" type="button" aria-label={`${expanded ? t("review.memory.collapse") : t("review.memory.expand")}${group.path}`} aria-expanded={expanded} onClick={() => setExpandedPath(expanded ? null : group.path)}><ChevronDown size={15} /></button>
            </div>
            {expanded && <div className="memory-group-events">{group.changes.map((change, index) => {
              const eventExpanded = expandedId === change.id;
              const previous = group.changes[index + 1];
              return <div className={`memory-group-event ${eventExpanded ? "expanded" : ""}`} key={change.id}>
                <div className="memory-event-row">
                  <span className={`memory-operation operation-${change.operation}`}>{operationIcon(change.operation)}</span>
                  <div className="memory-event-copy"><span className={`actor-badge actor-${change.actor}`}>{actorLabel(change.actor)}</span><span className="memory-change-meta">{operationLabel(change.operation)} · {formatTime(change.created_at)}</span></div>
                  <button className="change-expand" type="button" aria-label={`${eventExpanded ? t("review.memory.collapse") : t("review.memory.expand")}${group.path} ${formatTime(change.created_at)}`} aria-expanded={eventExpanded} onClick={() => setExpandedId(eventExpanded ? null : change.id)}><ChevronDown size={14} /></button>
                  {change.operation === "deleted" && <button className="restore-button" type="button" onClick={() => onRestore(change)}><RotateCcw size={12} />{t("review.memory.restore")}</button>}
                </div>
                {eventExpanded && <div className="memory-change-detail"><Snapshot before={previous?.content} after={change.content} /></div>}
              </div>;
            })}</div>}
          </section>;
        })}</div>
        {groups.length > 6 && <button className="review-list-toggle" type="button" aria-expanded={showAllGroups} onClick={() => setShowAllGroups((value) => !value)}><ChevronDown size={14} />{showAllGroups ? t("review.memory.collapse") : `${t("review.memory.expand")} · +${hiddenGroupCount}`}</button>}
      </> : <div className="card-empty compact-empty"><strong>{t("review.memory.emptyTitle")}</strong><span>{t("review.memory.emptyDescription")}</span></div>}
      <div className="change-summary"><FileText size={13} />{t("review.memory.summary", { files: groups.length, chat: actorCounts.chat, consolidation: actorCounts.consolidation, manual: actorCounts.manual })}</div>
    </>}
  </section>;
}
