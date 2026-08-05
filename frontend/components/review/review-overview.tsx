import { Activity, BookOpen, MessageSquare, Sparkles } from "lucide-react";
import type { Conversation, ConversationSummary, DailyUsage, MemoryVersion } from "@/lib/types";

function cacheRate(usage: DailyUsage | undefined) {
  if (!usage || usage.input_tokens <= 0) return "—";
  return `${Math.round(usage.cached_tokens / usage.input_tokens * 100)}%`;
}

export function ReviewOverview({ conversations, summaries, changes, selectedUsage }: { conversations: Conversation[]; summaries: ConversationSummary[]; changes: MemoryVersion[]; selectedUsage?: DailyUsage }) {
  return <section className="review-overview" aria-label="当天概览">
    <div className="overview-lead"><Sparkles size={16} /><div><strong>今天的工作脉络</strong><span>{summaries.length ? `已经整理出 ${summaries.length} 个会话摘要` : "还没有生成会话摘要"}</span></div></div>
    <div className="overview-metrics"><div><MessageSquare size={14} /><span>会话</span><strong>{conversations.length}</strong></div><div><Sparkles size={14} /><span>摘要</span><strong>{summaries.length}</strong></div><div><BookOpen size={14} /><span>记忆变更</span><strong>{changes.length}</strong></div><div><Activity size={14} /><span>缓存命中</span><strong>{cacheRate(selectedUsage)}</strong></div></div>
  </section>;
}
