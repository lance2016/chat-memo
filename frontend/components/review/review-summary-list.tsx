"use client";

import { useState } from "react";
import { ArrowUpRight, ChevronDown, Clock3, FileText } from "lucide-react";
import Link from "next/link";
import type { ConversationSummary } from "@/lib/types";
import { useI18n } from "@/components/i18n-provider";

export function ReviewSummaryList({ summaries, error }: { summaries: ConversationSummary[]; error?: string }) {
  const { locale, t } = useI18n();
  const [expanded, setExpanded] = useState(false);
  const formatTime = (value: string) => new Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit" }).format(new Date(value));
  const visibleSummaries = expanded ? summaries : summaries.slice(0, 5);
  const hiddenCount = summaries.length - visibleSummaries.length;

  return <section className="review-card review-primary-card review-summary-card">
    <div className="card-heading">
      <div><span className="card-kicker">{t("review.summary.kicker")}</span><h2>{t("review.summary.title")}</h2><p className="card-description">{t("review.summary.description")}</p></div>
      <span className="count-pill">{error ? "—" : summaries.length}</span>
    </div>
    {error ? <div className="card-state card-state-error">{error}</div> : summaries.length ? <>
      <div className="summary-timeline">{visibleSummaries.map((summary) => <Link className="summary-timeline-item" href={`/?conversation=${summary.conversation_id}`} key={summary.id}><span className="timeline-dot"><FileText size={13} /></span><span className="summary-timeline-copy"><strong>{summary.conversation_title}</strong><span>{summary.summary}</span><time><Clock3 size={12} />{formatTime(summary.created_at)}</time></span><ArrowUpRight size={14} className="summary-arrow" /></Link>)}</div>
      {summaries.length > 5 && <button className="review-list-toggle" type="button" aria-expanded={expanded} onClick={() => setExpanded((value) => !value)}><ChevronDown size={14} />{expanded ? t("review.memory.collapse") : `${t("review.memory.expand")} · +${hiddenCount}`}</button>}
    </> : <div className="card-empty compact-empty"><strong>{t("review.summary.emptyTitle")}</strong><span>{t("review.summary.emptyDescription")}</span></div>}
  </section>;
}
