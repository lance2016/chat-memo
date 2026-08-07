"use client";

import { useEffect, useRef, useState } from "react";
import { Archive, ArchiveRestore, BookOpen, CalendarClock, CalendarDays, Home, MoreHorizontal, Pencil, Settings2, Trash2 } from "lucide-react";
import Image from "next/image";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { archiveConversation, deleteConversation, errorMessage, listConversations, updateConversation } from "@/lib/api";
import type { Conversation } from "@/lib/types";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { SearchTrigger } from "@/components/global-search";
import { InputDialog } from "@/components/input-dialog";
import { ThemeControl } from "@/components/theme-control";
import { LanguageControl } from "@/components/language-control";
import { useI18n } from "@/components/i18n-provider";
import type { TranslationKey } from "@/lib/i18n";
import { confirmAppNavigation } from "@/lib/navigation-guard";

export type WorkspacePage = "chat" | "memories" | "review" | "timeline" | "settings";

export const conversationsChangedEvent = "chat-memo:conversations-changed";

export type WorkspaceConversationChange =
  | { type: "renamed"; conversation: Conversation }
  | { type: "archived"; conversation: Conversation; archived: boolean }
  | { type: "deleted"; conversationId: number };

export function notifyWorkspaceConversationsChanged(detail?: WorkspaceConversationChange) {
  window.dispatchEvent(detail
    ? new CustomEvent<WorkspaceConversationChange>(conversationsChangedEvent, { detail })
    : new Event(conversationsChangedEvent));
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

function currentConversationId() {
  if (typeof window === "undefined") return null;
  const value = Number(new URLSearchParams(window.location.search).get("conversation"));
  return Number.isFinite(value) && value > 0 ? value : null;
}

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
  const router = useRouter();
  const [recentConversations, setRecentConversations] = useState<Conversation[]>([]);
  const [showArchived, setShowArchived] = useState(false);
  const [menuTarget, setMenuTarget] = useState<Conversation | null>(null);
  const [renameTarget, setRenameTarget] = useState<Conversation | null>(null);
  const [renameDraft, setRenameDraft] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<Conversation | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [actionError, setActionError] = useState("");
  const menuRef = useRef<HTMLDivElement>(null);
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
    // Keep the workspace rail useful without turning every page into a second
    // conversation history. The full list still lives on Home/search.
    const refresh = () => { void listConversations(5, showArchived).then((items) => { if (activeRequest) setRecentConversations(items); }).catch(() => undefined); };
    refresh();
    window.addEventListener(conversationsChangedEvent, refresh);
    return () => {
      activeRequest = false;
      window.removeEventListener(conversationsChangedEvent, refresh);
    };
  }, [showArchived]);

  useEffect(() => {
    if (!menuTarget) return;
    const closeOnPointerDown = (event: PointerEvent) => {
      if (event.target instanceof Node && menuRef.current?.contains(event.target)) return;
      if (event.target instanceof Element && event.target.closest(".workspace-sidebar-conversation-more")) return;
      setMenuTarget(null);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenuTarget(null);
    };
    document.addEventListener("pointerdown", closeOnPointerDown);
    document.addEventListener("keydown", closeOnEscape);
    window.requestAnimationFrame(() => menuRef.current?.querySelector<HTMLButtonElement>("button")?.focus());
    return () => {
      document.removeEventListener("pointerdown", closeOnPointerDown);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [menuTarget]);

  const openRename = (conversation: Conversation) => {
    setMenuTarget(null);
    setRenameDraft(conversation.title);
    setRenameTarget(conversation);
    setActionError("");
  };

  const confirmRename = async () => {
    if (!renameTarget || busyId !== null) return;
    const title = renameDraft.trim();
    if (!title || title === renameTarget.title) { setRenameTarget(null); return; }
    setBusyId(renameTarget.id);
    setActionError("");
    try {
      const updated = await updateConversation(renameTarget.id, { title });
      setRecentConversations((current) => current.map((conversation) => conversation.id === updated.id ? updated : conversation));
      setRenameTarget(null);
      notifyWorkspaceConversationsChanged({ type: "renamed", conversation: updated });
    } catch (cause) {
      setRenameTarget(null);
      setActionError(errorMessage(cause, t("workspace.actionFailed")));
    } finally {
      setBusyId(null);
    }
  };

  const toggleConversationArchived = async (conversation: Conversation) => {
    if (busyId !== null) return;
    const archived = !showArchived;
    setBusyId(conversation.id);
    setActionError("");
    try {
      const updated = await archiveConversation(conversation.id, archived);
      setMenuTarget(null);
      setRecentConversations((current) => current.filter((item) => item.id !== conversation.id));
      notifyWorkspaceConversationsChanged({ type: "archived", conversation: updated, archived });
      if (currentConversationId() === conversation.id) router.push("/");
      if (!archived) setShowArchived(false);
    } catch (cause) {
      setMenuTarget(null);
      setActionError(errorMessage(cause, t("workspace.actionFailed")));
    } finally {
      setBusyId(null);
    }
  };

  const confirmDelete = async () => {
    if (!deleteTarget || busyId !== null) return;
    const conversation = deleteTarget;
    setBusyId(conversation.id);
    setActionError("");
    try {
      await deleteConversation(conversation.id);
      setRecentConversations((current) => current.filter((item) => item.id !== conversation.id));
      setDeleteTarget(null);
      notifyWorkspaceConversationsChanged({ type: "deleted", conversationId: conversation.id });
      if (currentConversationId() === conversation.id) router.push("/");
    } catch (cause) {
      setDeleteTarget(null);
      setActionError(errorMessage(cause, t("workspace.actionFailed")));
    } finally {
      setBusyId(null);
    }
  };

  return <>
    <aside className="workspace-sidebar">
      <MemoryBrand />
      <WorkspaceNav active={active} />
      <div className="workspace-sidebar-recent">
        <div className="workspace-sidebar-recent-heading">
          <span>{showArchived ? t("workspace.archived") : t("workspace.recent")}</span>
        </div>
        {recentConversations.map((conversation) => <div className="workspace-sidebar-conversation" key={conversation.id}>
          <Link href={`/?conversation=${conversation.id}`} title={conversation.title} onClick={(event) => { setMenuTarget(null); if (!confirmAppNavigation()) event.preventDefault(); }}><span>{conversation.title}</span></Link>
          <button
            className="workspace-sidebar-conversation-more"
            type="button"
            aria-label={t("workspace.conversationActions", { title: conversation.title })}
            aria-haspopup="menu"
            aria-expanded={menuTarget?.id === conversation.id}
            onClick={() => setMenuTarget((current) => current?.id === conversation.id ? null : conversation)}
          ><MoreHorizontal size={16} /></button>
          {menuTarget?.id === conversation.id && <div className="workspace-conversation-menu" role="menu" aria-label={t("workspace.conversationActions", { title: conversation.title })} ref={menuRef}>
            <button type="button" role="menuitem" onClick={() => openRename(conversation)} disabled={busyId !== null}><Pencil size={15} /><span>{t("workspace.rename")}</span></button>
            <button type="button" role="menuitem" onClick={() => void toggleConversationArchived(conversation)} disabled={busyId !== null}>{showArchived ? <ArchiveRestore size={15} /> : <Archive size={15} />}<span>{showArchived ? t("workspace.restore") : t("workspace.archive")}</span></button>
            <button className="danger" type="button" role="menuitem" onClick={() => { setMenuTarget(null); setDeleteTarget(conversation); setActionError(""); }} disabled={busyId !== null}><Trash2 size={15} /><span>{t("workspace.delete")}</span></button>
          </div>}
        </div>)}
        {!recentConversations.length && <small>{showArchived ? t("workspace.noArchived") : t("workspace.noRecent")}</small>}
        {actionError && <small className="workspace-sidebar-recent-error" role="alert">{actionError}</small>}
        <button className="workspace-sidebar-recent-filter" type="button" onClick={() => { setMenuTarget(null); setActionError(""); setShowArchived((value) => !value); }}>
          {showArchived ? <ArchiveRestore size={13} /> : <Archive size={13} />}
          <span>{showArchived ? t("workspace.backToRecent") : t("workspace.archived")}</span>
        </button>
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
    <ConfirmDialog
      open={deleteTarget !== null}
      title={t("chat.deleteTitle")}
      description={t("chat.deleteDescription")}
      subject={deleteTarget?.title}
      warning={deleteTarget && currentConversationId() === deleteTarget.id ? t("chat.deleteWarning") : undefined}
      confirmLabel={t("chat.deleteConfirm")}
      busy={busyId === deleteTarget?.id}
      onCancel={() => setDeleteTarget(null)}
      onConfirm={() => void confirmDelete()}
    />
    <InputDialog
      open={renameTarget !== null}
      title={t("chat.rename")}
      description={t("chat.renameDescription")}
      value={renameDraft}
      onChange={setRenameDraft}
      onCancel={() => setRenameTarget(null)}
      onConfirm={() => void confirmRename()}
      busy={busyId === renameTarget?.id}
    />
  </>;
}

/** Keep the workspace chrome mounted while a route waits for client data. */
export function WorkspacePageFallback({ active, message, messageKey }: { active: WorkspacePage; message?: string; messageKey?: TranslationKey }) {
  const { t } = useI18n();
  return <div className="workspace-content-loading" data-workspace-page={active}><div className="page-loading">{messageKey ? t(messageKey) : message}</div></div>;
}
