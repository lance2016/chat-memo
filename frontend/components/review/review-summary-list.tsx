import { ArrowUpRight, Clock3, FileText } from "lucide-react";
import Link from "next/link";
import type { ConversationSummary } from "@/lib/types";

function formatTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

export function ReviewSummaryList({ summaries, error }: { summaries: ConversationSummary[]; error?: string }) {
  return <section className="review-card review-primary-card"><div className="card-heading"><div><span className="card-kicker">WHAT HAPPENED</span><h2>今天聊了什么</h2><p className="card-description">每日整理生成的会话摘要，点击可以回到原始对话。</p></div><span className="count-pill">{error ? "—" : summaries.length}</span></div>{error ? <div className="card-state card-state-error">{error}</div> : summaries.length ? <div className="summary-timeline">{summaries.map((summary) => <Link className="summary-timeline-item" href={`/?conversation=${summary.conversation_id}`} key={summary.id}><span className="timeline-dot"><FileText size={13} /></span><span className="summary-timeline-copy"><strong>{summary.conversation_title}</strong><span>{summary.summary}</span><time><Clock3 size={12} />{formatTime(summary.created_at)}</time></span><ArrowUpRight size={14} className="summary-arrow" /></Link>)}</div> : <div className="card-empty compact-empty"><strong>今天还没有整理摘要</strong><span>运行一次“整理这一天”，让助手把对话提炼成可回看的线索。</span></div>}</section>;
}
