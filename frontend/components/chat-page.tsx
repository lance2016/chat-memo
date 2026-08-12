"use client";

import { FormEvent, KeyboardEvent, RefObject, useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useRouter, useSearchParams } from "next/navigation";
import { ArrowDown, ArrowRight, BrainCircuit, Check, ChevronDown, Copy, ExternalLink, Gauge, Globe2, ImagePlus, LoaderCircle, Pencil, Plus, RefreshCw, Search, Send, Sparkles, Square, TriangleAlert, Volume2, X } from "lucide-react";
import { apiUrl, attachmentObjectUrl, errorMessage, getContextPreview, getConversationContext, getMemoryStats, getModelCatalog, getNextSpeech, getRuntimeSettings, getTtsStatus, listConversations, listMessages, prepareSpeech, stopSpeech, streamChat, truncateMessages, uploadAttachment } from "@/lib/api";
import { defaultPreferences, preferencesChangeEvent, readPreferences, writePreferences, type UserPreferences } from "@/lib/preferences";
import { toTurns, toolLabel } from "@/lib/turns";
import type { AttachmentMeta, ChatEvent, Conversation, ConversationContext, ModelCatalog, ModelProfileSummary, ToolActivity, Turn, TurnAttachment, TtsStatus } from "@/lib/types";
import { Markdown } from "@/components/markdown";
import { conversationsChangedEvent, MemoryMark, notifyWorkspaceConversationsChanged, notifyWorkspaceSelectedConversationChanged, type WorkspaceConversationChange, WorkspacePageFallback } from "@/components/workspace-topbar";
import { newConversationEvent } from "@/components/keyboard-shortcuts";
import { LatestRequest } from "@/lib/latest-request";
import { resetMediaElement } from "@/lib/media-playback";
import { VoiceInputButton } from "@/components/voice-input-button";
import { useI18n } from "@/components/i18n-provider";
import { usePhoenixUrl } from "@/lib/phoenix";
import { useDismissOnOutside } from "@/lib/use-dismiss-on-outside";

interface LiveTool extends ToolActivity { status: "running" | "done"; }

function displayTool(tool: LiveTool | ToolActivity, t: ReturnType<typeof useI18n>["t"]) {
  const running = "status" in tool && tool.status === "running";
  return (
    <div className={`tool-activity ${running ? "running" : tool.ok ? "" : "failed"}`} key={`${tool.name}-${tool.summary}-${JSON.stringify(tool.input)}`}>
      <span className="tool-activity-icon" aria-hidden="true">{running ? <LoaderCircle size={13} className="spin" /> : tool.ok ? "✓" : "!"}</span>
      <div className="tool-activity-body">
        <div className="tool-activity-head">
          <span className="tool-activity-label">{toolLabel(tool)}</span>
          <span className="tool-activity-state">{running ? t("chat.tool.processing") : tool.ok ? t("chat.tool.done") : t("chat.tool.attention")}</span>
        </div>
        {!running && tool.summary && <p className="tool-summary">{tool.summary}</p>}
      </div>
    </div>
  );
}

function ToolActivityGroup({ tools }: { tools: (LiveTool | ToolActivity)[] }) {
  const { t } = useI18n();
  const runningCount = tools.filter((tool) => "status" in tool && tool.status === "running").length;
  const failedCount = tools.filter((tool) => !("status" in tool && tool.status === "running") && !tool.ok).length;
  const knowledgeBaseCount = tools.filter((tool) => tool.name.startsWith("kb_")).length;
  const timelineCount = tools.filter((tool) => tool.name.startsWith("timeline_")).length;
  const webSearchCount = tools.filter((tool) => tool.name === "web_search").length;
  const groupName = webSearchCount === tools.length ? t("chat.tool.webSearch") : knowledgeBaseCount === tools.length ? t("chat.tool.knowledge") : timelineCount === tools.length ? t("chat.tool.timeline") : knowledgeBaseCount > 0 || timelineCount > 0 ? t("chat.tool.general") : t("chat.tool.memory");
  const label = runningCount ? t("chat.tool.runningLabel", { count: tools.length, group: groupName }) : t("chat.tool.doneLabel", { count: tools.length, group: groupName });
  const state = runningCount ? t("chat.tool.runningState", { count: runningCount }) : failedCount ? t("chat.tool.attentionState", { count: failedCount }) : t("chat.tool.done");

  return <details className={`tool-group ${failedCount ? "has-failure" : ""}`} open={runningCount > 0 || failedCount > 0}>
    <summary><span className="tool-group-icon" aria-hidden="true">{runningCount ? <LoaderCircle size={13} className="spin" /> : failedCount ? <TriangleAlert size={13} /> : <Check size={13} />}</span><span className="tool-group-label">{label}</span>{failedCount > 0 && <span className="tool-group-warning"><TriangleAlert size={12} /><span>{state}</span></span>}{runningCount > 0 && <span className="tool-group-state">{state}</span>}<ChevronDown size={13} className="tool-group-chevron" /></summary>
    <div className="tool-group-list">{tools.map((tool) => displayTool(tool, t))}</div>
  </details>;
}

function TraceControls({ traceId }: { traceId: string }) {
  const { t } = useI18n();
  const [copied, setCopied] = useState(false);
  const phoenixUrl = usePhoenixUrl();
  const shortId = traceId.slice(0, 8);

  const copyTraceId = async () => {
    await navigator.clipboard.writeText(traceId);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  };

  return <div className="trace-controls" title={traceId}>
    <span className="trace-controls-label">{t("chat.trace")}</span>
    <code>{shortId}</code>
    <button type="button" onClick={() => void copyTraceId()} aria-label={t("chat.trace.copy")} title={t("chat.trace.copy")}>
      {copied ? <Check size={12} /> : <Copy size={12} />}
    </button>
    {phoenixUrl && <a href={phoenixUrl} target="_blank" rel="noreferrer" aria-label={t("chat.trace.open")} title={t("chat.trace.open")}>
      <ExternalLink size={12} />
    </a>}
  </div>;
}

function usageLabel(usage: NonNullable<Extract<Turn, { kind: "assistant" }>["usage"]>) {
  const output = usage.completion_tokens ?? usage.output_tokens;
  return typeof output === "number" ? `${output.toLocaleString()} output tokens` : "";
}

function homeDateLabel(locale: string) {
  return new Intl.DateTimeFormat(locale, { weekday: "long", month: "long", day: "numeric" }).format(new Date());
}

function greeting(t: ReturnType<typeof useI18n>["t"]) {
  const hour = new Date().getHours();
  if (hour < 6) return t("chat.greeting.late");
  if (hour < 11) return t("chat.greeting.morning");
  if (hour < 14) return t("chat.greeting.noon");
  if (hour < 18) return t("chat.greeting.afternoon");
  return t("chat.greeting.evening");
}

function ModelPicker({ catalog, value, disabled, onChange }: { catalog: ModelCatalog | null; value: number | null; disabled: boolean; onChange: (value: number | null) => void }) {
  const { t } = useI18n();
  const pickerRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const menuId = useId();
  const menuRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [menuPosition, setMenuPosition] = useState<{ left: number; top: number; width: number; maxHeight: number } | null>(null);
  const closeMenu = useCallback(() => {
    setOpen(false);
    setMenuPosition(null);
    setSearchQuery("");
  }, []);

  useEffect(() => {
    if (!open) return;
    const closeOnPointerDown = (event: PointerEvent) => {
      if (event.target instanceof Node && (pickerRef.current?.contains(event.target) || menuRef.current?.contains(event.target))) return;
      closeMenu();
    };
    const closeOnEscape = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") closeMenu();
    };
    document.addEventListener("pointerdown", closeOnPointerDown);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOnPointerDown);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [closeMenu, open]);

  useEffect(() => {
    if (disabled) closeMenu();
  }, [closeMenu, disabled]);

  const visibleServices = useMemo(() => {
    if (!catalog) return [];
    const query = searchQuery.trim().toLocaleLowerCase();
    return catalog.services.map((service) => ({
      service,
      profiles: catalog.profiles.filter((profile) => profile.service_id === service.id && (!query || [profile.display_name, profile.model_id, profile.service_name].some((value) => value.toLocaleLowerCase().includes(query)))),
    })).filter((group) => group.profiles.length > 0 || !query);
  }, [catalog, searchQuery]);

  // 扁平化的可选项顺序，和下面按服务分组的渲染顺序**必须一致** ——
  // 方向键走的是这个数组，高亮靠 index 对上 DOM id。
  const options = useMemo(() => {
    if (!catalog) return [] as { id: number | null; disabled: boolean }[];
    const flat: { id: number | null; disabled: boolean }[] = [{ id: null, disabled: false }];
    for (const { profiles } of visibleServices) {
      for (const profile of profiles) {
        flat.push({ id: profile.id, disabled: !profile.available && profile.id !== value });
      }
    }
    return flat;
  }, [catalog, value, visibleServices]);

  const optionId = (index: number) => `${menuId}-option-${index}`;

  useEffect(() => {
    if (!open) return;
    const menu = menuRef.current;
    const option = document.getElementById(optionId(activeIndex));
    if (!menu || !option || !menuPosition) return;
    // Do not use scrollIntoView here: it may scroll the page-level composer
    // container along with the menu and makes the whole UI jump.
    const menuRect = menu.getBoundingClientRect();
    const optionRect = option.getBoundingClientRect();
    if (optionRect.top < menuRect.top) menu.scrollTop -= menuRect.top - optionRect.top + 8;
    else if (optionRect.bottom > menuRect.bottom) menu.scrollTop += optionRect.bottom - menuRect.bottom + 8;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeIndex, open, menuId, menuPosition]);

  useEffect(() => {
    if (open && searchQuery) setActiveIndex(0);
  }, [open, searchQuery]);

  useLayoutEffect(() => {
    if (!open) return;
    const positionMenu = () => {
      const trigger = pickerRef.current?.querySelector<HTMLButtonElement>(".chat-model-trigger");
      const menu = menuRef.current;
      if (!trigger || !menu) return;
      const triggerRect = trigger.getBoundingClientRect();
      const maxHeight = Math.min(420, Math.max(180, window.innerHeight - 32));
      const menuHeight = Math.min(menu.scrollHeight, maxHeight);
      const width = Math.min(Math.max(triggerRect.width, 250), Math.min(300, window.innerWidth - 32));
      const spaceBelow = window.innerHeight - triggerRect.bottom - 16;
      const spaceAbove = triggerRect.top - 16;
      const openUp = spaceBelow < Math.min(menuHeight, 220) && spaceAbove > spaceBelow;
      const top = openUp ? triggerRect.top - menuHeight - 8 : triggerRect.bottom + 8;
      let left = triggerRect.left;
      if (left + width > window.innerWidth - 16) left = triggerRect.right - width;
      setMenuPosition({
        left: Math.max(16, Math.min(left, window.innerWidth - width - 16)),
        top: Math.max(16, Math.min(top, window.innerHeight - menuHeight - 16)),
        width,
        maxHeight,
      });
    };
    positionMenu();
    let frame = 0;
    const reposition = () => {
      if (frame) return;
      frame = window.requestAnimationFrame(() => {
        frame = 0;
        positionMenu();
      });
    };
    window.addEventListener("resize", reposition);
    window.addEventListener("scroll", reposition, true);
    return () => {
      if (frame) window.cancelAnimationFrame(frame);
      window.removeEventListener("resize", reposition);
      window.removeEventListener("scroll", reposition, true);
    };
  }, [open, options.length]);

  if (!catalog) return null;
  const defaultProfile = catalog.profiles.find((profile) => profile.id === catalog.default_profile_id || profile.is_default);
  const selectedProfile = catalog.profiles.find((profile) => profile.id === value);
  const selectedLabel = selectedProfile?.model_id ?? defaultProfile?.model_id ?? t("chat.model.followDefault");

  const choose = (next: number | null) => {
    onChange(next);
    closeMenu();
  };

  const openMenu = () => {
    setSearchQuery("");
    const selectedIndex = options.findIndex((option) => option.id === value);
    setActiveIndex(selectedIndex >= 0 ? selectedIndex : 0);
    setOpen(true);
  };

  // 跳过不可用项，绕一圈都没有可用的就原地不动。
  const step = (from: number, delta: number) => {
    let next = from;
    for (let moved = 0; moved < options.length; moved += 1) {
      next = (next + delta + options.length) % options.length;
      if (!options[next].disabled) return next;
    }
    return from;
  };

  const onTriggerKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (disabled) return;
    if (!open) {
      if (event.key === "ArrowDown" || event.key === "ArrowUp" || event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        openMenu();
      }
      return;
    }
    switch (event.key) {
      case "ArrowDown": event.preventDefault(); setActiveIndex((current) => step(current, 1)); break;
      case "ArrowUp": event.preventDefault(); setActiveIndex((current) => step(current, -1)); break;
      case "Home": event.preventDefault(); setActiveIndex(step(options.length - 1, 1)); break;
      case "End": event.preventDefault(); setActiveIndex(step(0, -1)); break;
      case "Enter":
      case " ": {
        event.preventDefault();
        const option = options[activeIndex];
        if (option && !option.disabled) choose(option.id);
        break;
      }
      case "Escape": event.preventDefault(); closeMenu(); break;
      case "Tab": closeMenu(); break;
      default: break;
    }
  };

  // 渲染时和 options 同步推进的游标，保证 DOM id 和方向键索引一一对应。
  let cursor = 0;

  return <div className={`chat-model-picker ${open ? "is-open" : ""}`} ref={pickerRef}>
    <span>{t("chat.model.label")}</span>
    <button className="chat-model-trigger" type="button" role="combobox" aria-label={t("chat.model.select")} aria-haspopup="listbox" aria-controls={menuId} aria-expanded={open} aria-activedescendant={open ? optionId(activeIndex) : undefined} disabled={disabled} onClick={() => open ? closeMenu() : openMenu()} onKeyDown={onTriggerKeyDown}>
      <span>{selectedLabel}</span><ChevronDown size={14} aria-hidden="true" />
    </button>
    {open && typeof document !== "undefined" && createPortal(<div className="chat-model-menu" id={menuId} ref={menuRef} role="listbox" aria-label={t("chat.model.list")} style={{ left: menuPosition?.left ?? 0, top: menuPosition?.top ?? 0, width: menuPosition?.width ?? 250, maxHeight: menuPosition?.maxHeight ?? 420, visibility: menuPosition ? "visible" : "hidden" }}>
      <div className="chat-model-search-wrap"><Search size={14} aria-hidden="true" /><input ref={searchRef} className="chat-model-search" type="search" value={searchQuery} placeholder={t("chat.model.search")} aria-label={t("chat.model.search")} onChange={(event) => setSearchQuery(event.target.value)} onKeyDown={(event) => { if (event.key === "Escape") { event.preventDefault(); closeMenu(); } }} /></div>
      {(() => { const index = cursor++; return <button className={`chat-model-option ${value === null ? "is-selected" : ""} ${activeIndex === index ? "is-active" : ""}`} id={optionId(index)} type="button" role="option" aria-selected={value === null} onMouseEnter={() => setActiveIndex(index)} onClick={() => choose(null)}>
        <span className="chat-model-option-copy"><strong>{t("chat.model.default")}</strong><small>{defaultProfile?.model_id ?? t("chat.model.followDefault")}</small></span>
        {value === null && <Check size={14} aria-hidden="true" />}
      </button>; })()}
      {visibleServices.map(({ service, profiles }) => <div className="chat-model-group" key={service.id}>
        <div className="chat-model-group-label">{service.name}<span>{profiles.length}</span></div>
        {profiles.map((profile) => {
          const unavailable = !profile.available && profile.id !== value;
          const index = cursor++;
          return <button className={`chat-model-option ${profile.id === value ? "is-selected" : ""} ${activeIndex === index ? "is-active" : ""}`} id={optionId(index)} type="button" role="option" aria-selected={profile.id === value} disabled={unavailable} key={profile.id} onMouseEnter={() => setActiveIndex(index)} onClick={() => choose(profile.id)}>
            <span className="chat-model-option-copy"><strong>{profile.model_id}</strong>{profile.is_default && <small>{t("chat.model.default")}</small>}{unavailable && <small>{t("chat.model.unavailable")}</small>}</span>
            {profile.id === value && <Check size={14} aria-hidden="true" />}
          </button>;
        })}
      </div>)}
      {searchQuery.trim() && visibleServices.length === 0 && <div className="chat-model-empty">{t("chat.model.noResults")}</div>}
    </div>, document.body)}
  </div>;
}

type DisplayAttachment = TurnAttachment & { previewUrl?: string };
type ComposerAttachment = AttachmentMeta & { previewUrl?: string };
type DropTargetProps = {
  onDragEnter: (event: React.DragEvent) => void;
  onDragOver: (event: React.DragEvent) => void;
  onDragLeave: (event: React.DragEvent) => void;
  onDrop: (event: React.DragEvent) => void;
};

/** 拖拽悬停时盖在输入框上的提示。没有它用户不知道能不能松手。 */
function DropHint({ available }: { available: boolean }) {
  const { t } = useI18n();
  return <div className={`composer-drop-hint ${available ? "" : "is-unavailable"}`} aria-hidden="true">
    <ImagePlus size={18} />
    <span>{available ? t("chat.attachment.dropHere") : t("chat.attachment.unavailable")}</span>
  </div>;
}

function AttachmentThumb({ id, filename, previewUrl, onRemove }: { id: number; filename: string; previewUrl?: string; onRemove?: () => void }) {
  const { t } = useI18n();
  const [url, setUrl] = useState(previewUrl ?? "");
  const [usingLocalPreview, setUsingLocalPreview] = useState(Boolean(previewUrl));
  const [failed, setFailed] = useState(false);
  // Keep the object URL that was already visible while history is being
  // reloaded. A transient failure of the authenticated download must not
  // turn a working image into a warning icon.
  const lastLocalPreviewRef = useRef(previewUrl ?? "");

  useEffect(() => {
    let active = true;
    if (previewUrl) lastLocalPreviewRef.current = previewUrl;
    const fallbackUrl = previewUrl || lastLocalPreviewRef.current;
    setUrl(fallbackUrl);
    setUsingLocalPreview(Boolean(fallbackUrl));
    setFailed(false);
    if (previewUrl) return () => { active = false; };
    // 走 authed fetch 换 blob URL —— <img src> 带不了 X-API-Key。
    // URL 由 api.ts 按 id 缓存复用，所以这里**不能** revokeObjectURL：
    // 撤销之后同一张图在别处（或重新挂载时）就再也显示不出来了。
    attachmentObjectUrl(id).then(
      (value) => {
        if (!active) return;
        setUrl(value);
        setUsingLocalPreview(false);
      },
      () => {
        // The local object URL is still a valid preview in this state. Keep
        // it instead of showing a false negative while the API recovers.
        if (active && !fallbackUrl) setFailed(true);
      },
    );
    return () => { active = false; };
  }, [id, previewUrl]);

  const handleImageError = () => {
    if (usingLocalPreview) {
      // The local URL itself failed, so don't try it again if the backend
      // fallback also fails.
      lastLocalPreviewRef.current = "";
      setUsingLocalPreview(false);
      setUrl("");
      attachmentObjectUrl(id).then(
        (value) => { setUrl(value); setFailed(false); },
        () => setFailed(true),
      );
      return;
    }
    // A canonical download can finish after the stream and still fail to
    // decode (or be briefly unavailable). If the user already has a good
    // local preview, prefer it to a warning placeholder.
    if (lastLocalPreviewRef.current) {
      setUrl(lastLocalPreviewRef.current);
      setUsingLocalPreview(true);
      setFailed(false);
      return;
    }
    setFailed(true);
  };

  return <span className={`attachment-thumb ${failed ? "is-failed" : ""}`} title={filename}>
    {/* next/image 在这里帮不上忙：它的优化管线要一个能被服务端取到的 URL，
        而这是个 authed fetch 出来的 blob:，只存在于本页面的内存里。 */}
    {/* eslint-disable-next-line @next/next/no-img-element */}
    {url && !failed ? <img src={url} alt={filename || t("chat.attachment.image")} onError={handleImageError} /> : <span className="attachment-thumb-placeholder">{failed ? <TriangleAlert size={14} /> : <LoaderCircle size={14} className="spin" />}</span>}
    {onRemove && <button type="button" className="attachment-thumb-remove" onClick={onRemove} aria-label={t("chat.attachment.remove")}><X size={11} /></button>}
  </span>;
}

function AttachmentStrip({ items, onRemove, showNames = false }: { items: DisplayAttachment[]; onRemove?: (id: number) => void; showNames?: boolean }) {
  if (items.length === 0) return null;
  return <div className={`attachment-strip ${showNames ? "with-names" : ""}`}>
    {items.map((item) => <div className="attachment-item" key={item.id}><AttachmentThumb id={item.id} filename={item.filename} previewUrl={item.previewUrl} onRemove={onRemove ? () => onRemove(item.id) : undefined} />{showNames && <span className="attachment-name" title={item.filename}>{item.filename}</span>}</div>)}
  </div>;
}

function ComposerAttachments({ items, uploading, onRemove }: { items: ComposerAttachment[]; uploading: number; onRemove: (id: number) => void }) {
  const { t } = useI18n();
  if (items.length === 0 && uploading === 0) return null;
  return <div className="composer-attachments">
    <div className="composer-attachments-head"><span><ImagePlus size={13} />{items.length ? t("chat.attachment.count", { count: items.length }) : t("chat.attachment.uploading")}</span>{uploading > 0 && <small>{t("chat.attachment.uploadingCount", { count: uploading })}</small>}</div>
    <div className="composer-attachments-body"><AttachmentStrip items={items.map((item) => ({ id: item.id, filename: item.filename, previewUrl: item.previewUrl }))} onRemove={onRemove} showNames />{uploading > 0 && <span className="attachment-thumb is-uploading"><span className="attachment-thumb-placeholder"><LoaderCircle size={14} className="spin" /></span></span>}</div>
  </div>;
}

function ComposerToolMenu({ enabled, available, disabled, onChange, onPickImage, imageAvailable }: { enabled: boolean; available: boolean; disabled: boolean; onChange: (value: boolean) => void; onPickImage?: () => void; imageAvailable?: boolean }) {
  const { t } = useI18n();
  const menuRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);

  useDismissOnOutside(menuRef, open, () => setOpen(false));

  useEffect(() => {
    if (disabled) setOpen(false);
  }, [disabled]);

  const toggleWebSearch = () => {
    if (!available || disabled) return;
    onChange(!enabled);
    setOpen(false);
  };

  return <div className={`composer-tools ${enabled ? "is-enabled" : ""}`} ref={menuRef}>
    <button type="button" className="composer-tool-trigger" aria-label={t("chat.webSearch.menu")} title={t("chat.webSearch.menu")} aria-haspopup="menu" aria-expanded={open} disabled={disabled} onClick={() => setOpen((current) => !current)}><Plus size={18} /></button>
    {enabled && <button type="button" className="composer-tool-active" onClick={() => onChange(false)} aria-label={t("chat.webSearch.disable")} title={t("chat.webSearch.disable")}><Globe2 size={12} /><span>{t("chat.webSearch.short")}</span><X size={12} /></button>}
    {open && <div className="composer-tool-menu" role="menu">
      <div className="composer-tool-menu-heading">{t("chat.webSearch.menu")}</div>
      {onPickImage && <button type="button" className="composer-tool-option" role="menuitem" disabled={!imageAvailable || disabled} onClick={() => { onPickImage(); setOpen(false); }}>
        <span className="composer-tool-option-icon"><ImagePlus size={16} /></span>
        <span className="composer-tool-option-copy"><strong>{t("chat.attachment.label")}</strong><small>{imageAvailable ? t("chat.attachment.description") : t("chat.attachment.unavailable")}</small></span>
      </button>}
      <button type="button" className={`composer-tool-option ${enabled ? "is-selected" : ""}`} role="menuitemcheckbox" aria-checked={enabled} disabled={!available || disabled} onClick={toggleWebSearch}>
        <span className="composer-tool-option-icon"><Globe2 size={16} /></span>
        <span className="composer-tool-option-copy"><strong>{t("chat.webSearch.label")}</strong><small>{available ? t("chat.webSearch.description") : t("chat.webSearch.unavailable")}</small></span>
        <span className={`composer-tool-switch ${enabled ? "is-on" : ""}`} aria-hidden="true"><i /></span>
      </button>
    </div>}
  </div>;
}

function ThinkingControl({ profile, enabled, effort, disabled, onEnabledChange, onEffortChange }: {
  profile: ModelProfileSummary | null;
  enabled: boolean;
  effort: string | null;
  disabled: boolean;
  onEnabledChange: (value: boolean) => void;
  onEffortChange: (value: string) => void;
}) {
  const { t } = useI18n();
  const controlRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const efforts = profile?.thinking_efforts ?? [];

  useDismissOnOutside(controlRef, open, () => setOpen(false));
  useEffect(() => {
    if (disabled) setOpen(false);
  }, [disabled]);

  if (!profile?.capabilities?.thinking) return null;

  const effortLabel = (value: string | null) => {
    if (value === "low") return t("chat.thinking.effort.low");
    if (value === "medium") return t("chat.thinking.effort.medium");
    if (value === "high") return t("chat.thinking.effort.high");
    if (value === "xhigh") return t("chat.thinking.effort.xhigh");
    if (value === "max") return t("chat.thinking.effort.max");
    return value || t("chat.thinking.effort.auto");
  };
  return <div className={`thinking-control-popover ${enabled ? "is-enabled" : ""}`} ref={controlRef}>
    <button type="button" className={`thinking-trigger ${enabled ? "is-enabled" : ""}`} aria-label={t("chat.thinking.settings")} aria-haspopup="menu" aria-expanded={open} disabled={disabled} onClick={() => setOpen((current) => !current)}><BrainCircuit size={14} /><span>{t("chat.thinking.control")}</span><ChevronDown size={12} className="thinking-trigger-chevron" /></button>
    {open && <div className="thinking-menu" role="menu" aria-label={t("chat.thinking.settings")}>
      <button type="button" className="thinking-menu-switch" role="menuitemcheckbox" aria-checked={enabled} onClick={() => onEnabledChange(!enabled)}>
        <span><strong>{t("chat.thinking.control")}</strong><small>{t("chat.thinking.description")}</small></span>
        <span className={`thinking-switch ${enabled ? "is-on" : ""}`} aria-hidden="true"><i /></span>
      </button>
      {enabled && <div className="thinking-effort-group" role="group" aria-label={t("chat.thinking.depth")}>
        <div className="thinking-menu-heading"><span>{t("chat.thinking.depth")}</span><small>{t("chat.thinking.depthHint")}</small></div>
        {efforts.length > 0 ? <div className="thinking-effort-options">
          {efforts.map((value) => <button type="button" role="menuitemradio" aria-checked={effort === value} className={effort === value ? "is-selected" : ""} key={value} onClick={() => { onEffortChange(value); setOpen(false); }}><span>{effortLabel(value)}</span>{effort === value && <Check size={13} />}</button>)}
        </div> : <div className="thinking-effort-automatic"><span><strong>{t("chat.thinking.effort.auto")}</strong><small>{t("chat.thinking.depthAutomatic")}</small></span><Check size={13} /></div>}
      </div>}
    </div>}
  </div>;
}

function HomeDashboard({ input, memoryCount, sending, backgroundResponseTitle, composerRef, onInput, onTranscription, onKeyDown, onSubmit, onReturnToResponse, modelCatalog, modelProfileId, onModelChange, thinkingProfile, thinkingEnabled, thinkingEffort, onThinkingChange, onThinkingEffortChange, webSearchEnabled, webSearchAvailable, onWebSearchChange, attachments, uploading, visionAvailable, onPickImage, onRemoveAttachment, onPasteFiles, dropTargetProps, dragActive, context, profileName }: {
  input: string;
  memoryCount: number | null;
  sending: boolean;
  backgroundResponseTitle?: string;
  composerRef: RefObject<HTMLTextAreaElement | null>;
  onInput: (value: string) => void;
  onTranscription: (value: string) => void;
  onKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => void;
  onSubmit: (event: FormEvent) => void;
  onReturnToResponse: () => void;
  modelCatalog: ModelCatalog | null;
  modelProfileId: number | null;
  onModelChange: (value: number | null) => void;
  thinkingProfile: ModelProfileSummary | null;
  thinkingEnabled: boolean;
  thinkingEffort: string | null;
  onThinkingChange: (value: boolean) => void;
  onThinkingEffortChange: (value: string) => void;
  webSearchEnabled: boolean;
  webSearchAvailable: boolean;
  onWebSearchChange: (value: boolean) => void;
  attachments: AttachmentMeta[];
  uploading: number;
  visionAvailable: boolean;
  onPickImage: () => void;
  onRemoveAttachment: (id: number) => void;
  onPasteFiles: (event: React.ClipboardEvent<HTMLTextAreaElement>) => void;
  dropTargetProps: DropTargetProps;
  dragActive: boolean;
  context: ConversationContext | null;
  profileName: string;
}) {
  const { locale, t } = useI18n();
  useEffect(() => {
    const focusComposer = () => window.requestAnimationFrame(() => composerRef.current?.focus());
    window.addEventListener(newConversationEvent, focusComposer);
    return () => window.removeEventListener(newConversationEvent, focusComposer);
  }, [composerRef]);
  return <div className="memory-home-scroll">
    <div className="memory-home">
      <section className="memory-home-hero">
        <div className="home-app-icon"><MemoryMark compact /></div>
        <div className="memory-home-copy">
          <div className="home-hero-meta"><p className="memory-date"><i /><i /><i />{homeDateLabel(locale)}</p>{memoryCount !== null && <span><Sparkles size={11} />{memoryCount} {t("chat.home.memories")}</span>}</div>
          <h1><span>{greeting(t)}, {profileName}</span><em>{t("chat.home.question")}</em></h1>
          <p>{t("chat.home.description")}</p>
        </div>
      </section>

      {backgroundResponseTitle && <button type="button" className="home-background-stream" onClick={onReturnToResponse}><LoaderCircle size={13} className="spin" /><span>{t("chat.backgroundResponse", { title: backgroundResponseTitle })}</span><ArrowRight size={13} /></button>}
      <form className={`home-capture ${dragActive ? "is-drop-target" : ""}`} onSubmit={onSubmit} {...dropTargetProps}>
        {dragActive && <DropHint available={visionAvailable} />}
        <div className="home-capture-main"><span><Sparkles size={17} /></span><textarea ref={composerRef} rows={2} value={input} onChange={(event) => onInput(event.target.value)} onKeyDown={onKeyDown} onPaste={onPasteFiles} placeholder={backgroundResponseTitle ? t("chat.composer.backgroundPlaceholder") : t("chat.home.placeholder")} aria-label={backgroundResponseTitle ? t("chat.composer.backgroundPlaceholder") : t("chat.home.placeholder")} disabled={sending} /></div>
        <ComposerAttachments items={attachments} uploading={uploading} onRemove={onRemoveAttachment} />
        <div className="home-capture-foot"><div className="home-capture-tools"><ComposerToolMenu enabled={webSearchEnabled} available={webSearchAvailable} disabled={sending} onChange={onWebSearchChange} onPickImage={onPickImage} imageAvailable={visionAvailable} /><ThinkingControl profile={thinkingProfile} enabled={thinkingEnabled} effort={thinkingEffort} disabled={sending} onEnabledChange={onThinkingChange} onEffortChange={onThinkingEffortChange} /></div><div className="home-capture-actions"><ContextIndicator context={context} /><ModelPicker catalog={modelCatalog} value={modelProfileId} disabled={sending} onChange={onModelChange} /><VoiceInputButton disabled={sending} onTranscript={onTranscription} /><button className="home-send" type="submit" disabled={(!input.trim() && attachments.length === 0) || sending || uploading > 0} aria-label={t("chat.send")}><Send size={16} /></button></div></div>
      </form>
      <div className="home-pills" aria-label={t("chat.home.question")}><button type="button" disabled={sending} onClick={() => onInput(t("chat.home.prompt.organize"))}>{t("chat.home.prompt.organize")}</button><button type="button" disabled={sending} onClick={() => onInput(t("chat.home.prompt.review"))}>{t("chat.home.prompt.review")}</button></div>

    </div>
  </div>;
}

function InlineMessageEditor({ value, enterToSend, onChange, onSubmit, onCancel }: { value: string; enterToSend: boolean; onChange: (value: string) => void; onSubmit: () => void; onCancel: () => void }) {
  const { t } = useI18n();
  const editorRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const editor = editorRef.current;
    if (!editor) return;
    editor.focus();
    editor.setSelectionRange(editor.value.length, editor.value.length);
  }, []);

  useLayoutEffect(() => {
    const editor = editorRef.current;
    if (!editor) return;
    editor.style.height = "auto";
    editor.style.height = `${editor.scrollHeight}px`;
  }, [value]);

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.nativeEvent.isComposing || event.keyCode === 229) return;
    if (event.key === "Escape") {
      event.preventDefault();
      onCancel();
      return;
    }
    if (event.key === "Enter" && !event.shiftKey && enterToSend) {
      event.preventDefault();
      if (value.trim()) onSubmit();
    }
  };

  return <div className="user-message-editor">
    <textarea ref={editorRef} value={value} onChange={(event) => onChange(event.target.value)} onKeyDown={handleKeyDown} aria-label={t("chat.composer.editPlaceholder")} />
    <div className="user-message-editor-footer">
      <span>{t("chat.editHint")}</span>
      <div>
        <button type="button" className="user-message-editor-cancel" onClick={onCancel}>{t("chat.cancelEdit")}</button>
        <button type="button" className="user-message-editor-submit" disabled={!value.trim()} onClick={onSubmit}><Send size={13} />{t("chat.resend")}</button>
      </div>
    </div>
  </div>;
}

function TurnView({ turn, traceId, editing = false, editText = "", enterToSend = true, onEditChange, onEditSubmit, onEditCancel, streaming = false, highlighted = false, showToolActivity = true, showUsage = true, ttsLoading = false, ttsPlaying = false, ttsAvailable = false, ttsDisabledReason, turnRef, onEdit, onRegenerate, onSpeak }: { turn: Turn; traceId?: string; editing?: boolean; editText?: string; enterToSend?: boolean; onEditChange?: (value: string) => void; onEditSubmit?: () => void; onEditCancel?: () => void; streaming?: boolean; highlighted?: boolean; showToolActivity?: boolean; showUsage?: boolean; ttsLoading?: boolean; ttsPlaying?: boolean; ttsAvailable?: boolean; ttsDisabledReason?: string; turnRef?: (node: HTMLDivElement | null) => void; onEdit?: () => void; onRegenerate?: () => void; onSpeak?: () => void }) {
  const { t } = useI18n();
  const [copied, setCopied] = useState(false);
  const unavailableReason = ttsDisabledReason ?? t("chat.tts.unknown");
  const copyTurn = async () => {
    await navigator.clipboard.writeText(turn.text);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  };
  if (turn.kind === "user") {
    return <div className={`turn user-turn ${editing ? "is-editing" : ""} ${highlighted ? "message-highlight" : ""}`} ref={turnRef} data-message-id={turn.messageId}><div className="user-message-group">{editing && onEditChange && onEditSubmit && onEditCancel ? <InlineMessageEditor value={editText} enterToSend={enterToSend} onChange={onEditChange} onSubmit={onEditSubmit} onCancel={onEditCancel} /> : <><AttachmentStrip items={turn.attachments ?? []} />{turn.text && <div className="user-bubble">{turn.text}</div>}<div className="turn-actions"><button onClick={() => void copyTurn()}>{copied ? <Check size={12} /> : <Copy size={12} />}{copied ? t("chat.copied") : t("chat.copy")}</button>{onEdit && <button onClick={onEdit}><Pencil size={12} />{t("chat.editResend")}</button>}</div></>}</div></div>;
  }
  return (
    <div className={`turn assistant-turn ${streaming ? "is-streaming" : ""} ${highlighted ? "message-highlight" : ""}`} ref={turnRef} data-message-id={turn.messageId}>
      {turn.thinking && (
        <details className="thinking">
          <summary>{streaming ? t("chat.thinkingActive") : t("chat.thinking")}</summary>
          <div className="thinking-body">{turn.thinking}</div>
        </details>
      )}
      {showToolActivity && turn.tools.length > 0 && <ToolActivityGroup tools={turn.tools} />}
      {streaming && !turn.text && !turn.thinking && turn.tools.length === 0 && <div className="chat-response-phase"><span /><span>{t("chat.phase.connecting")}</span></div>}
      {(turn.text || streaming) && <div className="assistant-content"><Markdown highlightCode={!streaming}>{turn.text}</Markdown>{streaming && <span className="streaming-cursor" />}</div>}
      {turn.usage?.interrupted && <div className="interrupted-answer"><TriangleAlert size={13} /><span>{t("chat.interrupted")}</span></div>}
      {(traceId || (showUsage && !turn.usage?.interrupted && turn.usage && usageLabel(turn.usage))) && <div className="assistant-response-meta">
        {traceId && <TraceControls traceId={traceId} />}
        {showUsage && !turn.usage?.interrupted && turn.usage && usageLabel(turn.usage) && <div className="message-usage">{usageLabel(turn.usage)}</div>}
      </div>}
      {(turn.text || onSpeak || onRegenerate) && <div className="turn-actions assistant-actions">
        {turn.text && <button onClick={() => void copyTurn()} title={t("chat.copyAnswer")}><span>{copied ? <Check size={12} /> : <Copy size={12} />}</span>{copied ? t("chat.copied") : t("chat.copy")}</button>}
        {onSpeak && <button className={`tts-button ${ttsPlaying ? "playing" : ""} ${ttsLoading ? "loading" : ""}`} onClick={onSpeak} disabled={ttsLoading || (!ttsAvailable && !ttsPlaying)} title={ttsAvailable || ttsPlaying ? (ttsPlaying ? t("chat.tts.stop") : t("chat.tts.playAnswer")) : unavailableReason} aria-label={ttsAvailable || ttsPlaying ? (ttsPlaying ? t("chat.tts.stop") : t("chat.tts.playAnswer")) : unavailableReason}>
          {ttsLoading ? <LoaderCircle size={12} className="spin" /> : <Volume2 size={12} />}{ttsLoading ? t("chat.tts.synthesizing") : ttsPlaying ? t("chat.tts.stop") : t("chat.tts.play")}
        </button>}
        {onRegenerate && <button onClick={onRegenerate}><RefreshCw size={12} />{t("chat.regenerate")}</button>}
      </div>}
    </div>
  );
}

function ContextIndicator({ context }: { context: ConversationContext | null }) {
  const { locale, t } = useI18n();
  const contextRef = useRef<HTMLDetailsElement>(null);
  const [open, setOpen] = useState(false);
  useDismissOnOutside(contextRef, open, () => setOpen(false));
  if (!context) return null;
  const number = (value: number) => value.toLocaleString(locale);
  const compact = (value: number) => {
    if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
    if (value >= 1_000) return `${(value / 1_000).toFixed(value >= 10_000 ? 0 : 1)}K`;
    return number(value);
  };
  const breakdown = context.breakdown ?? [];
  const total = context.prompt_tokens || context.estimated_prompt_tokens || 0;
  const window = context.context_window_tokens ?? null;
  const exact = context.exact_prompt_tokens ?? 0;
  const cacheRate = total > 0 ? Math.round(context.cached_tokens / total * 100) : 0;
  const colors = ["mint", "amber", "violet", "cyan", "blue"];
  const usedPercent = window ? Math.min(100, total / window * 100) : 0;
  return <details className="chat-context-indicator" ref={contextRef} open={open}>
    <summary aria-label={t("chat.context.label")} aria-expanded={open} onClick={(event) => { event.preventDefault(); setOpen((current) => !current); }}><Gauge size={13} /><span>{t("chat.context.label")}</span><b>{window ? `${compact(total)} / ${compact(window)}` : t("chat.context.approx", { value: compact(total) })}</b></summary>
    <div className="chat-context-popover">
      <div className="chat-context-heading"><div><strong>{t("chat.context.title")}</strong><span>{context.model_name || context.model_id || "—"}</span></div><span className="chat-context-estimate">{context.estimated ? t("chat.context.estimated") : t("chat.context.measured")}</span></div>
      <div className="chat-context-total"><strong>{compact(total)}</strong><span>{window ? `/ ${compact(window)} ${t("chat.context.tokensUnit")}` : t("chat.context.unknownWindow")}</span></div>
      <div className="chat-context-meter" aria-label={t("chat.context.title")}>
        {breakdown.map((item, index) => <span key={item.key} className={`chat-context-meter-segment ${colors[index % colors.length]}`} style={{ width: `${total > 0 ? Math.max(item.tokens / total * 100, item.tokens > 0 ? 1.5 : 0) : 0}%` }} />)}
      </div>
      <div className="chat-context-breakdown">
        {breakdown.map((item, index) => <div className="chat-context-breakdown-row" key={item.key}><span><i className={`chat-context-dot ${colors[index % colors.length]}`} />{item.label}{item.items ? ` · ${item.items}` : ""}</span><strong>{compact(item.tokens)}</strong></div>)}
      </div>
      <div className="chat-context-summary"><span>{t("chat.context.retained")}</span><strong>{t("chat.context.turns", { count: context.retained_turns })}</strong><span>{t("chat.context.trimmed")}</span><strong>{number(context.trimmed_messages)}</strong><span>{t("chat.context.cache")}</span><strong>{cacheRate}%</strong></div>
      <div className="chat-context-footnote"><span>{exact ? `${number(exact)} ${t("chat.context.tokensUnit")} · ${t("chat.context.measured")}` : t("chat.context.estimateNote")}</span><span>{window ? `${usedPercent.toFixed(1)}% · ${compact(Math.max(window - total, 0))} ${t("chat.context.remaining")}` : `${t("chat.context.outputLimit")} ${compact(context.max_output_tokens ?? 0)}`}</span></div>
    </div>
  </details>;
}

function turnKey(turn: Turn, index: number) {
  // The optimistic user turn has no message id yet, while the same turn from
  // history does. Keep image turns mounted across that handoff so their local
  // object preview can bridge the authenticated history fetch.
  if (turn.kind === "user" && turn.attachments?.length) {
    return `user-attachments-${turn.attachments.map((attachment) => attachment.id).join(",")}-${turn.text}`;
  }
  return `${turn.kind}-${turn.messageId ?? index}`;
}

export function ChatPage() {
  const { t } = useI18n();
  const router = useRouter();
  const searchParams = useSearchParams();
  const selectedFromUrl = Number(searchParams.get("conversation"));
  const messageFromUrl = Number(searchParams.get("message"));
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [apiMessages, setApiMessages] = useState<import("@/lib/types").ApiMessage[]>([]);
  const [, setArchivedConversations] = useState<Conversation[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(Number.isFinite(selectedFromUrl) && selectedFromUrl > 0 ? selectedFromUrl : null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [pendingUser, setPendingUser] = useState("");
  // 已经上传、还没随消息发出去的附件
  const [attachments, setAttachments] = useState<ComposerAttachment[]>([]);
  // 发送瞬间冻结的那一份，给乐观渲染用（attachments 已经被清空了）
  const [pendingAttachments, setPendingAttachments] = useState<TurnAttachment[]>([]);
  const [uploading, setUploading] = useState(0);
  const [draft, setDraft] = useState({ text: "", thinking: "", tools: [] as LiveTool[] });
  const [input, setInput] = useState("");
  const [loadingConversations, setLoadingConversations] = useState(true);
  const [loadingMessages, setLoadingMessages] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [, setShowArchived] = useState(false);
  const [editingTarget, setEditingTarget] = useState<number | null>(null);
  const [editDraft, setEditDraft] = useState("");
  const [editAttachments, setEditAttachments] = useState<TurnAttachment[]>([]);
  const [preferences, setPreferences] = useState<UserPreferences>(defaultPreferences);
  const [ttsStatus, setTtsStatus] = useState<TtsStatus | null>(null);
  const [ttsLoadingId, setTtsLoadingId] = useState<number | null>(null);
  const [ttsPlayingId, setTtsPlayingId] = useState<number | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const mountedRef = useRef(true);
  const scrollRef = useRef<HTMLDivElement>(null);
  const mainPanelRef = useRef<HTMLElement>(null);
  const composerWrapRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const audioRef = useRef<HTMLAudioElement>(null);
  const speechBusyGenerationRef = useRef<number | null>(null);
  const speechQueueRef = useRef<string[]>([]);
  const speechCursorRef = useRef(0);
  const speechPumpRef = useRef<Promise<void>>(Promise.resolve());
  const speechPumpTimerRef = useRef<number | null>(null);
  const speechGenerationRef = useRef(0);
  const speechAutoActiveRef = useRef(false);
  const speechFlushCompleteRef = useRef(false);
  const speechAudioPlayingRef = useRef(false);
  const speechAutoMessageIdRef = useRef<number | null>(null);
  const skipNextMessageLoadRef = useRef<number | null>(null);
  const draftTextRef = useRef("");
  const draftDeltaRef = useRef({ text: "", thinking: "" });
  const draftFrameRef = useRef<number | null>(null);
  const draftMessageIdRef = useRef<number | null>(null);
  const streamDoneRef = useRef(false);
  const messageRefs = useRef(new Map<number, HTMLDivElement>());
  const messageRequestsRef = useRef(new LatestRequest());
  const conversationRouteRequestsRef = useRef(new LatestRequest());
  const shouldAutoScroll = useRef(true);
  const programmaticScrollRef = useRef(false);
  const programmaticScrollTimerRef = useRef<number | null>(null);
  const touchStartYRef = useRef<number | null>(null);
  const [highlightedMessageId, setHighlightedMessageId] = useState<number | null>(null);
  const [awayFromBottom, setAwayFromBottom] = useState(false);
  const [dragActive, setDragActive] = useState(false);
  const dragDepthRef = useRef(0);
  const [memoryCount, setMemoryCount] = useState<number | null>(null);
  const [modelCatalog, setModelCatalog] = useState<ModelCatalog | null>(null);
  const [selectedModelProfileId, setSelectedModelProfileId] = useState<number | null>(null);
  const [webSearchAvailable, setWebSearchAvailable] = useState(false);
  const [visionAvailable, setVisionAvailable] = useState(false);
  const [webSearchEnabled, setWebSearchEnabled] = useState(false);
  const [conversationContext, setConversationContext] = useState<ConversationContext | null>(null);
  const [traceIds, setTraceIds] = useState<Record<number, string>>({});
  const selectedIdRef = useRef<number | null>(selectedId);
  const streamConversationIdRef = useRef<number | null>(null);
  const streamBaseTurnsRef = useRef<Turn[]>([]);
  const streamBaseMessagesRef = useRef<import("@/lib/types").ApiMessage[]>([]);
  const sendingRef = useRef(false);
  const [streamConversationId, setStreamConversationId] = useState<number | null>(null);

  const updateSelectedId = useCallback((id: number | null) => {
    selectedIdRef.current = id;
    setSelectedId(id);
  }, []);

  const selectedModelProfile = useMemo(() => {
    if (!modelCatalog) return null;
    const profileId = selectedModelProfileId ?? modelCatalog.default_profile_id;
    return modelCatalog.profiles.find((profile) => profile.id === profileId)
      ?? modelCatalog.profiles.find((profile) => profile.is_default)
      ?? null;
  }, [modelCatalog, selectedModelProfileId]);
  const selectedConversation = conversations.find((item) => item.id === selectedId);
  const thinkingPreferenceKey = selectedModelProfile?.slug || (selectedModelProfile ? `profile:${selectedModelProfile.id}` : "");
  const rememberedThinking = thinkingPreferenceKey ? preferences.modelThinking[thinkingPreferenceKey] : undefined;
  const thinkingEnabled = selectedModelProfile?.capabilities?.thinking
    ? rememberedThinking ?? selectedConversation?.thinking ?? selectedModelProfile.thinking_default ?? false
    : false;
  const thinkingEfforts = selectedModelProfile?.thinking_efforts ?? [];
  const rememberedEffort = thinkingPreferenceKey ? preferences.modelThinkingEffort[thinkingPreferenceKey] : undefined;
  const thinkingEffort = rememberedEffort && thinkingEfforts.includes(rememberedEffort)
    ? rememberedEffort
    : selectedModelProfile?.thinking_effort_default && thinkingEfforts.includes(selectedModelProfile.thinking_effort_default)
      ? selectedModelProfile.thinking_effort_default
      : thinkingEfforts[0] ?? null;

  const updateThinkingPreference = (value: boolean) => {
    if (!thinkingPreferenceKey) return;
    const current = readPreferences();
    const next = {
      ...current,
      modelThinking: { ...current.modelThinking, [thinkingPreferenceKey]: value },
    };
    setPreferences(next);
    writePreferences(next);
  };

  const updateThinkingEffortPreference = (value: string) => {
    if (!thinkingPreferenceKey || !thinkingEfforts.includes(value)) return;
    const current = readPreferences();
    const next = {
      ...current,
      modelThinkingEffort: { ...current.modelThinkingEffort, [thinkingPreferenceKey]: value },
    };
    setPreferences(next);
    writePreferences(next);
  };

  useEffect(() => {
    void getMemoryStats(30, 1).then((stats) => setMemoryCount(stats.total_memories)).catch(() => undefined);
    void getModelCatalog().then(setModelCatalog).catch(() => undefined);
    void getContextPreview().then((context) => {
      if (selectedIdRef.current === null) setConversationContext(context);
    }).catch(() => undefined);
    void getRuntimeSettings().then((settings) => {
      const disabled = new Set(String(settings.values?.toolkits_disabled ?? "").split(",").map((value) => value.trim()).filter(Boolean));
      setWebSearchAvailable(Boolean(settings.web_search_enabled) && !disabled.has("web_search"));
      setVisionAvailable(Boolean(settings.vision_enabled));
    }).catch(() => { setWebSearchAvailable(false); setVisionAvailable(false); });
  }, []);

  useEffect(() => {
    const conversation = conversations.find((item) => item.id === selectedId);
    setSelectedModelProfileId(conversation?.model_profile_id ?? modelCatalog?.default_profile_id ?? null);
  }, [conversations, modelCatalog?.default_profile_id, selectedId]);

  useEffect(() => {
    setPreferences(readPreferences());
    const handlePreferenceChange = (event: Event) => {
      const detail = (event as CustomEvent<UserPreferences>).detail;
      if (detail) setPreferences(detail);
    };
    window.addEventListener(preferencesChangeEvent(), handlePreferenceChange);
    return () => window.removeEventListener(preferencesChangeEvent(), handlePreferenceChange);
  }, []);

  useEffect(() => {
    const syncConversationChange = (event: Event) => {
      const change = (event as CustomEvent<WorkspaceConversationChange>).detail;
      if (!change) return;
      if (change.type === "cleared") {
        abortRef.current?.abort();
        abortRef.current = null;
        messageRequestsRef.current.invalidate();
        conversationRouteRequestsRef.current.invalidate();
        setConversations([]);
        setArchivedConversations([]);
        updateSelectedId(null);
        notifyWorkspaceSelectedConversationChanged(null);
        setApiMessages([]);
        setTurns([]);
        setConversationContext(null);
        setPendingUser("");
        setPendingAttachments([]);
        setDraft({ text: "", thinking: "", tools: [] });
        setEditingTarget(null);
        setEditDraft("");
        setEditAttachments([]);
        setError("");
        router.replace("/");
      } else if (change.type === "renamed") {
        setConversations((current) => current.map((item) => item.id === change.conversation.id ? change.conversation : item));
        setArchivedConversations((current) => current.map((item) => item.id === change.conversation.id ? change.conversation : item));
      } else if (change.type === "archived") {
        if (change.archived) {
          setConversations((current) => current.filter((item) => item.id !== change.conversation.id));
          setArchivedConversations((current) => [change.conversation, ...current.filter((item) => item.id !== change.conversation.id)]);
        } else {
          setArchivedConversations((current) => current.filter((item) => item.id !== change.conversation.id));
          setConversations((current) => [change.conversation, ...current.filter((item) => item.id !== change.conversation.id)]);
        }
      } else {
        setConversations((current) => current.filter((item) => item.id !== change.conversationId));
        setArchivedConversations((current) => current.filter((item) => item.id !== change.conversationId));
      }
    };
    window.addEventListener(conversationsChangedEvent, syncConversationChange);
    return () => window.removeEventListener(conversationsChangedEvent, syncConversationChange);
  }, [router, updateSelectedId]);

  useEffect(() => {
    let active = true;
    void getTtsStatus()
      .then((status) => { if (active) setTtsStatus(status); })
      .catch(() => undefined);
    return () => { active = false; };
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    const messageRequests = messageRequestsRef.current;
    const conversationRouteRequests = conversationRouteRequestsRef.current;
    const audio = audioRef.current;
    return () => {
      mountedRef.current = false;
      messageRequests.invalidate();
      conversationRouteRequests.invalidate();
      abortRef.current?.abort();
      abortRef.current = null;
      speechGenerationRef.current += 1;
      speechAutoActiveRef.current = false;
      speechFlushCompleteRef.current = false;
      speechQueueRef.current = [];
      if (speechPumpTimerRef.current !== null) window.clearTimeout(speechPumpTimerRef.current);
      if (draftFrameRef.current !== null) window.cancelAnimationFrame(draftFrameRef.current);
      if (programmaticScrollTimerRef.current !== null) window.clearTimeout(programmaticScrollTimerRef.current);
      if (audio) resetMediaElement(audio);
      void stopSpeech().catch(() => undefined);
    };
  }, []);

  const loadConversations = useCallback(async (archived = false) => {
    const items = await listConversations(50, archived);
    if (archived) setArchivedConversations(items);
    else setConversations(items);
    return items;
  }, []);

  useEffect(() => {
    const routeRequest = conversationRouteRequestsRef.current.begin();
    void loadConversations()
      .then(async (items) => {
        if (!conversationRouteRequestsRef.current.isCurrent(routeRequest)) return;
        const fromUrl = selectedFromUrl;
        let validUrlId = items.some((item) => item.id === fromUrl) ? fromUrl : undefined;
        if (validUrlId) setShowArchived(false);
        if (!validUrlId && Number.isFinite(fromUrl) && fromUrl > 0) {
          const archived = await loadConversations(true);
          if (!conversationRouteRequestsRef.current.isCurrent(routeRequest)) return;
          if (archived.some((item) => item.id === fromUrl)) {
            setShowArchived(true);
            validUrlId = fromUrl;
          }
        }
        if (validUrlId) {
          updateSelectedId(validUrlId);
          notifyWorkspaceSelectedConversationChanged(validUrlId);
          if (validUrlId !== fromUrl) router.replace(`/?conversation=${validUrlId}`);
        } else {
          updateSelectedId(null);
          notifyWorkspaceSelectedConversationChanged(null);
          setTurns([]);
          if (Number.isFinite(fromUrl) && fromUrl > 0) router.replace("/");
        }
      })
      .catch((cause: unknown) => {
        if (conversationRouteRequestsRef.current.isCurrent(routeRequest)) setError(errorMessage(cause, "无法连接后端"));
      })
      .finally(() => {
        if (conversationRouteRequestsRef.current.isCurrent(routeRequest)) setLoadingConversations(false);
      });
  }, [loadConversations, router, selectedFromUrl, updateSelectedId]);

  // silent：刚流完一轮时用。此时屏幕上已经有完整的回答（draft），再挂一次
  // 「加载消息中…」会把整个列表替换掉一瞬再换回来，看着就像正文是一次性蹦出来的。
  const loadMessages = useCallback(async (id: number, silent = false) => {
    const request = messageRequestsRef.current.begin();
    if (!silent) setLoadingMessages(true);
    setError("");
    try {
      const messages = await listMessages(id);
      if (!messageRequestsRef.current.isCurrent(request)) return;
      // A response finishing in a background conversation must never replace
      // the history of the conversation the user is currently reading.
      if (selectedIdRef.current !== id) return;
      // A history request started before Send can finish after the stream has
      // begun. It is stale even though the selected conversation did not
      // change, and must not erase or duplicate the live turn.
      if (sendingRef.current && streamConversationIdRef.current === id) return;
      messageRefs.current.clear();
      setHighlightedMessageId(null);
      setApiMessages(messages);
      setTurns(toTurns(messages));
      // 和 setTurns 同一批状态更新里清掉临时态，换帧才是原子的：
      // 分开清会先渲染出「权威历史 + 尚未清掉的 draft」这一帧，也就是重影。
      if (streamConversationIdRef.current === id) {
        setPendingUser("");
        setPendingAttachments([]);
        setDraft({ text: "", thinking: "", tools: [] });
        streamConversationIdRef.current = null;
        setStreamConversationId(null);
      }
      void getConversationContext(id).then((context) => {
        if (selectedIdRef.current === id) setConversationContext(context);
      }).catch(() => {
        if (selectedIdRef.current === id) setConversationContext(null);
      });
    } catch (cause) {
      if (!messageRequestsRef.current.isCurrent(request)) return;
      setError(errorMessage(cause, "无法加载消息"));
    } finally {
      if (messageRequestsRef.current.isCurrent(request)) setLoadingMessages(false);
    }
  }, []);

  useEffect(() => {
    if (!selectedId) return;
    // Returning to the conversation that owns the active response should show
    // its exact pre-stream history plus the live draft. Fetching here can
    // duplicate the pending user message or erase the draft mid-response.
    if (sendingRef.current && streamConversationIdRef.current === selectedId) {
      messageRequestsRef.current.invalidate();
      setLoadingMessages(false);
      setApiMessages(streamBaseMessagesRef.current);
      setTurns(streamBaseTurnsRef.current);
      return;
    }
    // 首页新建会话会立刻开始发送。跳过这里的空历史请求，避免它在流式请求
    // 写入用户消息之前返回 []，把 pendingUser 提前清掉。
    if (skipNextMessageLoadRef.current === selectedId) {
      skipNextMessageLoadRef.current = null;
      return;
    }
    void loadMessages(selectedId);
  }, [selectedId, loadMessages]);

  useEffect(() => {
    if (!Number.isFinite(messageFromUrl) || messageFromUrl <= 0 || loadingMessages) return;
    const target = messageRefs.current.get(messageFromUrl);
    if (!target) return;
    target.scrollIntoView({ behavior: "smooth", block: "center" });
    setHighlightedMessageId(messageFromUrl);
    const timer = window.setTimeout(() => setHighlightedMessageId(null), 2600);
    return () => window.clearTimeout(timer);
  }, [loadingMessages, messageFromUrl, turns]);

  useLayoutEffect(() => {
    const element = scrollRef.current;
    if (!element || !preferences.autoScroll || !shouldAutoScroll.current) return;
    programmaticScrollRef.current = true;
    element.scrollTop = element.scrollHeight;
    setAwayFromBottom(false);
    if (programmaticScrollTimerRef.current !== null) window.clearTimeout(programmaticScrollTimerRef.current);
    programmaticScrollTimerRef.current = window.setTimeout(() => {
      programmaticScrollRef.current = false;
      programmaticScrollTimerRef.current = null;
    }, 80);
  }, [draft, pendingUser, preferences.autoScroll, turns]);

  // composer 的高度是变的：textarea 会长到 180px，附件条会整条出现/消失。
  // message-scroll 的底部留白必须**等于实测高度**，写死 152px 的话，输入两行
  // 或贴一张图，最后一条消息就被压在 composer 底下（用户看不到自己刚发的话）。
  useLayoutEffect(() => {
    const panel = mainPanelRef.current;
    if (!panel) return;
    const wrap = composerWrapRef.current;
    if (!wrap) {
      panel.style.removeProperty("--composer-height");
      return;
    }
    const sync = () => {
      panel.style.setProperty("--composer-height", `${Math.ceil(wrap.getBoundingClientRect().height)}px`);
      // 留白变大会把内容顶上去一截。贴着底看的时候要跟着走，
      // 否则每多打一行字，正文就往上溜一行。
      const element = scrollRef.current;
      if (element && preferences.autoScroll && shouldAutoScroll.current) element.scrollTop = element.scrollHeight;
    };
    sync();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(sync);
    observer.observe(wrap);
    return () => observer.disconnect();
  }, [preferences.autoScroll, selectedId]);

  const handleMessageScroll = (element: HTMLDivElement) => {
    const distance = Math.max(0, element.scrollHeight - element.scrollTop - element.clientHeight);
    setAwayFromBottom(distance > 96);
    if (programmaticScrollRef.current) return;
    shouldAutoScroll.current = distance < 32;
  };

  const pauseAutoScroll = () => {
    programmaticScrollRef.current = false;
    if (programmaticScrollTimerRef.current !== null) window.clearTimeout(programmaticScrollTimerRef.current);
    programmaticScrollTimerRef.current = null;
    shouldAutoScroll.current = false;
  };

  const scrollToLatest = () => {
    const element = scrollRef.current;
    if (!element) return;
    shouldAutoScroll.current = true;
    programmaticScrollRef.current = true;
    setAwayFromBottom(false);
    element.scrollTo({ top: element.scrollHeight, behavior: "smooth" });
    if (programmaticScrollTimerRef.current !== null) window.clearTimeout(programmaticScrollTimerRef.current);
    programmaticScrollTimerRef.current = window.setTimeout(() => {
      programmaticScrollRef.current = false;
      programmaticScrollTimerRef.current = null;
      const current = scrollRef.current;
      if (current) current.scrollTop = current.scrollHeight;
    }, 420);
  };

  useEffect(() => {
    const element = composerRef.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${element.scrollHeight}px`;
  }, [input]);

  const selectConversation = (id: number) => {
    stopTtsPlayback();
    updateSelectedId(id);
    setEditingTarget(null);
    setEditDraft("");
    setEditAttachments([]);
    setInput("");
    setConversationContext(null);
    notifyWorkspaceSelectedConversationChanged(id);
    router.push(`/?conversation=${id}`);
  };

  function resetLocalSpeech() {
    speechGenerationRef.current += 1;
    speechAutoActiveRef.current = false;
    speechFlushCompleteRef.current = false;
    speechQueueRef.current = [];
    speechCursorRef.current = 0;
    speechAutoMessageIdRef.current = null;
    speechAudioPlayingRef.current = false;
    if (speechPumpTimerRef.current !== null) {
      window.clearTimeout(speechPumpTimerRef.current);
      speechPumpTimerRef.current = null;
    }
    const audio = audioRef.current;
    if (audio) {
      resetMediaElement(audio);
    }
    setTtsLoadingId(null);
    setTtsPlayingId(null);
  }

  function stopTtsPlayback() {
    resetLocalSpeech();
    void stopSpeech().catch(() => undefined);
  }

  async function playNextSpeech(generation = speechGenerationRef.current) {
    if (generation !== speechGenerationRef.current || speechAudioPlayingRef.current) return;
    const nextUrl = speechQueueRef.current.shift();
    if (!nextUrl) {
      if (speechFlushCompleteRef.current) speechAutoActiveRef.current = false;
      return;
    }
    const audio = audioRef.current;
    if (!audio) return;
    speechAudioPlayingRef.current = true;
    audio.src = nextUrl;
    audio.load();
    try {
      await audio.play();
      if (generation !== speechGenerationRef.current) return;
      setTtsPlayingId(speechAutoMessageIdRef.current);
    } catch (cause) {
      // resetLocalSpeech deliberately interrupts a pending play(). That stale
      // promise belongs to an invalidated playback and must not tear down or
      // report an error for the newer playback that may already be starting.
      if (generation !== speechGenerationRef.current) return;
      speechAudioPlayingRef.current = false;
      speechAutoActiveRef.current = false;
      speechQueueRef.current = [];
      void stopSpeech().catch(() => undefined);
      setTtsPlayingId(null);
      setError(cause instanceof DOMException && cause.name === "NotAllowedError" ? "浏览器阻止了自动播放，请点击消息旁的播放按钮" : errorMessage(cause, "语音播放失败"));
    }
  }

  function enqueueAutoSpeech(flush = false) {
    const generation = speechGenerationRef.current;
    const run = async () => {
      if (!speechAutoActiveRef.current || generation !== speechGenerationRef.current) return;
      const text = draftTextRef.current;
      for (;;) {
        const result = await getNextSpeech({ text, cursor: speechCursorRef.current, flush });
        if (!speechAutoActiveRef.current || generation !== speechGenerationRef.current) return;
        speechCursorRef.current = result.cursor;
        if (!result.url) break;
        speechQueueRef.current.push(apiUrl(result.url));
        void playNextSpeech(generation);
        if (flush) break;
      }
    };
    const next = speechPumpRef.current.catch(() => undefined).then(run);
    speechPumpRef.current = next.catch((cause) => {
      if (generation !== speechGenerationRef.current) return;
      speechAutoActiveRef.current = false;
      speechQueueRef.current = [];
      setError(errorMessage(cause, "自动朗读失败"));
      void stopSpeech().catch(() => undefined);
    });
    return speechPumpRef.current;
  }

  function scheduleAutoSpeech() {
    if (!speechAutoActiveRef.current || speechPumpTimerRef.current !== null) return;
    speechPumpTimerRef.current = window.setTimeout(() => {
      speechPumpTimerRef.current = null;
      void enqueueAutoSpeech();
    }, 300);
  }

  async function startAutoSpeech() {
    resetLocalSpeech();
    await stopSpeech().catch(() => undefined);
    speechAutoActiveRef.current = true;
    speechFlushCompleteRef.current = false;
    speechCursorRef.current = 0;
    speechAutoMessageIdRef.current = null;
  }

  async function flushAutoSpeech() {
    if (speechPumpTimerRef.current !== null) {
      window.clearTimeout(speechPumpTimerRef.current);
      speechPumpTimerRef.current = null;
    }
    await enqueueAutoSpeech(true);
    speechFlushCompleteRef.current = true;
    if (!speechAudioPlayingRef.current && speechQueueRef.current.length === 0) {
      speechAutoActiveRef.current = false;
    }
  }

  function handleSpeechEnded() {
    speechAudioPlayingRef.current = false;
    setTtsPlayingId(null);
    if (speechQueueRef.current.length > 0) void playNextSpeech();
    else if (speechFlushCompleteRef.current) speechAutoActiveRef.current = false;
  }

  function handleSpeechError() {
    if (!speechAudioPlayingRef.current) return;
    speechAudioPlayingRef.current = false;
    speechAutoActiveRef.current = false;
    speechQueueRef.current = [];
    setTtsPlayingId(null);
    setError("语音流播放失败，请在设置页试听并检查语音服务");
    void stopSpeech().catch(() => undefined);
  }

  const flushDraftDeltas = () => {
    if (draftFrameRef.current !== null) window.cancelAnimationFrame(draftFrameRef.current);
    draftFrameRef.current = null;
    const pending = draftDeltaRef.current;
    draftDeltaRef.current = { text: "", thinking: "" };
    if (!pending.text && !pending.thinking) return;
    setDraft((current) => ({
      ...current,
      text: current.text + pending.text,
      thinking: current.thinking + pending.thinking,
    }));
  };

  const queueDraftDelta = (kind: "text" | "thinking", value: string) => {
    draftDeltaRef.current[kind] += value;
    if (draftFrameRef.current === null) draftFrameRef.current = window.requestAnimationFrame(flushDraftDeltas);
  };

  const handleEvent = (event: ChatEvent, conversationId: number | null) => {
    if (event.type === "conversation") {
      const created = event.conversation;
      setConversations((current) => [created, ...current.filter((item) => item.id !== created.id)]);
      streamConversationIdRef.current = created.id;
      setStreamConversationId(created.id);
      if (created.model_profile_id !== null && created.model_profile_id !== undefined) {
        setSelectedModelProfileId(created.model_profile_id);
      }
      // A home-page send opens its new conversation only while the user is
      // still on Home. If they deliberately opened another conversation before
      // the server assigned an id, keep their navigation choice intact.
      if (selectedIdRef.current === null) {
        skipNextMessageLoadRef.current = created.id;
        updateSelectedId(created.id);
        notifyWorkspaceSelectedConversationChanged(created.id);
        setTurns([]);
        router.replace(`/?conversation=${created.id}`);
      }
      notifyWorkspaceConversationsChanged();
      return;
    }
    if (event.type === "trace" && conversationId !== null) {
      setTraceIds((current) => ({ ...current, [conversationId]: event.trace_id }));
      return;
    }
    if (event.type === "thinking_delta") queueDraftDelta("thinking", event.text);
    if (event.type === "text_delta") {
      draftTextRef.current += event.text;
      queueDraftDelta("text", event.text);
      scheduleAutoSpeech();
    }
    if (event.type === "message_id") {
      draftMessageIdRef.current = event.message_id;
      speechAutoMessageIdRef.current = event.message_id;
      if (speechAudioPlayingRef.current) setTtsPlayingId(event.message_id);
    }
    if (event.type === "done") { flushDraftDeltas(); streamDoneRef.current = true; }
    if (event.type === "title" && conversationId !== null) {
      setConversations((current) => current.map((item) => item.id === conversationId ? { ...item, title: event.title } : item));
      notifyWorkspaceConversationsChanged();
    }
    if (event.type === "tool_use") {
      setDraft((current) => ({ ...current, tools: [...current.tools, { name: event.name, input: event.input, ok: true, summary: "", status: "running" }] }));
    }
    if (event.type === "tool_result") {
      setDraft((current) => {
        const index = [...current.tools].reverse().findIndex((tool) => tool.status === "running" && tool.name === event.name);
        const actualIndex = index === -1 ? -1 : current.tools.length - 1 - index;
        if (actualIndex === -1) return { ...current, tools: [...current.tools, { name: event.name, input: {}, ok: event.ok, summary: event.summary, status: "done" }] };
        const tools = [...current.tools];
        tools[actualIndex] = { ...tools[actualIndex], ok: event.ok, summary: event.summary, status: "done" };
        return { ...current, tools };
      });
    }
    if (event.type === "error") {
      streamDoneRef.current = false;
      setError(event.message);
    }
  };

  const ttsAvailable = Boolean(ttsStatus && ttsStatus.mode !== "off" && ttsStatus.enabled);
  const ttsDisabledReason = !ttsStatus
    ? t("chat.tts.unknown")
    : !ttsStatus.enabled
      ? t("chat.tts.unknown")
      : t("chat.tts.unknown");

  const speakText = async (text: string, messageId?: number) => {
    if (!text.trim() || !ttsStatus || ttsStatus.mode === "off") return;
    const audio = audioRef.current;
    if (!audio) return;
    if (messageId !== undefined && ttsPlayingId === messageId) {
      stopTtsPlayback();
      return;
    }
    if (!ttsAvailable) {
      setError(ttsDisabledReason);
      return;
    }
    if (speechBusyGenerationRef.current === speechGenerationRef.current) return;

    resetLocalSpeech();
    const generation = speechGenerationRef.current;
    speechBusyGenerationRef.current = generation;
    setTtsLoadingId(messageId ?? null);
    setError("");
    try {
      await stopSpeech().catch(() => undefined);
      if (generation !== speechGenerationRef.current) return;
      const prepared = await prepareSpeech({ text });
      if (generation !== speechGenerationRef.current) return;
      audio.src = apiUrl(prepared.url);
      audio.currentTime = 0;
      audio.load();
      speechAudioPlayingRef.current = true;
      await audio.play();
      if (generation !== speechGenerationRef.current) return;
      setTtsPlayingId(messageId ?? null);
    } catch (cause) {
      if (generation !== speechGenerationRef.current) return;
      speechAudioPlayingRef.current = false;
      setTtsPlayingId(null);
      setError(cause instanceof DOMException && cause.name === "NotAllowedError" ? "浏览器阻止了自动播放，请点击消息旁的播放按钮" : errorMessage(cause, "语音播放失败"));
    } finally {
      if (speechBusyGenerationRef.current === generation) speechBusyGenerationRef.current = null;
      if (generation === speechGenerationRef.current) setTtsLoadingId(null);
    }
  };

  const addAttachments = async (files: File[]) => {
    const images = files.filter((file) => file.type.startsWith("image/"));
    if (files.length > 0 && images.length < files.length) setError(t("chat.attachment.unsupported"));
    if (images.length === 0 || sending) return;
    if (!visionAvailable) { setError(t("chat.attachment.unavailable")); return; }
    if (images.length === files.length) setError("");
    setUploading((current) => current + images.length);
    for (const file of images) {
      const previewUrl = URL.createObjectURL(file);
      try {
        const meta = await uploadAttachment(file, selectedId);
        if (!mountedRef.current) return;
        setAttachments((current) => [...current, { ...meta, previewUrl }]);
      } catch (cause) {
        URL.revokeObjectURL(previewUrl);
        if (!mountedRef.current) return;
        setError(errorMessage(cause, t("chat.attachment.uploadFailed")));
      } finally {
        if (mountedRef.current) setUploading((current) => Math.max(0, current - 1));
      }
    }
  };

  const removeAttachment = (id: number) => setAttachments((current) => {
    const removed = current.find((item) => item.id === id);
    if (removed?.previewUrl) URL.revokeObjectURL(removed.previewUrl);
    return current.filter((item) => item.id !== id);
  });

  const pickImage = () => fileInputRef.current?.click();

  const onPasteFiles = (event: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const files = Array.from(event.clipboardData.files);
    // 只在真的有图时拦截：剪贴板里同时有文字和图片是常见的，
    // 那种情况下文字仍然该正常粘进输入框。
    if (files.some((file) => file.type.startsWith("image/"))) {
      event.preventDefault();
      void addAttachments(files);
    }
  };

  // dragenter/dragleave 在每个子元素边界上都会各触发一次，所以要计数而不是
  // 置布尔 —— 用布尔的话鼠标从 textarea 划到按钮上高亮就会闪一下。
  const dragCarriesFiles = (event: React.DragEvent) => event.dataTransfer.types.includes("Files");

  const onDragEnterFiles = (event: React.DragEvent) => {
    if (!dragCarriesFiles(event)) return;
    event.preventDefault();
    dragDepthRef.current += 1;
    setDragActive(true);
  };

  const onDragOverFiles = (event: React.DragEvent) => {
    if (!dragCarriesFiles(event)) return;
    event.preventDefault();
    // 看不了图的模型就直接显示「禁止」光标，别让用户拖完才发现不支持。
    event.dataTransfer.dropEffect = visionAvailable ? "copy" : "none";
  };

  const onDragLeaveFiles = (event: React.DragEvent) => {
    if (!dragCarriesFiles(event)) return;
    dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
    if (dragDepthRef.current === 0) setDragActive(false);
  };

  const onDropFiles = (event: React.DragEvent) => {
    dragDepthRef.current = 0;
    setDragActive(false);
    const files = Array.from(event.dataTransfer.files);
    if (files.some((file) => file.type.startsWith("image/"))) {
      event.preventDefault();
      void addAttachments(files);
    }
  };

  const dropTargetProps = {
    onDragEnter: onDragEnterFiles,
    onDragOver: onDragOverFiles,
    onDragLeave: onDragLeaveFiles,
    onDrop: onDropFiles,
  };

  const send = async (contentOverride?: string, targetIdOverride?: number, conversationIdOverride?: number, attachmentsOverride?: TurnAttachment[]) => {
    const content = (contentOverride ?? input).trim();
    const conversationId = conversationIdOverride ?? selectedId;
    // 编辑重发/重新生成要把原消息的图**带回来**：那条消息会被软删除、重新落一条新的，
    // 不显式传就等于编辑一次图片就没了（而用户只改了几个字）。
    // 附件行可以改挂到新消息上，所以重复用同一批 id 是安全的。
    const outgoingAttachments = attachmentsOverride ?? (targetIdOverride === undefined && editingTarget === null ? attachments.map((item) => ({ id: item.id, filename: item.filename, previewUrl: item.previewUrl })) : []);
    // 只贴图不打字是正常用法，所以不能再要求 content 非空
    if ((!content && outgoingAttachments.length === 0) || sending) return;
    if (uploading > 0) return;
    let activeConversationId = conversationId;
    const targetId = targetIdOverride ?? editingTarget;
    const baseMessages = targetId !== null && targetId !== undefined
      ? apiMessages.slice(0, Math.max(0, apiMessages.findIndex((message) => message.id === targetId)))
      : apiMessages;
    const baseTurns = targetId !== null && targetId !== undefined ? toTurns(baseMessages) : turns;
    streamBaseMessagesRef.current = baseMessages;
    streamBaseTurnsRef.current = baseTurns;
    streamConversationIdRef.current = conversationId;
    setStreamConversationId(conversationId);
    if (conversationId !== null) {
      setTraceIds((current) => {
        if (!(conversationId in current)) return current;
        const next = { ...current };
        delete next[conversationId];
        return next;
      });
    }
    sendingRef.current = true;
    setSending(true);
    setError("");
    setInput("");
    setPendingUser(content);
    setAttachments([]);
    setPendingAttachments(outgoingAttachments);
    if (draftFrameRef.current !== null) window.cancelAnimationFrame(draftFrameRef.current);
    draftFrameRef.current = null;
    setDraft({ text: "", thinking: "", tools: [] });
    draftDeltaRef.current = { text: "", thinking: "" };
    draftTextRef.current = "";
    draftMessageIdRef.current = null;
    streamDoneRef.current = false;
    shouldAutoScroll.current = true;
    setAwayFromBottom(false);
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      if (ttsStatus?.mode === "auto") await startAutoSpeech();
      else stopTtsPlayback();
      if (targetId !== null && targetId !== undefined && activeConversationId !== null) {
        const targetIndex = apiMessages.findIndex((message) => message.id === targetId);
        if (targetIndex < 0) throw new Error("找不到要重发的消息");
        const after = targetIndex > 0 ? apiMessages[targetIndex - 1].id : 0;
        await truncateMessages(activeConversationId, after);
        setTurns(baseTurns);
      }
      await streamChat(activeConversationId, content, selectedModelProfileId, (event) => {
        if (event.type === "conversation") activeConversationId = event.conversation.id;
        handleEvent(event, activeConversationId);
      }, controller.signal, webSearchEnabled, outgoingAttachments.map((item) => item.id), selectedModelProfile?.capabilities?.thinking ? thinkingEnabled : undefined, thinkingEnabled ? thinkingEffort : null);
      if (ttsStatus?.mode === "auto" && streamDoneRef.current && draftTextRef.current.trim()) {
        await flushAutoSpeech();
      }
    } catch (cause) {
      if (!mountedRef.current) return;
      if (!streamDoneRef.current) stopTtsPlayback();
      if (!(cause instanceof DOMException && cause.name === "AbortError")) setError(errorMessage(cause, "聊天失败"));
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
      if (!mountedRef.current) return;
      sendingRef.current = false;
      setSending(false);
      setEditingTarget(null);
      setEditDraft("");
      setEditAttachments([]);
      // 临时态由 loadMessages 在换上权威历史的同一帧里清掉，这里不要提前清 ——
      // 提前清会让刚说完的回答先消失，等 fetch 回来再出现。
      await Promise.all([
        activeConversationId === null ? Promise.resolve() : loadMessages(activeConversationId, true),
        loadConversations().catch(() => undefined),
      ]);
      window.setTimeout(() => { void loadConversations().catch(() => undefined); }, 1200);
    }
  };

  const stop = () => {
    abortRef.current?.abort();
    stopTtsPlayback();
  };

  // Esc 停止生成。此前必须用鼠标点方块按钮，而 Esc 是 macOS 上「让它停下」
  // 的肌肉记忆。abort 之后 send() 的 catch 分支会顺带停掉 TTS。
  useEffect(() => {
    if (!sending) return;
    const onEscape = (event: globalThis.KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented) return;
      // Esc 在这个应用里还负责关浮层和退出编辑，更「近」的意图优先。
      if (editingTarget !== null) return;
      if (document.querySelector(".search-dialog, .confirm-dialog, .shortcuts-dialog, .chat-model-menu, .composer-tool-menu, .thinking-menu, .workspace-conversation-menu")) return;
      abortRef.current?.abort();
    };
    window.addEventListener("keydown", onEscape);
    return () => window.removeEventListener("keydown", onEscape);
  }, [editingTarget, sending]);
  const startHomeConversation = (event: FormEvent) => {
    event.preventDefault();
    const content = input.trim();
    if ((!content && attachments.length === 0) || sending) return;
    void send(content);
  };
  const onSubmit = (event: FormEvent) => { event.preventDefault(); void send(); };
  const appendTranscription = (text: string) => {
    setInput((current) => current.trimEnd() ? `${current.trimEnd()}\n${text}` : text);
    window.requestAnimationFrame(() => composerRef.current?.focus());
  };
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    // 中文/日文等输入法选词阶段也会触发 Enter。此时必须交给 IME 完成
    // 合成，不能把尚未确认的拼音直接提交成一条消息。
    if (event.nativeEvent.isComposing || event.keyCode === 229) return;
    if (event.key === "Enter" && !event.shiftKey && preferences.enterToSend) {
      event.preventDefault();
      if (selectedId === null) event.currentTarget.form?.requestSubmit();
      else void send();
    }
    if (event.key === "Escape" && editingTarget !== null) { setEditingTarget(null); setEditDraft(""); setEditAttachments([]); }
  };

  const editMessage = (turn: Extract<Turn, { kind: "user" }>) => {
    if (turn.messageId === undefined) return;
    setEditingTarget(turn.messageId);
    setEditDraft(turn.text);
    setEditAttachments(turn.attachments ?? []);
  };

  const cancelEditing = () => {
    setEditingTarget(null);
    setEditDraft("");
    setEditAttachments([]);
    window.requestAnimationFrame(() => composerRef.current?.focus());
  };

  const submitEditing = () => {
    if (editingTarget === null || sending) return;
    if (!editDraft.trim() && editAttachments.length === 0) return;
    void send(editDraft, editingTarget, undefined, editAttachments);
  };

  // 读屏此前对流式生成完全静默：发出去之后没有任何声音，直到用户自己去翻正文。
  // 只在「开始」和「结束」两个节点播报，逐 token 播报等于噪音。
  const [liveAnnouncement, setLiveAnnouncement] = useState("");
  const wasStreamingRef = useRef(false);
  useEffect(() => {
    const streaming = sending && streamConversationId === selectedId;
    if (streaming && !wasStreamingRef.current) setLiveAnnouncement(t("chat.status.responding"));
    else if (!streaming && wasStreamingRef.current) setLiveAnnouncement(t("chat.status.done"));
    wasStreamingRef.current = streaming;
  }, [selectedId, sending, streamConversationId, t]);

  const streamConversation = useMemo(() => conversations.find((item) => item.id === streamConversationId), [conversations, streamConversationId]);
  const streamBelongsToSelection = streamConversationId !== null && streamConversationId === selectedId;
  const selectedTraceId = selectedId === null ? undefined : traceIds[selectedId];
  const displayTurns = useMemo(() => {
    const result = [...turns] as Turn[];
    // 纯图片消息没有正文，所以判据要带上附件 —— 只看 pendingUser 的话
    // 那种消息在等待期间气泡整个不出现，看着像没发出去。
    if (streamBelongsToSelection && (pendingUser || pendingAttachments.length > 0)) {
      result.push({ kind: "user", text: pendingUser, attachments: pendingAttachments });
    }
    // sending 期间无条件给出助手气泡：首个 token 之前它只有一个闪烁光标，
    // 否则从发送到首字之间界面上没有任何「在回答」的迹象，看着像卡死。
    // 流结束后不看 sending，让 draft 一直留在屏幕上，直到 loadMessages
    // 把权威历史换进来（见 loadMessages）—— 中间不能有空档，否则正文会闪一下。
    if (streamBelongsToSelection && (sending || draft.text || draft.thinking || draft.tools.length)) {
      result.push({ kind: "assistant", text: draft.text, thinking: draft.thinking, tools: draft.tools });
    }
    return result;
  }, [draft, pendingAttachments, pendingUser, sending, streamBelongsToSelection, turns]);

  if (loadingConversations) return <WorkspacePageFallback active="chat" messageKey="loading.connecting" />;

  return (
    <div className="app-shell">
      <main className="main-panel" ref={mainPanelRef}>
        {selectedId === null ? <HomeDashboard input={input} memoryCount={memoryCount} sending={sending} profileName={preferences.profileName || defaultPreferences.profileName} backgroundResponseTitle={sending && streamConversationId !== null ? streamConversation?.title ?? t("chat.current") : undefined} composerRef={composerRef} onInput={setInput} onTranscription={appendTranscription} onKeyDown={onKeyDown} onSubmit={startHomeConversation} onReturnToResponse={() => { if (streamConversationId !== null) selectConversation(streamConversationId); }} modelCatalog={modelCatalog} modelProfileId={selectedModelProfileId} onModelChange={setSelectedModelProfileId} thinkingProfile={selectedModelProfile} thinkingEnabled={thinkingEnabled} thinkingEffort={thinkingEffort} onThinkingChange={updateThinkingPreference} onThinkingEffortChange={updateThinkingEffortPreference} webSearchEnabled={webSearchEnabled} webSearchAvailable={webSearchAvailable} onWebSearchChange={setWebSearchEnabled} attachments={attachments} uploading={uploading} visionAvailable={visionAvailable} onPickImage={pickImage} onRemoveAttachment={removeAttachment} onPasteFiles={onPasteFiles} dropTargetProps={dropTargetProps} dragActive={dragActive} context={conversationContext} /> : <>
          <div className="chat-conversation-toolbar">
            <div className="chat-conversation-toolbar-inner">
              <div className="chat-conversation-identity"><MemoryMark /><strong title={selectedConversation?.title}>{selectedConversation?.title ?? t("chat.current")}</strong></div>
              <div className="chat-conversation-toolbar-right">
                {sending && !streamBelongsToSelection && streamConversationId !== null && <button type="button" className="background-stream-button" onClick={() => selectConversation(streamConversationId)} title={t("chat.returnToResponse")}><LoaderCircle size={13} className="spin" /><span>{t("chat.backgroundResponse", { title: streamConversation?.title ?? t("chat.current") })}</span><ArrowRight size={13} /></button>}
                <ModelPicker catalog={modelCatalog} value={selectedModelProfileId} disabled={sending} onChange={setSelectedModelProfileId} />
              </div>
            </div>
          </div>
          <div className="message-scroll" ref={scrollRef} onWheel={(event) => { if (event.deltaY < 0) pauseAutoScroll(); }} onTouchStart={(event) => { touchStartYRef.current = event.touches[0]?.clientY ?? null; }} onTouchMove={(event) => { const start = touchStartYRef.current; const current = event.touches[0]?.clientY; if (start !== null && current !== undefined && current > start + 4) pauseAutoScroll(); }} onTouchEnd={() => { touchStartYRef.current = null; }} onScroll={(event) => handleMessageScroll(event.currentTarget)}>
            {loadingMessages && !(streamBelongsToSelection && pendingUser) ? <div className="centered-empty">{t("chat.loadingMessages")}</div> : displayTurns.length === 0 ? <div className="chat-empty-state"><span><Sparkles size={18} /></span><h2>{t("chat.empty.title")}</h2><p>{t("chat.empty.description")}</p></div> : displayTurns.map((turn, index) => { const previous = index > 0 ? displayTurns[index - 1] : undefined; const previousUser = previous?.kind === "user" ? previous : undefined; const isAssistant = turn.kind === "assistant"; const editing = turn.kind === "user" && turn.messageId === editingTarget; const hasSpeechButton = isAssistant && turn.messageId !== undefined && ttsStatus?.mode !== undefined && ttsStatus.mode !== "off"; const isLatestAssistant = isAssistant && index === displayTurns.length - 1; return <TurnView turn={turn} traceId={isLatestAssistant ? selectedTraceId : undefined} editing={editing} editText={editing ? editDraft : ""} enterToSend={preferences.enterToSend} onEditChange={editing ? setEditDraft : undefined} onEditSubmit={editing ? submitEditing : undefined} onEditCancel={editing ? cancelEditing : undefined} showToolActivity={preferences.showToolActivity} showUsage={preferences.showUsage} highlighted={turn.messageId === highlightedMessageId} ttsAvailable={ttsAvailable} ttsDisabledReason={ttsDisabledReason} ttsLoading={isAssistant && turn.messageId !== undefined && ttsLoadingId === turn.messageId} ttsPlaying={isAssistant && turn.messageId !== undefined && ttsPlayingId === turn.messageId} turnRef={(node) => { if (turn.messageId !== undefined) { if (node) messageRefs.current.set(turn.messageId, node); else messageRefs.current.delete(turn.messageId); } }} onEdit={turn.kind === "user" && !sending && !editing ? () => editMessage(turn) : undefined} onRegenerate={turn.kind === "assistant" && !sending && previousUser?.messageId !== undefined ? () => void send(previousUser.text, previousUser.messageId, undefined, previousUser.attachments ?? []) : undefined} onSpeak={hasSpeechButton ? () => void speakText(turn.text, turn.messageId) : undefined} streaming={sending && streamBelongsToSelection && index === displayTurns.length - 1 && turn.kind === "assistant"} key={turnKey(turn, index)} />; })}
          </div>
          {awayFromBottom && <button className="chat-scroll-latest" type="button" aria-label={t("chat.scrollToBottom")} title={t("chat.scrollToBottom")} onClick={scrollToLatest}><ArrowDown size={17} /><span>{t("chat.scrollToBottom")}</span></button>}
          <div className="composer-wrap" ref={composerWrapRef}>
            {error && <div className="error-banner" role="alert">{error}</div>}
            <form className={`composer ${dragActive ? "is-drop-target" : ""}`} onSubmit={onSubmit} {...dropTargetProps}>
              {dragActive && <DropHint available={visionAvailable} />}
              <ComposerAttachments items={attachments} uploading={uploading} onRemove={removeAttachment} />
              <textarea ref={composerRef} rows={1} value={input} onChange={(event) => setInput(event.target.value)} onKeyDown={onKeyDown} onPaste={onPasteFiles} placeholder={sending && !streamBelongsToSelection ? t("chat.composer.backgroundPlaceholder") : t("chat.composer.placeholder")} disabled={sending} />
              <div className="composer-bottom">
                <div className="composer-meta">
                  <ComposerToolMenu enabled={webSearchEnabled} available={webSearchAvailable} disabled={sending} onChange={setWebSearchEnabled} onPickImage={pickImage} imageAvailable={visionAvailable} />
                  <ThinkingControl profile={selectedModelProfile} enabled={thinkingEnabled} effort={thinkingEffort} disabled={sending} onEnabledChange={updateThinkingPreference} onEffortChange={updateThinkingEffortPreference} />
                  <ContextIndicator context={conversationContext} />
                  <span className="composer-hint">{sending && !streamBelongsToSelection ? t("chat.backgroundHint") : t("chat.sendHint")}</span>
                </div>
                <div className="composer-actions">
                  <VoiceInputButton disabled={sending} onTranscript={appendTranscription} />
                  {sending
                    ? streamBelongsToSelection ? <button type="button" className="ghost-button stop-button composer-submit" onClick={stop} aria-label={t("chat.stop")} title={t("chat.stop")}><Square size={14} /></button> : <button type="button" className="ghost-button composer-submit" onClick={() => streamConversationId !== null && selectConversation(streamConversationId)} aria-label={t("chat.returnToResponse")} title={t("chat.returnToResponse")}><ArrowRight size={15} /></button>
                    : <button type="submit" className="primary-button composer-submit" disabled={(!input.trim() && attachments.length === 0) || uploading > 0} aria-label={t("chat.send")} title={t("chat.send")}><Send size={17} /></button>}
                </div>
              </div>
            </form>
          </div>
        </>}
        {/* 两个 composer 共用这一个。value 每次清掉，否则连着选同一张图不会触发 change */}
        <input ref={fileInputRef} type="file" accept="image/png,image/jpeg,image/gif,image/webp" multiple hidden onChange={(event) => { const files = Array.from(event.target.files ?? []); event.target.value = ""; void addAttachments(files); }} />
        <audio ref={audioRef} className="tts-audio" onEnded={handleSpeechEnded} onError={handleSpeechError} />
        <p className="sr-only" role="status" aria-live="polite">{liveAnnouncement}</p>
      </main>
    </div>
  );
}
