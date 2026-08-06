import { BookOpen, CalendarDays, MessageSquare, Settings2 } from "lucide-react";
import Link from "next/link";
import { SearchTrigger } from "@/components/global-search";
import { ThemeControl } from "@/components/theme-control";

export type WorkspacePage = "chat" | "memories" | "review" | "settings";

const navigation: Array<{ key: WorkspacePage; href: string; label: string; icon: typeof MessageSquare }> = [
  { key: "chat", href: "/", label: "聊天", icon: MessageSquare },
  { key: "memories", href: "/memories", label: "记忆管理", icon: BookOpen },
  { key: "review", href: "/review", label: "每日回顾", icon: CalendarDays },
  { key: "settings", href: "/settings", label: "设置", icon: Settings2 },
];

export function WorkspaceNav({ active, className = "" }: { active: WorkspacePage; className?: string }) {
  return <nav className={`workspace-nav ${className}`} aria-label="主导航">
    {navigation.map(({ key, href, label, icon: Icon }) => key === active ? <span className="active" aria-current="page" key={key}><Icon size={14} /><span>{label}</span></span> : <Link href={href} key={key}><Icon size={14} /><span>{label}</span></Link>)}
  </nav>;
}

export function WorkspaceTopbar({ active, subtitle }: { active: WorkspacePage; subtitle: string }) {
  return <header className="workspace-topbar">
    <Link className="brand brand-home" href="/" aria-label="返回主页"><div className="brand-mark">✦</div><div><div className="brand-title">个人 AI 助手</div><div className="brand-subtitle">{subtitle}</div></div></Link>
    <div className="workspace-topbar-tools">
      <WorkspaceNav active={active} />
      <SearchTrigger />
      <ThemeControl />
    </div>
  </header>;
}
