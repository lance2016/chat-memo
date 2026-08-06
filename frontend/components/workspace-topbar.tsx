"use client";

import { useEffect, useState } from "react";
import { BookOpen, CalendarDays, Home, MessageSquare, Plus, Settings2 } from "lucide-react";
import Link from "next/link";
import { listConversations } from "@/lib/api";
import type { Conversation } from "@/lib/types";
import { SearchTrigger } from "@/components/global-search";
import { ThemeControl } from "@/components/theme-control";
import { confirmAppNavigation } from "@/lib/navigation-guard";

export type WorkspacePage = "chat" | "memories" | "review" | "settings";

const navigation = [
  { key: "chat" as const, href: "/", label: "首页", icon: Home },
  { key: "memories" as const, href: "/memories", label: "记忆库", icon: BookOpen },
  { key: "review" as const, href: "/review", label: "每日回顾", icon: CalendarDays },
];

const pageLabels: Record<WorkspacePage, string> = {
  chat: "首页",
  memories: "记忆库",
  review: "每日回顾",
  settings: "设置",
};

export function MemoryMark({ compact = false }: { compact?: boolean }) {
  return <span className={`memory-mark ${compact ? "compact" : ""}`} aria-hidden="true">
    <svg viewBox="0 0 48 48" fill="none">
      <path d="M12 32V17.5c0-4 4.7-6.2 7.8-3.7L24 17.2l4.2-3.4c3.1-2.5 7.8-.3 7.8 3.7V32M12 30.5c4.4 0 8.6 1.8 12 5 3.4-3.2 7.6-5 12-5" stroke="currentColor" strokeWidth="4" strokeLinecap="round" strokeLinejoin="round" />
      <g fill="#f2a869"><circle cx="24" cy="7.3" r="2.2" /><circle cx="26.2" cy="9.5" r="2.2" /><circle cx="24" cy="11.7" r="2.2" /><circle cx="21.8" cy="9.5" r="2.2" /></g>
      <circle cx="24" cy="9.5" r="1.45" fill="#fff7e9" />
    </svg>
  </span>;
}

export function MemoryBrand() {
  return <Link className="memory-brand-link" href="/" aria-label="返回朝花夕拾首页" onClick={(event) => { if (!confirmAppNavigation()) event.preventDefault(); }}>
    <MemoryMark />
    <span><strong>朝花夕拾</strong><small>PERSONAL MEMORY</small></span>
  </Link>;
}

export function WorkspaceNav({ active, className = "" }: { active: WorkspacePage; className?: string }) {
  return <nav className={`workspace-nav ${className}`} aria-label="主导航">
    {navigation.map(({ key, href, label, icon: Icon }) => key === active
      ? <span className="active" aria-current="page" key={key}><Icon size={18} /><span>{label}</span></span>
      : <Link href={href} key={key} onClick={(event) => { if (!confirmAppNavigation()) event.preventDefault(); }}><Icon size={18} /><span>{label}</span></Link>)}
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

  useEffect(() => {
    let activeRequest = true;
    void listConversations(3).then((items) => { if (activeRequest) setRecentConversations(items); }).catch(() => undefined);
    return () => { activeRequest = false; };
  }, []);

  return <>
    <aside className="workspace-sidebar">
      <MemoryBrand />
      <Link className="workspace-sidebar-capture" href="/"><Plus size={15} />记录新想法</Link>
      <WorkspaceNav active={active} />
      <Link className="workspace-sidebar-secondary" href="/"><MessageSquare size={15} />返回最近对话</Link>
      <div className="workspace-sidebar-recent">
        <span>最近对话</span>
        {recentConversations.map((conversation) => <Link href={`/?conversation=${conversation.id}`} key={conversation.id}><i />{conversation.title}</Link>)}
        {!recentConversations.length && <small>从首页开始一段新的记录</small>}
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
  return <div className="workspace-loading-shell">
    <WorkspaceTopbar active={active} />
    <main className="workspace-loading-content"><div className="page-loading">{message}</div></main>
  </div>;
}
