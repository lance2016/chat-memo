"use client";

import { useState } from "react";
import { ArrowUpRight, ChevronDown, Clock3, MessageSquare } from "lucide-react";
import Link from "next/link";
import type { Conversation } from "@/lib/types";
import { useI18n } from "@/components/i18n-provider";

export function ReviewConversationList({ conversations, error }: { conversations: Conversation[]; error?: string }) {
  const { locale, t } = useI18n();
  const [showAll, setShowAll] = useState(false);
  const formatTime = (value: string) => new Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit" }).format(new Date(value));
  const visibleConversations = showAll ? conversations : conversations.slice(0, 6);
  const hiddenCount = conversations.length - visibleConversations.length;

  return <details className="review-card review-collapsible-card review-conversation-card" open={error ? true : undefined}>
    <summary className="card-heading review-collapsible-heading">
      <div><span className="card-kicker">{t("review.chats.kicker")}</span><h2>{t("review.chats.title")}</h2></div>
      <span className="review-collapsible-meta"><span className="count-pill">{error ? "—" : conversations.length}</span><ChevronDown size={15} /></span>
    </summary>
    {error ? <div className="card-state card-state-error">{error}</div> : conversations.length ? <>
      <div className="review-list compact-list">{visibleConversations.map((conversation) => <Link className="review-conversation" href={`/?conversation=${conversation.id}`} key={conversation.id}><MessageSquare size={15} /><span>{conversation.title}</span><time><Clock3 size={12} />{formatTime(conversation.updated_at)}</time><ArrowUpRight size={13} /></Link>)}</div>
      {conversations.length > 6 && <button className="review-list-toggle" type="button" aria-expanded={showAll} onClick={() => setShowAll((value) => !value)}><ChevronDown size={14} />{showAll ? t("review.memory.collapse") : `${t("review.memory.expand")} · +${hiddenCount}`}</button>}
    </> : <div className="card-empty compact-empty"><span>{t("review.chats.empty")}</span></div>}
  </details>;
}
