import { ArrowUpRight, Clock3, MessageSquare } from "lucide-react";
import Link from "next/link";
import type { Conversation } from "@/lib/types";

function formatTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

export function ReviewConversationList({ conversations, error }: { conversations: Conversation[]; error?: string }) {
  return <section className="review-card"><div className="card-heading"><div><span className="card-kicker">SOURCE CHATS</span><h2>相关会话</h2></div><span className="count-pill">{error ? "—" : conversations.length}</span></div>{error ? <div className="card-state card-state-error">{error}</div> : conversations.length ? <div className="review-list compact-list">{conversations.map((conversation) => <Link className="review-conversation" href={`/?conversation=${conversation.id}`} key={conversation.id}><MessageSquare size={15} /><span>{conversation.title}</span><time><Clock3 size={12} />{formatTime(conversation.updated_at)}</time><ArrowUpRight size={13} /></Link>)}</div> : <div className="card-empty compact-empty"><span>当天没有更新过的会话。</span></div>}</section>;
}
