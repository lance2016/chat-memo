import { BookOpen, MessageSquare, Sparkles } from "lucide-react";
import type { Conversation, ConversationSummary, MemoryVersion } from "@/lib/types";

export function ReviewOverview({ conversations, summaries, changes }: { conversations: Conversation[]; summaries: ConversationSummary[]; changes: MemoryVersion[] }) {
  return <section className="review-overview" aria-label="当天概览">
    <div className="overview-lead"><Sparkles size={16} /><div><strong>{summaries.length ? "这一天已经整理完成" : "等待整理这一天"}</strong><span>{summaries.length ? `形成 ${summaries.length} 条摘要和 ${changes.length} 次记忆变更` : "整理后会在这里汇总结论和记忆变化"}</span></div></div>
    <div className="overview-metrics"><div><MessageSquare size={14} /><span>相关会话</span><strong>{conversations.length}</strong></div><div><Sparkles size={14} /><span>会话摘要</span><strong>{summaries.length}</strong></div><div><BookOpen size={14} /><span>记忆变更</span><strong>{changes.length}</strong></div></div>
  </section>;
}
