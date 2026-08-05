"use client";

import { FormEvent, KeyboardEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { Archive, ArchiveRestore, BookOpen, Bot, CalendarDays, LoaderCircle, Menu, MessageSquare, Pencil, Plus, RefreshCw, Send, Settings2, Square, Trash2, TriangleAlert, Brain } from "lucide-react";
import { archiveConversation, createConversation, deleteConversation, errorMessage, getRuntimeSettings, listConversations, listMessages, streamChat, truncateMessages, updateConversation } from "@/lib/api";
import { defaultPreferences, preferencesChangeEvent, readPreferences, type UserPreferences } from "@/lib/preferences";
import { toTurns, toolLabel } from "@/lib/turns";
import type { ChatEvent, Conversation, RuntimeSettings, ToolActivity, Turn } from "@/lib/types";
import { Markdown } from "@/components/markdown";
import { ThemeControl } from "@/components/theme-control";

interface LiveTool extends ToolActivity { status: "running" | "done"; }

function dateLabel(value: string) {
  return new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric" }).format(new Date(value));
}

function displayTool(tool: LiveTool | ToolActivity) {
  const running = "status" in tool && tool.status === "running";
  return (
    <div className={`tool-activity ${running ? "running" : tool.ok ? "" : "failed"}`} key={`${tool.name}-${tool.summary}-${JSON.stringify(tool.input)}`}>
      {running ? <LoaderCircle size={13} className="spin" /> : tool.ok ? <span>✓</span> : <span>!</span>}
      <span>{toolLabel(tool)}</span>
      {!running && tool.summary && <span className="tool-summary">{tool.summary}</span>}
    </div>
  );
}

function usageLabel(usage: NonNullable<Extract<Turn, { kind: "assistant" }>["usage"]>) {
  const output = usage.completion_tokens ?? usage.output_tokens;
  return typeof output === "number" ? `${output.toLocaleString()} output tokens` : "";
}

function TurnView({ turn, streaming = false, highlighted = false, showThinking = true, showToolActivity = true, showUsage = true, turnRef, onEdit, onRegenerate }: { turn: Turn; streaming?: boolean; highlighted?: boolean; showThinking?: boolean; showToolActivity?: boolean; showUsage?: boolean; turnRef?: (node: HTMLDivElement | null) => void; onEdit?: () => void; onRegenerate?: () => void }) {
  if (turn.kind === "user") {
    return <div className={`turn user-turn ${highlighted ? "message-highlight" : ""}`} ref={turnRef} data-message-id={turn.messageId}><div className="user-message-group"><div className="user-bubble">{turn.text}</div>{onEdit && <div className="turn-actions"><button onClick={onEdit}><Pencil size={12} />编辑重发</button></div>}</div></div>;
  }
  return (
    <div className={`turn assistant-turn ${highlighted ? "message-highlight" : ""}`} ref={turnRef} data-message-id={turn.messageId}>
      {showThinking && turn.thinking && (
        <details className="thinking">
          <summary>{streaming ? "思考中…" : "思考过程"}</summary>
          <div className="thinking-body">{turn.thinking}</div>
        </details>
      )}
      {showToolActivity && turn.tools.map((tool) => displayTool(tool))}
      {turn.text && <div className="assistant-content"><Markdown>{turn.text}</Markdown></div>}
      {turn.usage?.interrupted && <div className="interrupted-answer"><TriangleAlert size={13} /><span>回答被中断，已保留已生成的内容</span></div>}
      {showUsage && !turn.usage?.interrupted && turn.usage && usageLabel(turn.usage) && <div className="message-usage">{usageLabel(turn.usage)}</div>}
      {onRegenerate && <div className="turn-actions assistant-actions"><button onClick={onRegenerate}><RefreshCw size={12} />重新生成</button></div>}
    </div>
  );
}

export function ChatPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const selectedFromUrl = Number(searchParams.get("conversation"));
  const messageFromUrl = Number(searchParams.get("message"));
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [apiMessages, setApiMessages] = useState<import("@/lib/types").ApiMessage[]>([]);
  const [archivedConversations, setArchivedConversations] = useState<Conversation[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(Number.isFinite(selectedFromUrl) && selectedFromUrl > 0 ? selectedFromUrl : null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [pendingUser, setPendingUser] = useState("");
  const [draft, setDraft] = useState({ text: "", thinking: "", tools: [] as LiveTool[] });
  const [input, setInput] = useState("");
  const [loadingConversations, setLoadingConversations] = useState(true);
  const [loadingMessages, setLoadingMessages] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [showArchived, setShowArchived] = useState(false);
  const [editingTarget, setEditingTarget] = useState<number | null>(null);
  const [runtimeSettings, setRuntimeSettings] = useState<RuntimeSettings | null>(null);
  const [updatingThinking, setUpdatingThinking] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [preferences, setPreferences] = useState<UserPreferences>(defaultPreferences);
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const messageRefs = useRef(new Map<number, HTMLDivElement>());
  const shouldAutoScroll = useRef(true);
  const [highlightedMessageId, setHighlightedMessageId] = useState<number | null>(null);

  useEffect(() => {
    void getRuntimeSettings().then(setRuntimeSettings).catch(() => undefined);
    setPreferences(readPreferences());
    const handlePreferenceChange = (event: Event) => {
      const detail = (event as CustomEvent<UserPreferences>).detail;
      if (detail) setPreferences(detail);
    };
    window.addEventListener(preferencesChangeEvent(), handlePreferenceChange);
    return () => window.removeEventListener(preferencesChangeEvent(), handlePreferenceChange);
  }, []);

  const loadConversations = useCallback(async (archived = false) => {
    const items = await listConversations(50, archived);
    if (archived) setArchivedConversations(items);
    else setConversations(items);
    return items;
  }, []);

  useEffect(() => {
    void loadConversations()
      .then(async (items) => {
        const fromUrl = selectedFromUrl;
        let validUrlId = items.some((item) => item.id === fromUrl) ? fromUrl : undefined;
        if (validUrlId) setShowArchived(false);
        if (!validUrlId && Number.isFinite(fromUrl) && fromUrl > 0) {
          const archived = await loadConversations(true);
          if (archived.some((item) => item.id === fromUrl)) {
            setShowArchived(true);
            validUrlId = fromUrl;
          }
        }
        validUrlId ??= items[0]?.id;
        if (validUrlId) {
          setSelectedId(validUrlId);
          if (validUrlId !== fromUrl) router.replace(`/?conversation=${validUrlId}`);
        } else {
          const created = await createConversation();
          setConversations([created]);
          setSelectedId(created.id);
          router.replace(`/?conversation=${created.id}`);
        }
      })
      .catch((cause: unknown) => setError(errorMessage(cause, "无法连接后端")))
      .finally(() => setLoadingConversations(false));
  }, [loadConversations, router, selectedFromUrl]);

  const loadMessages = useCallback(async (id: number) => {
    setLoadingMessages(true);
    setError("");
    try {
      const messages = await listMessages(id);
      messageRefs.current.clear();
      setHighlightedMessageId(null);
      setApiMessages(messages);
      setTurns(toTurns(messages));
    } catch (cause) {
      setError(errorMessage(cause, "无法加载消息"));
    } finally {
      setLoadingMessages(false);
    }
  }, []);

  useEffect(() => {
    if (selectedId) void loadMessages(selectedId);
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

  useEffect(() => {
    const element = scrollRef.current;
    if (element && preferences.autoScroll && shouldAutoScroll.current) element.scrollTop = element.scrollHeight;
  }, [draft, pendingUser, preferences.autoScroll, turns]);

  const selectConversation = (id: number) => {
    if (sending) return;
    setSelectedId(id);
    setSidebarOpen(false);
    setEditingTarget(null);
    setInput("");
    router.push(`/?conversation=${id}`);
  };

  const newConversation = async () => {
    try {
      setShowArchived(false);
      const created = await createConversation();
      setConversations((current) => [created, ...current]);
      selectConversation(created.id);
      setTurns([]);
    } catch (cause) {
      setError(errorMessage(cause, "无法创建会话"));
    }
  };

  const toggleArchived = async (conversation: Conversation, archived: boolean) => {
    try {
      await archiveConversation(conversation.id, archived);
      if (archived) {
        const remaining = conversations.filter((item) => item.id !== conversation.id);
        setConversations(remaining);
        if (selectedId === conversation.id) {
          const next = remaining[0] ?? await createConversation();
          if (!remaining[0]) setConversations([next]);
          selectConversation(next.id);
        }
      } else {
        setArchivedConversations((current) => current.filter((item) => item.id !== conversation.id));
      }
    } catch (cause) {
      setError(errorMessage(cause, archived ? "归档失败" : "恢复会话失败"));
    }
  };

  const toggleArchivedView = async () => {
    const next = !showArchived;
    setShowArchived(next);
    try { await loadConversations(next); } catch (cause) { setError(errorMessage(cause, next ? "无法加载归档会话" : "无法刷新会话列表")); }
  };

  const changeThinking = async (value: string) => {
    if (!selectedId || !selected || updatingThinking || sending) return;
    const thinking = value === "default" ? null : value === "on";
    setUpdatingThinking(true);
    setError("");
    try {
      const updated = await updateConversation(selectedId, { thinking });
      setConversations((current) => current.map((item) => item.id === updated.id ? updated : item));
      setArchivedConversations((current) => current.map((item) => item.id === updated.id ? updated : item));
    } catch (cause) {
      setError(errorMessage(cause, "无法更新思考模式"));
    } finally {
      setUpdatingThinking(false);
    }
  };

  const renameSelectedConversation = async () => {
    if (!selected || renaming || sending) return;
    const nextTitle = window.prompt("修改会话标题", selected.title)?.trim();
    if (!nextTitle || nextTitle === selected.title) return;
    setRenaming(true);
    setError("");
    try {
      const updated = await updateConversation(selected.id, { title: nextTitle });
      setConversations((current) => current.map((item) => item.id === updated.id ? updated : item));
      setArchivedConversations((current) => current.map((item) => item.id === updated.id ? updated : item));
    } catch (cause) {
      setError(errorMessage(cause, "无法修改会话标题"));
    } finally {
      setRenaming(false);
    }
  };

  const removeConversation = async (conversation: Conversation) => {
    if (!window.confirm(`确定删除「${conversation.title}」吗？此操作不可撤销。`)) return;
    try {
      await deleteConversation(conversation.id);
      const remaining = (showArchived ? archivedConversations : conversations).filter((item) => item.id !== conversation.id);
      if (showArchived) setArchivedConversations(remaining);
      else setConversations(remaining);
      if (selectedId === conversation.id) {
        const next = remaining[0] ?? await createConversation();
        if (!remaining[0]) setConversations([next]);
        selectConversation(next.id);
      }
    } catch (cause) {
      setError(errorMessage(cause, "无法删除会话"));
    }
  };

  const handleEvent = (event: ChatEvent) => {
    if (event.type === "thinking_delta") setDraft((current) => ({ ...current, thinking: current.thinking + event.text }));
    if (event.type === "text_delta") setDraft((current) => ({ ...current, text: current.text + event.text }));
    if (event.type === "title") setConversations((current) => current.map((item) => item.id === selectedId ? { ...item, title: event.title } : item));
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
    if (event.type === "error") setError(event.message);
  };

  const send = async (contentOverride?: string, targetIdOverride?: number) => {
    const content = (contentOverride ?? input).trim();
    if (!content || !selectedId || sending) return;
    const targetId = targetIdOverride ?? editingTarget;
    setSending(true);
    setError("");
    setInput("");
    setPendingUser(content);
    setDraft({ text: "", thinking: "", tools: [] });
    shouldAutoScroll.current = true;
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      if (targetId !== null && targetId !== undefined) {
        const targetIndex = apiMessages.findIndex((message) => message.id === targetId);
        if (targetIndex < 0) throw new Error("找不到要重发的消息");
        const after = targetIndex > 0 ? apiMessages[targetIndex - 1].id : 0;
        await truncateMessages(selectedId, after);
        setTurns(toTurns(apiMessages.slice(0, targetIndex)));
      }
      await streamChat(selectedId, content, handleEvent, controller.signal);
    } catch (cause) {
      if (!(cause instanceof DOMException && cause.name === "AbortError")) setError(errorMessage(cause, "聊天失败"));
    } finally {
      abortRef.current = null;
      setSending(false);
      setPendingUser("");
      setDraft({ text: "", thinking: "", tools: [] });
      setEditingTarget(null);
      await Promise.all([loadMessages(selectedId), loadConversations().catch(() => undefined)]);
    }
  };

  const stop = () => abortRef.current?.abort();
  const onSubmit = (event: FormEvent) => { event.preventDefault(); void send(); };
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey && preferences.enterToSend) { event.preventDefault(); void send(); }
    if (event.key === "Escape" && editingTarget !== null) { setEditingTarget(null); setInput(""); }
  };

  const editMessage = (turn: Extract<Turn, { kind: "user" }>) => {
    if (turn.messageId === undefined) return;
    setEditingTarget(turn.messageId);
    setInput(turn.text);
  };

  const visibleConversations = showArchived ? archivedConversations : conversations;
  const selected = useMemo(() => visibleConversations.find((item) => item.id === selectedId), [selectedId, visibleConversations]);
  const displayTurns = useMemo(() => {
    const result = [...turns] as Turn[];
    if (pendingUser) result.push({ kind: "user", text: pendingUser });
    if (sending && (draft.text || draft.thinking || draft.tools.length)) result.push({ kind: "assistant", text: draft.text, thinking: draft.thinking, tools: draft.tools });
    return result;
  }, [draft, pendingUser, sending, turns]);

  if (loadingConversations) return <div className="page-loading">正在连接助手…</div>;

  return (
    <div className="app-shell">
      <aside className={`sidebar ${sidebarOpen ? "mobile-open" : ""}`}>
        <Link className="brand brand-home" href="/" aria-label="返回主页"><div className="brand-mark">✦</div><div><div className="brand-title">个人 AI 助手</div><div className="brand-subtitle">Memory workspace</div></div></Link>
        <div className="sidebar-actions"><button className="primary-button full-width" onClick={() => void newConversation()}><Plus size={15} />新对话</button></div>
        <Link className="nav-link active" href="/"><MessageSquare size={15} />聊天</Link>
        <Link className="nav-link" href="/memories"><BookOpen size={15} />记忆管理</Link>
        <Link className="nav-link" href="/review"><CalendarDays size={15} />每日回顾</Link>
        <button className={`nav-link nav-button ${showArchived ? "active" : ""}`} onClick={() => void toggleArchivedView()}><Archive size={15} />{showArchived ? "返回会话" : "已归档"}</button>
        <Link className="nav-link" href="/settings"><Settings2 size={15} />设置</Link>
        <div className="section-label">Conversations</div>
        <div className="conversation-list">
          {visibleConversations.map((conversation) => (
            <div className={`conversation-item ${conversation.id === selectedId ? "selected" : ""}`} key={conversation.id} onClick={() => selectConversation(conversation.id)} role="button" tabIndex={0} onKeyDown={(event) => { if (event.key === "Enter") selectConversation(conversation.id); }}>
              <MessageSquare size={14} /><span className="conversation-title">{conversation.title}</span><span className="conversation-date">{dateLabel(conversation.updated_at)}</span>
              <button aria-label={showArchived ? `恢复${conversation.title}` : `归档${conversation.title}`} title={showArchived ? "恢复会话" : "归档会话"} className="icon-button" onClick={(event) => { event.stopPropagation(); void toggleArchived(conversation, !showArchived); }}>{showArchived ? <ArchiveRestore size={13} /> : <Archive size={13} />}</button>
              <button aria-label={`删除${conversation.title}`} className="icon-button" onClick={(event) => { event.stopPropagation(); void removeConversation(conversation); }}><Trash2 size={13} /></button>
            </div>
          ))}
        </div>
        <div className="sidebar-footer">后端：localhost:8000 · 长期记忆已启用</div>
      </aside>

      {sidebarOpen && <button className="sidebar-backdrop" aria-label="关闭导航" onClick={() => setSidebarOpen(false)} />}
      <main className="main-panel">
        <header className="topbar"><div className="topbar-left"><button className="icon-button mobile-menu" aria-label="打开导航" onClick={() => setSidebarOpen(true)}><Menu size={19} /></button><div><div className="topbar-title-row"><div className="topbar-title">{selected?.title ?? "新对话"}</div>{selected && <button className="icon-button topbar-edit" aria-label="修改会话标题" title="修改会话标题" onClick={() => void renameSelectedConversation()} disabled={renaming || sending}><Pencil size={13} /></button>}</div><div className="topbar-meta">{selected ? "与你的私人记忆相连" : ""}</div></div></div><div className="topbar-actions">{selected && <label className="thinking-control"><Brain size={14} /><span>思考</span><select aria-label="本会话思考模式" value={selected.thinking === null ? "default" : selected.thinking ? "on" : "off"} onChange={(event) => void changeThinking(event.target.value)} disabled={!runtimeSettings || !runtimeSettings.thinking_toggle || updatingThinking || sending}><option value="default">跟随默认{runtimeSettings ? `（${runtimeSettings.thinking_default ? "开" : "关"}）` : ""}</option><option value="on">始终开启</option><option value="off" disabled={runtimeSettings ? !runtimeSettings.thinking_toggle : true}>关闭思考</option></select></label>}<ThemeControl /><Bot size={18} color="var(--accent)" /></div></header>
        <div className="message-scroll" ref={scrollRef} onScroll={(event) => { const element = event.currentTarget; shouldAutoScroll.current = element.scrollHeight - element.scrollTop - element.clientHeight < 90; }}>
          {loadingMessages ? <div className="centered-empty">加载消息中…</div> : displayTurns.length === 0 ? <div className="welcome"><div className="eyebrow">Personal intelligence</div><h1>把想法交给<br />一个记得住的助手。</h1><p>聊天中的重要信息会被整理进长期记忆。你可以直接提问，也可以告诉我你的偏好、计划和正在做的事。</p><div className="suggestions"><button className="suggestion" onClick={() => setInput("帮我整理一下今天的工作计划")}>整理今天的工作计划</button><button className="suggestion" onClick={() => setInput("记住我喜欢简洁、直接的回答")}>记住一个偏好</button></div></div> : displayTurns.map((turn, index) => { const previous = index > 0 ? displayTurns[index - 1] : undefined; const previousUser = previous?.kind === "user" ? previous : undefined; return <TurnView turn={turn} showThinking={preferences.showThinking} showToolActivity={preferences.showToolActivity} showUsage={preferences.showUsage} highlighted={turn.messageId === highlightedMessageId} turnRef={(node) => { if (turn.messageId !== undefined) { if (node) messageRefs.current.set(turn.messageId, node); else messageRefs.current.delete(turn.messageId); } }} onEdit={turn.kind === "user" && !sending ? () => editMessage(turn) : undefined} onRegenerate={turn.kind === "assistant" && !sending && previousUser?.messageId !== undefined ? () => void send(previousUser.text, previousUser.messageId) : undefined} streaming={sending && index === displayTurns.length - 1 && turn.kind === "assistant"} key={`${turn.kind}-${index}`} />; })}
        </div>
        <div className="composer-wrap">
          {error && <div className="error-banner">{error}</div>}
          <form className="composer" onSubmit={onSubmit}><textarea value={input} onChange={(event) => setInput(event.target.value)} onKeyDown={onKeyDown} placeholder={editingTarget !== null ? "编辑这条消息后重新发送…" : "写下你的问题或想让我记住的事…"} disabled={sending} /><div className="composer-bottom"><span className="composer-hint">{editingTarget !== null ? "编辑重发 · Esc 取消" : "Enter 发送 · Shift + Enter 换行"}</span>{sending ? <button type="button" className="ghost-button stop-button" onClick={stop}><Square size={13} />停止</button> : <button type="submit" className="primary-button" disabled={!input.trim()}><Send size={14} />{editingTarget !== null ? "重发" : "发送"}</button>}</div></form>
        </div>
      </main>
    </div>
  );
}
