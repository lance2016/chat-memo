"use client";

import { useEffect, useState } from "react";
import { Archive, ArchiveRestore, BookOpen, CalendarClock, CalendarDays, Home, Plus, Settings2 } from "lucide-react";
import Image from "next/image";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { listConversations } from "@/lib/api";
import type { Conversation } from "@/lib/types";
import { SearchTrigger } from "@/components/global-search";
import { ThemeControl } from "@/components/theme-control";
import { LanguageControl } from "@/components/language-control";
import { useI18n } from "@/components/i18n-provider";
import type { TranslationKey } from "@/lib/i18n";
import { confirmAppNavigation } from "@/lib/navigation-guard";

export type WorkspacePage = "chat" | "memories" | "review" | "timeline" | "settings";

const conversationsChangedEvent = "chat-memo:conversations-changed";

export function notifyWorkspaceConversationsChanged() {
  window.dispatchEvent(new Event(conversationsChangedEvent));
}

const navigation = [
  { key: "chat" as const, href: "/", label: "nav.chat" as TranslationKey, icon: Home },
  { key: "memories" as const, href: "/memories", label: "nav.memories" as TranslationKey, icon: BookOpen },
  { key: "review" as const, href: "/review", label: "nav.review" as TranslationKey, icon: CalendarDays },
  { key: "timeline" as const, href: "/timeline", label: "nav.timeline" as TranslationKey, icon: CalendarClock },
  { key: "settings" as const, href: "/settings", label: "nav.settings" as TranslationKey, icon: Settings2 },
];
const workspaceRoutes = navigation.map(({ href }) => href);
const warmedRoutes = new Set<string>();

const pageLabels: Record<WorkspacePage, TranslationKey> = {
  chat: "nav.chat",
  memories: "nav.memories",
  review: "nav.review",
  timeline: "nav.timeline",
  settings: "nav.settings",
};

export function MemoryMark({ compact = false }: { compact?: boolean }) {
  return <span className={`memory-mark ${compact ? "compact" : ""}`} aria-hidden="true">
    <Image className="memory-mark-image" src="/morning-memory-logo.png" alt="" width={80} height={80} sizes={compact ? "80px" : "48px"} />
  </span>;
}

export function MemoryBrand() {
  const { t } = useI18n();
  return <Link className="memory-brand-link" href="/" aria-label={t("workspace.backHome")} onClick={(event) => { if (!confirmAppNavigation()) event.preventDefault(); }}>
    <Image className="memory-brand-lockup" src="/morning-memory-wordmark.png" alt="朝花夕拾" width={220} height={59} sizes="(max-width: 980px) 56px, 220px" priority />
  </Link>;
}

export function WorkspaceNav({ active, className = "" }: { active: WorkspacePage; className?: string }) {
  const { t } = useI18n();
  const router = useRouter();
  const prefetch = (href: string) => {
    if (warmedRoutes.has(href)) return;
    warmedRoutes.add(href);
    if (process.env.NODE_ENV === "development") {
      // App Router disables router.prefetch() in development. A background GET
      // still makes next dev compile the route before the user clicks it.
      void fetch(href, { credentials: "same-origin", cache: "no-store" }).catch(() => warmedRoutes.delete(href));
      return;
    }
    void router.prefetch(href);
  };

  return <nav className={`workspace-nav ${className}`} aria-label={t("nav.main")}>
    {navigation.map(({ key, href, label, icon: Icon }) => key === active
      ? <span className="active" aria-current="page" key={key}><Icon size={18} /><span>{t(label)}</span></span>
      : <Link href={href} key={key} onPointerEnter={() => prefetch(href)} onFocus={() => prefetch(href)} onTouchStart={() => prefetch(href)} onClick={(event) => { if (!confirmAppNavigation()) event.preventDefault(); }}><Icon size={18} /><span>{t(label)}</span></Link>)}
  </nav>;
}

export function WorkspaceProfile() {
  const { t } = useI18n();
  return <div className="workspace-profile">
    <Link href="/settings" className="workspace-avatar" aria-label={t("workspace.openSettings")}>L</Link>
    <span><strong>Lance</strong><small>{t("workspace.localMemory")}</small></span>
    <Link href="/settings" className="workspace-profile-settings" aria-label={t("workspace.openSettings")}><Settings2 size={15} /></Link>
  </div>;
}

export function WorkspaceTopbar({ active }: { active: WorkspacePage; subtitle?: string }) {
  const { t } = useI18n();
  const [recentConversations, setRecentConversations] = useState<Conversation[]>([]);
  const [showArchived, setShowArchived] = useState(false);
  const activeRoute = navigation.find(({ key }) => key === active)?.href;

  useEffect(() => {
    if (process.env.NODE_ENV !== "development") return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      for (const href of workspaceRoutes) {
        if (href === activeRoute || warmedRoutes.has(href)) continue;
        warmedRoutes.add(href);
        void fetch(href, { credentials: "same-origin", cache: "no-store", signal: controller.signal }).catch(() => warmedRoutes.delete(href));
      }
    }, 300);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [activeRoute]);

  useEffect(() => {
    let activeRequest = true;
    const refresh = () => { void listConversations(8, showArchived).then((items) => { if (activeRequest) setRecentConversations(items); }).catch(() => undefined); };
    refresh();
    window.addEventListener(conversationsChangedEvent, refresh);
    return () => {
      activeRequest = false;
      window.removeEventListener(conversationsChangedEvent, refresh);
    };
  }, [showArchived]);

  return <>
    <aside className="workspace-sidebar">
      <MemoryBrand />
      <Link className="workspace-capture-button workspace-sidebar-capture" href="/" onClick={(event) => { if (!confirmAppNavigation()) event.preventDefault(); else setShowArchived(false); }}><Plus size={15} />{t("workspace.newThought")}</Link>
      <WorkspaceNav active={active} />
      <button className="workspace-sidebar-secondary" type="button" onClick={() => setShowArchived((value) => !value)}>{showArchived ? <ArchiveRestore size={15} /> : <Archive size={15} />}{showArchived ? t("workspace.backToRecent") : t("workspace.archived")}</button>
      <div className="workspace-sidebar-recent">
        <span>{showArchived ? t("workspace.archived") : t("workspace.recent")}</span>
        {recentConversations.map((conversation) => <Link href={`/?conversation=${conversation.id}`} key={conversation.id} onClick={(event) => { if (!confirmAppNavigation()) event.preventDefault(); }}><i />{conversation.title}</Link>)}
        {!recentConversations.length && <small>{showArchived ? t("workspace.noArchived") : t("workspace.noRecent")}</small>}
      </div>
      <WorkspaceProfile />
    </aside>
    <header className="workspace-desktop-topbar">
      <div className="workspace-breadcrumb"><span>{t("workspace.root")}</span><b>›</b><strong>{t(pageLabels[active])}</strong></div>
      <div className="workspace-topbar-tools"><SearchTrigger /><LanguageControl /><ThemeControl /></div>
    </header>
    <header className="workspace-mobile-topbar">
      <MemoryBrand />
      <div><SearchTrigger /><LanguageControl /><ThemeControl /></div>
    </header>
    <WorkspaceNav active={active} className="workspace-mobile-nav" />
  </>;
}

/** Keep the workspace chrome mounted while a route waits for client data. */
export function WorkspacePageFallback({ active, message, messageKey }: { active: WorkspacePage; message?: string; messageKey?: TranslationKey }) {
  const { t } = useI18n();
  return <div className="workspace-content-loading" data-workspace-page={active}><div className="page-loading">{messageKey ? t(messageKey) : message}</div></div>;
}
