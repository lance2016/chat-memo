"use client";

import { useEffect, useState } from "react";
import { Archive, ArchiveRestore, BookOpen, CalendarDays, Home, Plus, Settings2 } from "lucide-react";
import Image from "next/image";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { listConversations } from "@/lib/api";
import type { Conversation } from "@/lib/types";
import { SearchTrigger } from "@/components/global-search";
import { ThemeControl } from "@/components/theme-control";
import { confirmAppNavigation } from "@/lib/navigation-guard";

export type WorkspacePage = "chat" | "memories" | "review" | "settings";

const conversationsChangedEvent = "chat-memo:conversations-changed";

export function notifyWorkspaceConversationsChanged() {
  window.dispatchEvent(new Event(conversationsChangedEvent));
}

const navigation = [
  { key: "chat" as const, href: "/", label: "首页", icon: Home },
  { key: "memories" as const, href: "/memories", label: "记忆库", icon: BookOpen },
  { key: "review" as const, href: "/review", label: "每日回顾", icon: CalendarDays },
];
const workspaceRoutes = [...navigation.map(({ href }) => href), "/settings"];
const warmedRoutes = new Set<string>();

const pageLabels: Record<WorkspacePage, string> = {
  chat: "首页",
  memories: "记忆库",
  review: "每日回顾",
  settings: "设置",
};

export function MemoryMark({ compact = false }: { compact?: boolean }) {
  return <span className={`memory-mark ${compact ? "compact" : ""}`} aria-hidden="true">
    <Image className="memory-mark-image" src="/morning-memory-logo.png" alt="" width={80} height={80} sizes={compact ? "80px" : "48px"} />
  </span>;
}

export function MemoryBrand() {
  return <Link className="memory-brand-link" href="/" aria-label="返回朝花夕拾首页" onClick={(event) => { if (!confirmAppNavigation()) event.preventDefault(); }}>
    <Image className="memory-brand-lockup" src="/morning-memory-wordmark.png" alt="朝花夕拾" width={220} height={59} sizes="(max-width: 980px) 56px, 220px" priority />
  </Link>;
}

export function WorkspaceNav({ active, className = "" }: { active: WorkspacePage; className?: string }) {
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

  return <nav className={`workspace-nav ${className}`} aria-label="主导航">
    {navigation.map(({ key, href, label, icon: Icon }) => key === active
      ? <span className="active" aria-current="page" key={key}><Icon size={18} /><span>{label}</span></span>
      : <Link href={href} key={key} onPointerEnter={() => prefetch(href)} onFocus={() => prefetch(href)} onTouchStart={() => prefetch(href)} onClick={(event) => { if (!confirmAppNavigation()) event.preventDefault(); }}><Icon size={18} /><span>{label}</span></Link>)}
  </nav>;
}

export function WorkspaceProfile() {
  return <div className="workspace-profile">
    <Link href="/settings" className="workspace-avatar" aria-label="打开设置">L</Link>
    <span><strong>Lance</strong><small>记忆已保存在本地</small></span>
    <Link href="/settings" className="workspace-profile-settings" aria-label="打开设置"><Settings2 size={15} /></Link>
  </div>;
}

export function WorkspaceTopbar({ active }: { active: WorkspacePage; subtitle?: string }) {
  const [recentConversations, setRecentConversations] = useState<Conversation[]>([]);
  const [showArchived, setShowArchived] = useState(false);
  const activeRoute = active === "settings" ? "/settings" : navigation.find(({ key }) => key === active)?.href;

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
      <Link className="workspace-capture-button workspace-sidebar-capture" href="/" onClick={(event) => { if (!confirmAppNavigation()) event.preventDefault(); else setShowArchived(false); }}><Plus size={15} />记录新想法</Link>
      <WorkspaceNav active={active} />
      <button className="workspace-sidebar-secondary" type="button" onClick={() => setShowArchived((value) => !value)}>{showArchived ? <ArchiveRestore size={15} /> : <Archive size={15} />}{showArchived ? "返回最近对话" : "已归档对话"}</button>
      <div className="workspace-sidebar-recent">
        <span>{showArchived ? "已归档对话" : "最近对话"}</span>
        {recentConversations.map((conversation) => <Link href={`/?conversation=${conversation.id}`} key={conversation.id} onClick={(event) => { if (!confirmAppNavigation()) event.preventDefault(); }}><i />{conversation.title}</Link>)}
        {!recentConversations.length && <small>{showArchived ? "没有已归档的对话" : "从首页开始一段新的记录"}</small>}
      </div>
      <WorkspaceProfile />
    </aside>
    <header className="workspace-desktop-topbar">
      <div className="workspace-breadcrumb"><span>我的记忆</span><b>›</b><strong>{pageLabels[active]}</strong></div>
      <div className="workspace-topbar-tools"><SearchTrigger /><ThemeControl /></div>
    </header>
    <header className="workspace-mobile-topbar">
      <MemoryBrand />
      <div><SearchTrigger /><ThemeControl /></div>
    </header>
  </>;
}

/** Keep the workspace chrome mounted while a route waits for client data. */
export function WorkspacePageFallback({ active, message }: { active: WorkspacePage; message: string }) {
  return <div className="workspace-content-loading" data-workspace-page={active}><div className="page-loading">{message}</div></div>;
}
