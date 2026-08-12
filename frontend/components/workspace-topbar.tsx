"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState, type MouseEvent as ReactMouseEvent } from "react";
import { createPortal } from "react-dom";
import { Archive, ArchiveRestore, ArrowLeft, BookOpen, CalendarCheck2, ChevronUp, Clock3, Home, LoaderCircle, MessageCircle, MoreHorizontal, PanelLeftClose, PanelLeftOpen, Pencil, Plus, Settings2, Trash2 } from "lucide-react";
import Image from "next/image";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { archiveConversation, deleteConversation, errorMessage, listConversations, updateConversation } from "@/lib/api";
import type { Conversation } from "@/lib/types";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { SearchTrigger } from "@/components/global-search";
import { InputDialog } from "@/components/input-dialog";
import { useI18n } from "@/components/i18n-provider";
import type { TranslationKey } from "@/lib/i18n";
import { confirmAppNavigation } from "@/lib/navigation-guard";
import { useToast } from "@/components/toast";
import { newConversationEvent } from "@/components/keyboard-shortcuts";
import { defaultPreferences, isProfileAvatarImage, preferencesChangeEvent, profileInitials, readPreferences, type UserPreferences } from "@/lib/preferences";

export type WorkspacePage = "chat" | "memories" | "review" | "timeline" | "settings";

export const conversationsChangedEvent = "chat-memo:conversations-changed";
export const selectedConversationChangedEvent = "chat-memo:selected-conversation-changed";

export type WorkspaceConversationChange =
  | { type: "renamed"; conversation: Conversation }
  | { type: "archived"; conversation: Conversation; archived: boolean }
  | { type: "deleted"; conversationId: number }
  | { type: "cleared" };

export function notifyWorkspaceConversationsChanged(detail?: WorkspaceConversationChange) {
  window.dispatchEvent(detail
    ? new CustomEvent<WorkspaceConversationChange>(conversationsChangedEvent, { detail })
    : new Event(conversationsChangedEvent));
}

export function notifyWorkspaceSelectedConversationChanged(conversationId: number | null) {
  window.dispatchEvent(new CustomEvent<number | null>(selectedConversationChangedEvent, { detail: conversationId }));
}

const navigation = [
  { key: "chat" as const, href: "/", label: "nav.chat" as TranslationKey, icon: Home, tone: "blue" },
  { key: "memories" as const, href: "/memories", label: "nav.memories" as TranslationKey, icon: BookOpen, tone: "indigo" },
  { key: "review" as const, href: "/review", label: "nav.review" as TranslationKey, icon: CalendarCheck2, tone: "violet" },
  { key: "timeline" as const, href: "/timeline", label: "nav.timeline" as TranslationKey, icon: Clock3, tone: "orange" },
  { key: "settings" as const, href: "/settings", label: "nav.settings" as TranslationKey, icon: Settings2, tone: "gray" },
];
const sidebarNavigation = navigation.filter(({ key }) => key !== "chat" && key !== "settings");
const workspaceRoutes = navigation.map(({ href }) => href);
const warmedRoutes = new Set<string>();
const CONVERSATION_PAGE_SIZE = 20;

function currentConversationId() {
  if (typeof window === "undefined") return null;
  const value = Number(new URLSearchParams(window.location.search).get("conversation"));
  return Number.isFinite(value) && value > 0 ? value : null;
}

export function MemoryMark({ compact = false }: { compact?: boolean }) {
  return <span className={`memory-mark ${compact ? "compact" : ""}`} aria-hidden="true">
    <Image className="memory-mark-image" src="/morning-memory-logo.png" alt="" width={80} height={80} sizes={compact ? "80px" : "48px"} />
  </span>;
}

export function MemoryBrand({ iconOnly = false }: { iconOnly?: boolean }) {
  const { t } = useI18n();
  return <Link className="memory-brand-link" href="/" aria-label={t("workspace.backHome")} onClick={(event) => { if (!confirmAppNavigation()) event.preventDefault(); else notifyWorkspaceSelectedConversationChanged(null); }}>
    <MemoryMark />
    {!iconOnly && <span className="workspace-brand-copy"><strong>{t("app.title")}</strong><small>{t("app.tagline")}</small></span>}
  </Link>;
}

export function WorkspaceNav({ active, className = "", items = navigation }: { active: WorkspacePage; className?: string; items?: typeof navigation }) {
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
    {items.map(({ key, href, label, icon: Icon, tone }) => key === active
      ? <span className="active" aria-current="page" aria-label={t(label)} title={t(label)} data-label={t(label)} key={key}><span className={`workspace-nav-icon tone-${tone}`} aria-hidden="true"><Icon size={15} /></span><span className="workspace-nav-label">{t(label)}</span></span>
      : <Link href={href} aria-label={t(label)} title={t(label)} data-label={t(label)} key={key} onPointerEnter={() => prefetch(href)} onFocus={() => prefetch(href)} onTouchStart={() => prefetch(href)} onClick={(event) => { if (!confirmAppNavigation()) event.preventDefault(); }}><span className={`workspace-nav-icon tone-${tone}`} aria-hidden="true"><Icon size={15} /></span><span className="workspace-nav-label">{t(label)}</span></Link>)}
  </nav>;
}

export function WorkspaceProfile() {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [preferences, setPreferences] = useState<UserPreferences>(defaultPreferences);
  const shellRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const closeOnPointerDown = (event: PointerEvent) => {
      if (!shellRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setOpen(false);
      window.requestAnimationFrame(() => triggerRef.current?.focus());
    };
    document.addEventListener("pointerdown", closeOnPointerDown);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOnPointerDown);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);

  useEffect(() => {
    setPreferences(readPreferences());
    const sync = (event: Event) => {
      const detail = (event as CustomEvent<UserPreferences>).detail;
      setPreferences(detail ?? readPreferences());
    };
    window.addEventListener(preferencesChangeEvent(), sync);
    return () => window.removeEventListener(preferencesChangeEvent(), sync);
  }, []);

  const menuLinks = [
    { href: "/settings", label: t("nav.settings"), icon: Settings2 },
  ];

  return <div className="workspace-profile-shell" ref={shellRef}>
    {open && <div className="workspace-profile-menu" role="menu" aria-label={t("workspace.tools")}>
      {menuLinks.map(({ href, label, icon: Icon }) => <Link href={href} role="menuitem" key={href} onClick={(event) => { setOpen(false); if (!confirmAppNavigation()) event.preventDefault(); }}><Icon size={15} /><span>{label}</span></Link>)}
    </div>}
    <button className="workspace-profile" type="button" ref={triggerRef} aria-label={t("workspace.openMenu")} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
      <span className={`workspace-avatar tone-${preferences.profileTone}`} aria-hidden="true">{isProfileAvatarImage(preferences.profileAvatar) ? <>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={preferences.profileAvatar} alt="" />
      </> : profileInitials(preferences.profileName || "Lance", preferences.profileAvatar)}</span>
      <span className="workspace-profile-copy"><strong>{preferences.profileName || "Lance"}</strong><small>{t("workspace.localMemory")}</small></span>
      <ChevronUp className="workspace-profile-chevron" size={14} aria-hidden="true" />
    </button>
  </div>;
}

export function WorkspaceTopbar({ active, sidebarCollapsed = false, onSidebarCollapsedChange }: { active: WorkspacePage; subtitle?: string; sidebarCollapsed?: boolean; onSidebarCollapsedChange?: (collapsed: boolean) => void }) {
  const { t } = useI18n();
  const toast = useToast();
  const router = useRouter();
  const [selectedConversationId, setSelectedConversationId] = useState<number | null>(null);
  const [recentConversations, setRecentConversations] = useState<Conversation[]>([]);
  const [hasMoreConversations, setHasMoreConversations] = useState(true);
  const [loadingConversations, setLoadingConversations] = useState(false);
  const [conversationLoadError, setConversationLoadError] = useState("");
  const [showArchived, setShowArchived] = useState(false);
  const [menuTarget, setMenuTarget] = useState<Conversation | null>(null);
  const [renameTarget, setRenameTarget] = useState<Conversation | null>(null);
  const [renameDraft, setRenameDraft] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<Conversation | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [menuPosition, setMenuPosition] = useState<{ left: number; top: number } | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const recentListRef = useRef<HTMLDivElement>(null);
  const recentListEndRef = useRef<HTMLButtonElement>(null);
  const loadingConversationsRef = useRef(false);
  const conversationRequestVersionRef = useRef(0);
  const activeRoute = navigation.find(({ key }) => key === active)?.href;

  const loadMoreConversations = useCallback(() => {
    if (!hasMoreConversations || loadingConversationsRef.current) return;
    const requestVersion = conversationRequestVersionRef.current;
    const offset = recentConversations.length;
    loadingConversationsRef.current = true;
    setLoadingConversations(true);
    setConversationLoadError("");
    void listConversations(CONVERSATION_PAGE_SIZE, showArchived, offset)
      .then((items) => {
        if (requestVersion !== conversationRequestVersionRef.current) return;
        setRecentConversations((current) => {
          const knownIds = new Set(current.map((conversation) => conversation.id));
          return [...current, ...items.filter((conversation) => !knownIds.has(conversation.id))];
        });
        setHasMoreConversations(items.length === CONVERSATION_PAGE_SIZE);
      })
      .catch((cause: unknown) => {
        if (requestVersion === conversationRequestVersionRef.current) {
          setConversationLoadError(errorMessage(cause, t("workspace.actionFailed")));
        }
      })
      .finally(() => {
        if (requestVersion !== conversationRequestVersionRef.current) return;
        loadingConversationsRef.current = false;
        setLoadingConversations(false);
      });
  }, [hasMoreConversations, recentConversations.length, showArchived, t]);

  useEffect(() => {
    const syncFromLocation = () => setSelectedConversationId(currentConversationId());
    const handleSelectedConversation = (event: Event) => setSelectedConversationId((event as CustomEvent<number | null>).detail ?? null);
    syncFromLocation();
    window.addEventListener("popstate", syncFromLocation);
    window.addEventListener(selectedConversationChangedEvent, handleSelectedConversation);
    return () => {
      window.removeEventListener("popstate", syncFromLocation);
      window.removeEventListener(selectedConversationChangedEvent, handleSelectedConversation);
    };
  }, []);

  useEffect(() => {
    setSelectedConversationId(active === "chat" ? currentConversationId() : null);
  }, [active]);

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
    const refresh = () => {
      const requestVersion = ++conversationRequestVersionRef.current;
      loadingConversationsRef.current = true;
      setLoadingConversations(true);
      setConversationLoadError("");
      setHasMoreConversations(true);
      setRecentConversations([]);
      recentListRef.current?.scrollTo({ top: 0 });
      void listConversations(CONVERSATION_PAGE_SIZE, showArchived, 0)
        .then((items) => {
          if (!activeRequest || requestVersion !== conversationRequestVersionRef.current) return;
          setRecentConversations(items);
          setHasMoreConversations(items.length === CONVERSATION_PAGE_SIZE);
        })
        .catch((cause: unknown) => {
          if (activeRequest && requestVersion === conversationRequestVersionRef.current) {
            setConversationLoadError(errorMessage(cause, t("workspace.actionFailed")));
          }
        })
        .finally(() => {
          if (!activeRequest || requestVersion !== conversationRequestVersionRef.current) return;
          loadingConversationsRef.current = false;
          setLoadingConversations(false);
        });
    };
    refresh();
    window.addEventListener(conversationsChangedEvent, refresh);
    return () => {
      activeRequest = false;
      conversationRequestVersionRef.current += 1;
      loadingConversationsRef.current = false;
      window.removeEventListener(conversationsChangedEvent, refresh);
    };
  }, [showArchived, t]);

  useEffect(() => {
    const root = recentListRef.current;
    const end = recentListEndRef.current;
    if (!root || !end || !hasMoreConversations || loadingConversations || conversationLoadError || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) loadMoreConversations();
    }, { root, rootMargin: "120px 0px" });
    observer.observe(end);
    return () => observer.disconnect();
  }, [conversationLoadError, hasMoreConversations, loadMoreConversations, loadingConversations]);

  useEffect(() => {
    if (!menuTarget) return;
    const closeOnPointerDown = (event: PointerEvent) => {
      if (event.target instanceof Node && menuRef.current?.contains(event.target)) return;
      if (event.target instanceof Element && event.target.closest(".workspace-sidebar-conversation-more")) return;
      setMenuTarget(null);
      setMenuPosition(null);
    };
    // role="menu" 此前只有 Escape 和首项聚焦，方向键完全没实现 ——
    // 读屏会宣告「菜单」，用户按 ↓ 却毫无反应。
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setMenuTarget(null);
        setMenuPosition(null);
        // 焦点必须回到触发它的 ⋯ 按钮，否则键盘用户被扔回文档开头。
        window.requestAnimationFrame(() => triggerRef.current?.focus());
        return;
      }
      if (event.key !== "ArrowDown" && event.key !== "ArrowUp" && event.key !== "Home" && event.key !== "End") return;
      const items = Array.from(menuRef.current?.querySelectorAll<HTMLButtonElement>("button:not([disabled])") ?? []);
      if (!items.length) return;
      event.preventDefault();
      const current = items.findIndex((item) => item === document.activeElement);
      const next = event.key === "Home" ? 0
        : event.key === "End" ? items.length - 1
        : (current + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
      items[next]?.focus();
    };
    document.addEventListener("pointerdown", closeOnPointerDown);
    document.addEventListener("keydown", onKeyDown);
    window.requestAnimationFrame(() => menuRef.current?.querySelector<HTMLButtonElement>("button")?.focus());
    return () => {
      document.removeEventListener("pointerdown", closeOnPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [menuTarget]);

  /**
   * The action menu used to live inside the scrollable conversation list.
   * Focusing its first item could scroll that list, and overflow clipping made
   * the menu cover or hide the chat pane at narrow widths. Position the menu
   * in a body-level layer instead, while keeping it anchored to its trigger.
   */
  const positionConversationMenu = useCallback(() => {
    if (!menuTarget || !triggerRef.current || !menuRef.current) return;
    const trigger = triggerRef.current.getBoundingClientRect();
    const menu = menuRef.current.getBoundingClientRect();
    const edge = 8;
    const gap = 8;
    const width = menu.width || 172;
    const height = menu.height || 124;
    let left = trigger.right + gap;
    if (left + width > window.innerWidth - edge) left = trigger.left - width - gap;
    left = Math.max(edge, Math.min(left, window.innerWidth - width - edge));
    let top = trigger.top - 5;
    if (top + height > window.innerHeight - edge) top = window.innerHeight - height - edge;
    top = Math.max(edge, top);
    setMenuPosition({ left, top });
  }, [menuTarget]);

  useLayoutEffect(() => {
    if (!menuTarget) return;
    positionConversationMenu();
    const reposition = () => positionConversationMenu();
    window.addEventListener("resize", reposition);
    // Capture scrolling from the list and any page-level scroll container.
    window.addEventListener("scroll", reposition, true);
    return () => {
      window.removeEventListener("resize", reposition);
      window.removeEventListener("scroll", reposition, true);
    };
  }, [menuTarget, positionConversationMenu, recentConversations]);

  const openRename = (conversation: Conversation) => {
    setMenuTarget(null);
    setMenuPosition(null);
    setRenameDraft(conversation.title);
    setRenameTarget(conversation);
  };

  const confirmRename = async () => {
    if (!renameTarget || busyId !== null) return;
    const title = renameDraft.trim();
    if (!title || title === renameTarget.title) { setRenameTarget(null); return; }
    setBusyId(renameTarget.id);
    try {
      const updated = await updateConversation(renameTarget.id, { title });
      setRecentConversations((current) => current.map((conversation) => conversation.id === updated.id ? updated : conversation));
      setRenameTarget(null);
      notifyWorkspaceConversationsChanged({ type: "renamed", conversation: updated });
    } catch (cause) {
      setRenameTarget(null);
      toast.push({ message: errorMessage(cause, t("workspace.actionFailed")), tone: "danger" });
    } finally {
      setBusyId(null);
    }
  };

  /** 归档天生可逆（反着调一次就回来了），所以这里给的是真正的撤销，
   *  不需要像会话删除那样先做软删。 */
  const applyArchived = async (conversation: Conversation, archived: boolean) => {
    const updated = await archiveConversation(conversation.id, archived);
    setMenuTarget(null);
    setMenuPosition(null);
    setRecentConversations((current) => current.filter((item) => item.id !== conversation.id));
    notifyWorkspaceConversationsChanged({ type: "archived", conversation: updated, archived });
    if (currentConversationId() === conversation.id) router.push("/");
    if (!archived) setShowArchived(false);
    return updated;
  };

  const toggleConversationArchived = async (conversation: Conversation) => {
    if (busyId !== null) return;
    const archived = !showArchived;
    setBusyId(conversation.id);
    try {
      const updated = await applyArchived(conversation, archived);
      toast.push({
        message: archived ? t("workspace.toast.archived", { title: updated.title }) : t("workspace.toast.restored", { title: updated.title }),
        tone: "success",
        action: { label: t("toast.undo"), run: () => applyArchived(updated, !archived).then(() => undefined) },
      });
    } catch (cause) {
      setMenuTarget(null);
      toast.push({ message: errorMessage(cause, t("workspace.actionFailed")), tone: "danger" });
    } finally {
      setBusyId(null);
    }
  };

  const confirmDelete = async () => {
    if (!deleteTarget || busyId !== null) return;
    const conversation = deleteTarget;
    setBusyId(conversation.id);
    try {
      await deleteConversation(conversation.id);
      setRecentConversations((current) => current.filter((item) => item.id !== conversation.id));
      setDeleteTarget(null);
      notifyWorkspaceConversationsChanged({ type: "deleted", conversationId: conversation.id });
      if (currentConversationId() === conversation.id) router.push("/");
      // 会话是硬删，给不了撤销 —— 所以确认弹窗留着，这里只报结果。
      toast.push({ message: t("workspace.toast.deleted", { title: conversation.title }), tone: "success" });
    } catch (cause) {
      setDeleteTarget(null);
      toast.push({ message: errorMessage(cause, t("workspace.actionFailed")), tone: "danger" });
    } finally {
      setBusyId(null);
    }
  };

  const beginNewConversation = (event: ReactMouseEvent<HTMLAnchorElement>) => {
    setMenuTarget(null);
    setMenuPosition(null);
    if (!confirmAppNavigation()) {
      event.preventDefault();
      return;
    }
    if (active === "chat" && selectedConversationId === null) {
      event.preventDefault();
      window.dispatchEvent(new Event(newConversationEvent));
      return;
    }
    setSelectedConversationId(null);
    notifyWorkspaceSelectedConversationChanged(null);
  };

  return <>
    <aside className="workspace-sidebar" id="workspace-primary-sidebar">
      <div className="workspace-sidebar-header">
        {sidebarCollapsed
          ? <button className="workspace-sidebar-compact-brand" type="button" aria-label={t("workspace.expandSidebar")} aria-controls="workspace-primary-sidebar" aria-expanded="false" title={t("workspace.expandSidebar")} onClick={() => onSidebarCollapsedChange?.(false)}>
            <MemoryMark />
            <PanelLeftOpen className="workspace-sidebar-compact-expand-icon" size={20} aria-hidden="true" />
          </button>
          : <>
            <MemoryBrand iconOnly />
            <div className="workspace-sidebar-header-actions">
              <SearchTrigger />
              <button className="workspace-sidebar-toggle" type="button" aria-label={t("workspace.collapseSidebar")} aria-controls="workspace-primary-sidebar" aria-expanded="true" title={t("workspace.collapseSidebar")} onClick={() => onSidebarCollapsedChange?.(true)}>
                <PanelLeftClose size={18} />
              </button>
            </div>
          </>}
      </div>
      <div className="workspace-sidebar-primary-actions">
        <Link className="workspace-new-conversation" href="/" aria-label={t("shortcuts.newChat")} title={`${t("shortcuts.newChat")} · ⌘N`} onClick={beginNewConversation}><Plus size={17} aria-hidden="true" /><span>{t("shortcuts.newChat")}</span><kbd>⌘N</kbd></Link>
      </div>
      {sidebarCollapsed && <div className="workspace-sidebar-compact-actions" aria-label={t("workspace.tools")}>
        <SearchTrigger />
        <button className="workspace-sidebar-compact-recent" type="button" aria-label={t("workspace.recent")} title={t("workspace.recent")} onClick={() => { setShowArchived(false); onSidebarCollapsedChange?.(false); }}><MessageCircle size={20} aria-hidden="true" /></button>
      </div>}
      {!sidebarCollapsed && <WorkspaceNav active={active} className="workspace-sidebar-feature-nav" items={sidebarNavigation} />}
      <div className="workspace-sidebar-recent">
        <div className="workspace-sidebar-recent-heading">
          <span>{showArchived ? t("workspace.archived") : t("workspace.recent")}</span>
        </div>
        <div
          className="workspace-sidebar-recent-list"
          ref={recentListRef}
          onScroll={() => {
            const list = recentListRef.current;
            if (list && list.scrollHeight - list.scrollTop - list.clientHeight < 120) loadMoreConversations();
          }}
        >
          {loadingConversations && !recentConversations.length && <small><LoaderCircle size={12} className="spin" />{t("workspace.loadingConversations")}</small>}
          {recentConversations.map((conversation) => <div className={`workspace-sidebar-conversation ${selectedConversationId === conversation.id ? "selected" : ""}`} key={conversation.id}>
            <Link href={`/?conversation=${conversation.id}`} title={conversation.title} aria-current={selectedConversationId === conversation.id ? "page" : undefined} onClick={(event) => { setMenuTarget(null); setMenuPosition(null); if (!confirmAppNavigation()) event.preventDefault(); else setSelectedConversationId(conversation.id); }}><span>{conversation.title}</span></Link>
            <button
              className="workspace-sidebar-conversation-more"
              ref={menuTarget?.id === conversation.id ? triggerRef : undefined}
              type="button"
              aria-label={t("workspace.conversationActions", { title: conversation.title })}
              aria-haspopup="menu"
              aria-expanded={menuTarget?.id === conversation.id}
              onClick={() => {
                if (menuTarget?.id === conversation.id) {
                  setMenuTarget(null);
                  setMenuPosition(null);
                } else {
                  setMenuPosition(null);
                  setMenuTarget(conversation);
                }
              }}
            ><MoreHorizontal size={16} /></button>
          </div>)}
          {loadingConversations && recentConversations.length > 0 && <small><LoaderCircle size={12} className="spin" />{t("workspace.loadingConversations")}</small>}
          {!loadingConversations && !recentConversations.length && !conversationLoadError && <small>{showArchived ? t("workspace.noArchived") : t("workspace.noRecent")}</small>}
          {conversationLoadError && <><small className="workspace-sidebar-recent-error" role="alert">{conversationLoadError}</small><button className="workspace-sidebar-load-more" type="button" onClick={loadMoreConversations}>{t("workspace.retry")}</button></>}
          {!loadingConversations && !conversationLoadError && hasMoreConversations && <button className="workspace-sidebar-load-more" type="button" ref={recentListEndRef} onClick={loadMoreConversations}>{t("workspace.loadMore")}</button>}
        </div>
        <button className="workspace-sidebar-recent-filter" type="button" onClick={() => { setMenuTarget(null); setMenuPosition(null); setShowArchived((value) => !value); }}>
          {showArchived ? <ArrowLeft size={15} /> : <Archive size={15} />}
          <span>{showArchived ? t("workspace.backToRecent") : t("workspace.archived")}</span>
        </button>
      </div>
      <footer className="workspace-sidebar-footer">
        <WorkspaceProfile />
      </footer>
    </aside>
    {menuTarget && typeof document !== "undefined" && createPortal(
      <div
        className="workspace-conversation-menu"
        role="menu"
        aria-label={t("workspace.conversationActions", { title: menuTarget.title })}
        ref={menuRef}
        style={{
          left: menuPosition?.left ?? 0,
          top: menuPosition?.top ?? 0,
          visibility: menuPosition ? "visible" : "hidden",
        }}
      >
        <button type="button" role="menuitem" onClick={() => openRename(menuTarget)} disabled={busyId !== null}><Pencil size={15} /><span>{t("workspace.rename")}</span></button>
        <button type="button" role="menuitem" onClick={() => void toggleConversationArchived(menuTarget)} disabled={busyId !== null}>{showArchived ? <ArchiveRestore size={15} /> : <Archive size={15} />}<span>{showArchived ? t("workspace.restore") : t("workspace.archive")}</span></button>
        <button className="danger" type="button" role="menuitem" onClick={() => { setMenuTarget(null); setMenuPosition(null); setDeleteTarget(menuTarget); }} disabled={busyId !== null}><Trash2 size={15} /><span>{t("workspace.delete")}</span></button>
      </div>,
      document.body,
    )}
    <header className="workspace-mobile-topbar">
      <MemoryBrand />
      <div><Link className="workspace-mobile-new-conversation" href="/" aria-label={t("shortcuts.newChat")} title={t("shortcuts.newChat")} onClick={beginNewConversation}><Plus size={18} /></Link><SearchTrigger /></div>
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
