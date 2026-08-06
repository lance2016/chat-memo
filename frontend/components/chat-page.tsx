"use client";

import { FormEvent, KeyboardEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { Archive, ArchiveRestore, ChevronDown, ListChecks, LoaderCircle, Menu, MessageSquare, Pencil, Plus, RefreshCw, Send, Square, Trash2, TriangleAlert, Volume2 } from "lucide-react";
import { apiBaseLabel, apiUrl, archiveConversation, createConversation, deleteConversation, errorMessage, getNextSpeech, getTtsStatus, listConversations, listMessages, prepareSpeech, stopSpeech, streamChat, truncateMessages, updateConversation } from "@/lib/api";
import { defaultPreferences, preferencesChangeEvent, readPreferences, type UserPreferences } from "@/lib/preferences";
import { toTurns, toolLabel } from "@/lib/turns";
import type { ChatEvent, Conversation, ToolActivity, Turn, TtsStatus } from "@/lib/types";
import { Markdown } from "@/components/markdown";
import { SearchTrigger } from "@/components/global-search";
import { ThemeControl } from "@/components/theme-control";
import { WorkspaceNav } from "@/components/workspace-topbar";
import { LatestRequest } from "@/lib/latest-request";
import { ConfirmDialog } from "@/components/confirm-dialog";

interface LiveTool extends ToolActivity { status: "running" | "done"; }

function dateLabel(value: string) {
  return new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric" }).format(new Date(value));
}

function displayTool(tool: LiveTool | ToolActivity) {
  const running = "status" in tool && tool.status === "running";
  return (
    <div className={`tool-activity ${running ? "running" : tool.ok ? "" : "failed"}`} key={`${tool.name}-${tool.summary}-${JSON.stringify(tool.input)}`}>
      <span className="tool-activity-icon" aria-hidden="true">{running ? <LoaderCircle size={13} className="spin" /> : tool.ok ? "✓" : "!"}</span>
      <span className="tool-activity-label">{toolLabel(tool)}</span>
      <span className="tool-activity-state">{running ? "处理中" : tool.ok ? "已完成" : "需注意"}</span>
      {!running && tool.summary && <span className="tool-summary" title={tool.summary}>{tool.summary}</span>}
    </div>
  );
}

function ToolActivityGroup({ tools }: { tools: (LiveTool | ToolActivity)[] }) {
  const runningCount = tools.filter((tool) => "status" in tool && tool.status === "running").length;
  const failedCount = tools.filter((tool) => !("status" in tool && tool.status === "running") && !tool.ok).length;
  const label = runningCount ? `正在处理 ${tools.length} 项记忆操作` : `记忆操作 ${tools.length} 次`;
  const state = runningCount ? `${runningCount} 项进行中` : failedCount ? `${failedCount} 项需注意` : "已完成";

  return <details className={`tool-group ${failedCount ? "has-failure" : ""}`} open={runningCount > 0}>
    <summary><span className="tool-group-icon"><ListChecks size={13} /></span><span className="tool-group-label">{label}</span><span className="tool-group-state">{state}</span><ChevronDown size={13} className="tool-group-chevron" /></summary>
    <div className="tool-group-list">{tools.map((tool) => displayTool(tool))}</div>
  </details>;
}

function usageLabel(usage: NonNullable<Extract<Turn, { kind: "assistant" }>["usage"]>) {
  const output = usage.completion_tokens ?? usage.output_tokens;
  return typeof output === "number" ? `${output.toLocaleString()} output tokens` : "";
}

function TurnView({ turn, streaming = false, highlighted = false, showThinking = true, showToolActivity = true, showUsage = true, ttsLoading = false, ttsPlaying = false, ttsAvailable = false, ttsDisabledReason = "语音服务状态未知", turnRef, onEdit, onRegenerate, onSpeak }: { turn: Turn; streaming?: boolean; highlighted?: boolean; showThinking?: boolean; showToolActivity?: boolean; showUsage?: boolean; ttsLoading?: boolean; ttsPlaying?: boolean; ttsAvailable?: boolean; ttsDisabledReason?: string; turnRef?: (node: HTMLDivElement | null) => void; onEdit?: () => void; onRegenerate?: () => void; onSpeak?: () => void }) {
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
      {showToolActivity && turn.tools.length > 0 && <ToolActivityGroup tools={turn.tools} />}
      {turn.text && <div className="assistant-content"><Markdown highlightCode={!streaming}>{turn.text}</Markdown></div>}
      {turn.usage?.interrupted && <div className="interrupted-answer"><TriangleAlert size={13} /><span>回答被中断，已保留已生成的内容</span></div>}
      {showUsage && !turn.usage?.interrupted && turn.usage && usageLabel(turn.usage) && <div className="message-usage">{usageLabel(turn.usage)}</div>}
      {(onSpeak || onRegenerate) && <div className="turn-actions assistant-actions">
        {onSpeak && <button className={`tts-button ${ttsPlaying ? "playing" : ""} ${ttsLoading ? "loading" : ""}`} onClick={onSpeak} disabled={ttsLoading || (!ttsAvailable && !ttsPlaying)} title={ttsAvailable || ttsPlaying ? (ttsPlaying ? "停止播放" : "播放这条回答") : ttsDisabledReason} aria-label={ttsAvailable || ttsPlaying ? (ttsPlaying ? "停止播放" : "播放这条回答") : ttsDisabledReason}>
          {ttsLoading ? <LoaderCircle size={12} className="spin" /> : <Volume2 size={12} />}{ttsLoading ? "合成中…" : ttsPlaying ? "停止播放" : "播放语音"}
        </button>}
        {onRegenerate && <button onClick={onRegenerate}><RefreshCw size={12} />重新生成</button>}
      </div>}
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
  const [renaming, setRenaming] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<Conversation | null>(null);
  const [deletingConversation, setDeletingConversation] = useState(false);
  const [preferences, setPreferences] = useState<UserPreferences>(defaultPreferences);
  const [ttsStatus, setTtsStatus] = useState<TtsStatus | null>(null);
  const [ttsLoadingId, setTtsLoadingId] = useState<number | null>(null);
  const [ttsPlayingId, setTtsPlayingId] = useState<number | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const audioRef = useRef<HTMLAudioElement>(null);
  const speechBusyRef = useRef(false);
  const speechQueueRef = useRef<string[]>([]);
  const speechCursorRef = useRef(0);
  const speechPumpRef = useRef<Promise<void>>(Promise.resolve());
  const speechPumpTimerRef = useRef<number | null>(null);
  const speechGenerationRef = useRef(0);
  const speechAutoActiveRef = useRef(false);
  const speechFlushCompleteRef = useRef(false);
  const speechAudioPlayingRef = useRef(false);
  const speechAutoMessageIdRef = useRef<number | null>(null);
  const draftTextRef = useRef("");
  const draftMessageIdRef = useRef<number | null>(null);
  const streamDoneRef = useRef(false);
  const messageRefs = useRef(new Map<number, HTMLDivElement>());
  const messageRequestsRef = useRef(new LatestRequest());
  const shouldAutoScroll = useRef(true);
  const [highlightedMessageId, setHighlightedMessageId] = useState<number | null>(null);

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
    let active = true;
    void getTtsStatus()
      .then((status) => { if (active) setTtsStatus(status); })
      .catch(() => undefined);
    return () => { active = false; };
  }, []);

  useEffect(() => () => {
    messageRequestsRef.current.invalidate();
    speechGenerationRef.current += 1;
    speechAutoActiveRef.current = false;
    speechFlushCompleteRef.current = false;
    speechQueueRef.current = [];
    if (speechPumpTimerRef.current !== null) window.clearTimeout(speechPumpTimerRef.current);
    audioRef.current?.pause();
    void stopSpeech().catch(() => undefined);
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
    const request = messageRequestsRef.current.begin();
    setLoadingMessages(true);
    setError("");
    try {
      const messages = await listMessages(id);
      if (!messageRequestsRef.current.isCurrent(request)) return;
      messageRefs.current.clear();
      setHighlightedMessageId(null);
      setApiMessages(messages);
      setTurns(toTurns(messages));
    } catch (cause) {
      if (!messageRequestsRef.current.isCurrent(request)) return;
      setError(errorMessage(cause, "无法加载消息"));
    } finally {
      if (messageRequestsRef.current.isCurrent(request)) setLoadingMessages(false);
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

  useEffect(() => {
    const element = composerRef.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${element.scrollHeight}px`;
  }, [input]);

  const selectConversation = (id: number) => {
    if (sending) return;
    stopTtsPlayback();
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

  const removeConversation = async () => {
    const conversation = deleteTarget;
    if (!conversation || deletingConversation) return;
    setDeletingConversation(true);
    setError("");
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
      setDeleteTarget(null);
    } catch (cause) {
      setError(errorMessage(cause, "无法删除会话"));
    } finally {
      setDeletingConversation(false);
    }
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
      audio.pause();
      audio.removeAttribute("src");
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

  const handleEvent = (event: ChatEvent) => {
    if (event.type === "thinking_delta") setDraft((current) => ({ ...current, thinking: current.thinking + event.text }));
    if (event.type === "text_delta") {
      draftTextRef.current += event.text;
      setDraft((current) => ({ ...current, text: current.text + event.text }));
      scheduleAutoSpeech();
    }
    if (event.type === "message_id") {
      draftMessageIdRef.current = event.message_id;
      speechAutoMessageIdRef.current = event.message_id;
      if (speechAudioPlayingRef.current) setTtsPlayingId(event.message_id);
    }
    if (event.type === "done") streamDoneRef.current = true;
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
    if (event.type === "error") {
      streamDoneRef.current = false;
      setError(event.message);
    }
  };

  const ttsAvailable = Boolean(ttsStatus && ttsStatus.mode !== "off" && ttsStatus.enabled);
  const ttsDisabledReason = !ttsStatus
    ? "正在检查语音服务"
    : !ttsStatus.enabled
      ? "语音播放未启用"
      : "语音播放已关闭";

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
    if (speechBusyRef.current) return;

    speechBusyRef.current = true;
    resetLocalSpeech();
    setTtsLoadingId(messageId ?? null);
    setError("");
    try {
      await stopSpeech().catch(() => undefined);
      const prepared = await prepareSpeech({ text });
      audio.src = apiUrl(prepared.url);
      audio.currentTime = 0;
      audio.load();
      speechAudioPlayingRef.current = true;
      await audio.play();
      setTtsPlayingId(messageId ?? null);
    } catch (cause) {
      speechAudioPlayingRef.current = false;
      setTtsPlayingId(null);
      setError(cause instanceof DOMException && cause.name === "NotAllowedError" ? "浏览器阻止了自动播放，请点击消息旁的播放按钮" : errorMessage(cause, "语音播放失败"));
    } finally {
      speechBusyRef.current = false;
      setTtsLoadingId(null);
    }
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
    draftTextRef.current = "";
    draftMessageIdRef.current = null;
    streamDoneRef.current = false;
    shouldAutoScroll.current = true;
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      if (ttsStatus?.mode === "auto") await startAutoSpeech();
      else stopTtsPlayback();
      if (targetId !== null && targetId !== undefined) {
        const targetIndex = apiMessages.findIndex((message) => message.id === targetId);
        if (targetIndex < 0) throw new Error("找不到要重发的消息");
        const after = targetIndex > 0 ? apiMessages[targetIndex - 1].id : 0;
        await truncateMessages(selectedId, after);
        setTurns(toTurns(apiMessages.slice(0, targetIndex)));
      }
      await streamChat(selectedId, content, handleEvent, controller.signal);
      if (ttsStatus?.mode === "auto" && streamDoneRef.current && draftTextRef.current.trim()) {
        await flushAutoSpeech();
      }
    } catch (cause) {
      if (!streamDoneRef.current) stopTtsPlayback();
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

  const stop = () => {
    abortRef.current?.abort();
    stopTtsPlayback();
  };
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
        <WorkspaceNav active="chat" className="sidebar-workspace-nav" />
        <button className={`nav-link nav-button ${showArchived ? "active" : ""}`} onClick={() => void toggleArchivedView()}><Archive size={15} />{showArchived ? "返回会话" : "已归档"}</button>
        <div className="section-label">Conversations</div>
        <div className="conversation-list">
          {visibleConversations.map((conversation) => (
            <div className={`conversation-item ${conversation.id === selectedId ? "selected" : ""}`} key={conversation.id}>
              <button className="conversation-open" type="button" onClick={() => selectConversation(conversation.id)} aria-current={conversation.id === selectedId ? "true" : undefined}>
                <MessageSquare size={14} /><span className="conversation-title">{conversation.title}</span><span className="conversation-date">{dateLabel(conversation.updated_at)}</span>
              </button>
              <button aria-label={showArchived ? `恢复${conversation.title}` : `归档${conversation.title}`} title={showArchived ? "恢复会话" : "归档会话"} className="icon-button" onClick={(event) => { event.stopPropagation(); void toggleArchived(conversation, !showArchived); }}>{showArchived ? <ArchiveRestore size={13} /> : <Archive size={13} />}</button>
              <button aria-label={`删除${conversation.title}`} className="icon-button" onClick={(event) => { event.stopPropagation(); setDeleteTarget(conversation); }}><Trash2 size={13} /></button>
            </div>
          ))}
        </div>
        <div className="sidebar-footer">后端：{apiBaseLabel()} · 长期记忆已启用</div>
      </aside>

      {sidebarOpen && <button className="sidebar-backdrop" aria-label="关闭导航" onClick={() => setSidebarOpen(false)} />}
      <main className="main-panel">
        <header className="topbar"><div className="topbar-left"><button className="icon-button mobile-menu" aria-label="打开导航" onClick={() => setSidebarOpen(true)}><Menu size={19} /></button><div><div className="topbar-title-row"><div className="topbar-title">{selected?.title ?? "新对话"}</div>{selected && <button className="icon-button topbar-edit" aria-label="修改会话标题" title="修改会话标题" onClick={() => void renameSelectedConversation()} disabled={renaming || sending}><Pencil size={13} /></button>}</div><div className="topbar-meta">{selected ? "与你的私人记忆相连" : ""}</div></div></div><div className="topbar-actions"><WorkspaceNav active="chat" className="chat-workspace-nav" /><SearchTrigger /><ThemeControl /></div></header>
        <div className="message-scroll" ref={scrollRef} onScroll={(event) => { const element = event.currentTarget; shouldAutoScroll.current = element.scrollHeight - element.scrollTop - element.clientHeight < 90; }}>
          {loadingMessages ? <div className="centered-empty">加载消息中…</div> : displayTurns.length === 0 ? <div className="welcome"><div className="eyebrow">Personal intelligence</div><h1>把想法交给<br />一个记得住的助手。</h1><p>聊天中的重要信息会被整理进长期记忆。你可以直接提问，也可以告诉我你的偏好、计划和正在做的事。</p><div className="suggestions"><button className="suggestion" onClick={() => setInput("帮我整理一下今天的工作计划")}>整理今天的工作计划</button><button className="suggestion" onClick={() => setInput("记住我喜欢简洁、直接的回答")}>记住一个偏好</button></div></div> : displayTurns.map((turn, index) => { const previous = index > 0 ? displayTurns[index - 1] : undefined; const previousUser = previous?.kind === "user" ? previous : undefined; const isAssistant = turn.kind === "assistant"; const hasSpeechButton = isAssistant && turn.messageId !== undefined && ttsStatus?.mode !== undefined && ttsStatus.mode !== "off"; return <TurnView turn={turn} showThinking={preferences.showThinking} showToolActivity={preferences.showToolActivity} showUsage={preferences.showUsage} highlighted={turn.messageId === highlightedMessageId} ttsAvailable={ttsAvailable} ttsDisabledReason={ttsDisabledReason} ttsLoading={isAssistant && turn.messageId !== undefined && ttsLoadingId === turn.messageId} ttsPlaying={isAssistant && turn.messageId !== undefined && ttsPlayingId === turn.messageId} turnRef={(node) => { if (turn.messageId !== undefined) { if (node) messageRefs.current.set(turn.messageId, node); else messageRefs.current.delete(turn.messageId); } }} onEdit={turn.kind === "user" && !sending ? () => editMessage(turn) : undefined} onRegenerate={turn.kind === "assistant" && !sending && previousUser?.messageId !== undefined ? () => void send(previousUser.text, previousUser.messageId) : undefined} onSpeak={hasSpeechButton ? () => void speakText(turn.text, turn.messageId) : undefined} streaming={sending && index === displayTurns.length - 1 && turn.kind === "assistant"} key={`${turn.kind}-${index}`} />; })}
        </div>
        <div className="composer-wrap">
          {error && <div className="error-banner">{error}</div>}
          <form className="composer" onSubmit={onSubmit}><textarea ref={composerRef} rows={1} value={input} onChange={(event) => setInput(event.target.value)} onKeyDown={onKeyDown} placeholder={editingTarget !== null ? "编辑这条消息后重新发送…" : "写下你的问题或想让我记住的事…"} disabled={sending} /><div className="composer-bottom"><span className="composer-hint">{editingTarget !== null ? "编辑重发 · Esc 取消" : "Enter 发送 · Shift + Enter 换行"}</span>{sending ? <button type="button" className="ghost-button stop-button" onClick={stop}><Square size={13} />停止</button> : <button type="submit" className="primary-button" disabled={!input.trim()}><Send size={14} />{editingTarget !== null ? "重发" : "发送"}</button>}</div></form>
        </div>
        <audio ref={audioRef} className="tts-audio" onEnded={handleSpeechEnded} onError={handleSpeechError} />
      </main>
      <ConfirmDialog
        open={deleteTarget !== null}
        title="删除这段会话？"
        description="会话中的全部消息和关联记录都会被永久删除。这个操作无法撤销。"
        subject={deleteTarget?.title}
        warning={selectedId === deleteTarget?.id ? "删除当前会话后，将自动切换到下一段会话。" : undefined}
        confirmLabel="永久删除会话"
        busy={deletingConversation}
        onCancel={() => setDeleteTarget(null)}
        onConfirm={() => void removeConversation()}
      />
    </div>
  );
}
