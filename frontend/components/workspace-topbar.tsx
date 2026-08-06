import { BookOpen, CalendarDays, CalendarCheck2, Home, Settings2, UsersRound } from "lucide-react";
import Link from "next/link";
import { SearchTrigger } from "@/components/global-search";
import { ThemeControl } from "@/components/theme-control";
import { confirmAppNavigation } from "@/lib/navigation-guard";

export type WorkspacePage = "chat" | "memories" | "review" | "settings";

const navigation = [
  { key: "chat" as const, href: "/", label: "首页", icon: Home },
  { key: "memories" as const, href: "/memories", label: "记忆库", icon: BookOpen },
  { key: "review" as const, href: "/review", label: "每日回顾", icon: CalendarDays },
];

const memoryViews = [
  { href: "/memories?view=people", label: "重要的人", icon: UsersRound },
  { href: "/memories?view=plans", label: "计划与约定", icon: CalendarCheck2 },
];

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
    {memoryViews.map(({ href, label, icon: Icon }) => <Link href={href} key={href} onClick={(event) => { if (!confirmAppNavigation()) event.preventDefault(); }}><Icon size={18} /><span>{label}</span></Link>)}
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
  return <>
    <aside className="workspace-sidebar">
      <MemoryBrand />
      <WorkspaceNav active={active} />
      <div className="workspace-sidebar-tools"><SearchTrigger /><ThemeControl /></div>
      <WorkspaceProfile />
    </aside>
    <header className="workspace-mobile-topbar">
      <MemoryBrand />
      <div><SearchTrigger /><ThemeControl /></div>
    </header>
  </>;
}
