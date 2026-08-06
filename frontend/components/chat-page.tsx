"use client";

import { FormEvent, KeyboardEvent, RefObject, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { Archive, ArchiveRestore, ArrowRight, ChevronDown, ListChecks, LoaderCircle, Pencil, RefreshCw, Send, Sparkles, Square, Trash2, TriangleAlert, Volume2 } from "lucide-react";
import { apiUrl, archiveConversation, createConversation, deleteConversation, errorMessage, getMemoryStats, getNextSpeech, getTtsStatus, listConversations, listMessages, prepareSpeech, stopSpeech, streamChat, truncateMessages, updateConversation } from "@/lib/api";
import { defaultPreferences, preferencesChangeEvent, readPreferences, type UserPreferences } from "@/lib/preferences";
import { toTurns, toolLabel } from "@/lib/turns";
import type { ChatEvent, Conversation, ToolActivity, Turn, TtsStatus } from "@/lib/types";
import { Markdown } from "@/components/markdown";
import { MemoryMark, notifyWorkspaceConversationsChanged, WorkspacePageFallback } from "@/components/workspace-topbar";
import { LatestRequest } from "@/lib/latest-request";
import { resetMediaElement } from "@/lib/media-playback";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { InputDialog } from "@/components/input-dialog";
import { VoiceInputButton } from "@/components/voice-input-button";

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
  const knowledgeBaseCount = tools.filter((tool) => tool.name.startsWith("kb_")).length;
  const groupName = knowledgeBaseCount === tools.length ? "知识库查询" : knowledgeBaseCount > 0 ? "工具操作" : "记忆操作";
  const label = runningCount ? `正在处理 ${tools.length} 项${groupName}` : `${groupName} ${tools.length} 次`;
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

function homeDateLabel() {
  return new Intl.DateTimeFormat("zh-CN", { weekday: "long", month: "long", day: "numeric" }).format(new Date());
}

function greeting() {
  const hour = new Date().getHours();
  if (hour < 6) return "夜深了";
  if (hour < 11) return "早上好";
  if (hour < 14) return "中午好";
  if (hour < 18) return "下午好";
  return "晚上好";
}

function HomeDashboard({ conversations, input, memoryCount, sending, composerRef, onInput, onTranscription, onKeyDown, onSubmit, onOpenConversation }: {
  conversations: Conversation[];
  input: string;
  memoryCount: number | null;
  sending: boolean;
  composerRef: RefObject<HTMLTextAreaElement | null>;
  onInput: (value: string) => void;
  onTranscription: (value: string) => void;
  onKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => void;
  onSubmit: (event: FormEvent) => void;
  onOpenConversation: (id: number) => void;
}) {
  const recent = conversations.slice(0, 3);
  return <div className="memory-home-scroll">
    <div className="memory-home">
      <section className="memory-home-hero">
        <div className="memory-home-copy">
          <p className="memory-date"><i /><i /><i />{homeDateLabel()}</p>
          <h1>{greeting()}，Lance。<br /><em>今天想留下什么？</em></h1>
          <p>我会替你保存重要的想法、偏好和约定，在需要时帮你重新想起。</p>
        </div>
        <div className="memory-orbit" aria-hidden="true">
          <i className="orbit-ring" /><i className="orbit-ring orbit-ring-wide" />
          <span className="orbit-core"><MemoryMark compact /></span>
          <i className="orbit-dot orbit-dot-mint" /><i className="orbit-dot orbit-dot-orange" /><i className="orbit-dot orbit-dot-blue" />
          <span className="orbit-note"><Sparkles size={12} /><b>{memoryCount ?? "—"}</b> 条记忆</span>
        </div>
      </section>

      <form className="home-capture" onSubmit={onSubmit}>
        <div className="home-capture-main"><span><Sparkles size={17} /></span><textarea ref={composerRef} rows={2} value={input} onChange={(event) => onInput(event.target.value)} onKeyDown={onKeyDown} placeholder="和我聊聊，或告诉我一件想记住的事……" disabled={sending} /></div>
        <div className="home-capture-foot"><div className="home-pills"><button type="button" onClick={() => onInput("帮我整理今天的想法")}>整理今天的想法</button><button type="button" onClick={() => onInput("回顾一下最近的计划")}>回顾最近的计划</button><button type="button" onClick={() => onInput("记住一个新的偏好：")}>记住一个偏好</button><span className="home-capture-hint">Enter 发送 · Shift + Enter 换行</span></div><div className="home-capture-actions"><VoiceInputButton disabled={sending} onTranscript={onTranscription} /><button className="home-send" type="submit" disabled={!input.trim() || sending} aria-label="发送"><Send size={16} /></button></div></div>
      </form>

      <section className="memory-home-grid">
        <div className="home-panel">
          <div className="home-panel-heading"><div><span>RECENT CONVERSATIONS</span><h2>最近聊过的事情</h2></div><button type="button" onClick={() => conversations[0] && onOpenConversation(conversations[0].id)}>继续最近对话 <ArrowRight size={13} /></button></div>
          <div className="home-stream">
            {recent.length ? recent.map((conversation, index) => <button className="home-stream-item" type="button" onClick={() => onOpenConversation(conversation.id)} key={conversation.id}><i className={`home-stream-pin tone-${index + 1}`}><span /></i><span><time>{dateLabel(conversation.updated_at)}</time><strong>{conversation.title}</strong><small>打开这段对话，继续沿着当时的想法往下聊。</small><em>{index === 0 ? "最近" : "对话"}</em></span><ArrowRight size={15} /></button>) : <div className="home-stream-empty">还没有对话，从上方写下第一件想记住的事吧。</div>}
          </div>
        </div>
        <aside className="home-rail">
          <Link className="home-review-card" href="/review"><span className="review-scene"><i className="review-sun" /><i className="review-hill back" /><i className="review-hill front" /></span><span className="home-review-copy"><small>TODAY&apos;S REVIEW</small><strong>回看今天<br />留下的脉络</strong><em>开始今日回顾 <ArrowRight size={13} /></em></span></Link>
        </aside>
      </section>
    </div>
  </div>;
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
      {(turn.text || streaming) && <div className="assistant-content"><Markdown highlightCode={!streaming}>{turn.text}</Markdown>{streaming && <span className="streaming-cursor" />}</div>}
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
  const [showArchived, setShowArchived] = useState(false);
  const [editingTarget, setEditingTarget] = useState<number | null>(null);
  const [renaming, setRenaming] = useState(false);
  const [renameDialogOpen, setRenameDialogOpen] = useState(false);
  const [renameDraft, setRenameDraft] = useState("");
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
  const draftMessageIdRef = useRef<number | null>(null);
  const streamDoneRef = useRef(false);
  const messageRefs = useRef(new Map<number, HTMLDivElement>());
  const messageRequestsRef = useRef(new LatestRequest());
  const shouldAutoScroll = useRef(true);
  const [highlightedMessageId, setHighlightedMessageId] = useState<number | null>(null);
  const [memoryCount, setMemoryCount] = useState<number | null>(null);

  useEffect(() => {
    void getMemoryStats(30, 1).then((stats) => setMemoryCount(stats.total_memories)).catch(() => undefined);
  }, []);

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
    if (audioRef.current) resetMediaElement(audioRef.current);
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
        if (validUrlId) {
          setSelectedId(validUrlId);
          if (validUrlId !== fromUrl) router.replace(`/?conversation=${validUrlId}`);
        } else {
          setSelectedId(null);
          setTurns([]);
          if (Number.isFinite(fromUrl) && fromUrl > 0) router.replace("/");
        }
      })
      .catch((cause: unknown) => setError(errorMessage(cause, "无法连接后端")))
      .finally(() => setLoadingConversations(false));
  }, [loadConversations, router, selectedFromUrl]);

  // silent：刚流完一轮时用。此时屏幕上已经有完整的回答（draft），再挂一次
  // 「加载消息中…」会把整个列表替换掉一瞬再换回来，看着就像正文是一次性蹦出来的。
  const loadMessages = useCallback(async (id: number, silent = false) => {
    const request = messageRequestsRef.current.begin();
    if (!silent) setLoadingMessages(true);
    setError("");
    try {
      const messages = await listMessages(id);
      if (!messageRequestsRef.current.isCurrent(request)) return;
      messageRefs.current.clear();
      setHighlightedMessageId(null);
      setApiMessages(messages);
      setTurns(toTurns(messages));
      // 和 setTurns 同一批状态更新里清掉临时态，换帧才是原子的：
      // 分开清会先渲染出「权威历史 + 尚未清掉的 draft」这一帧，也就是重影。
      setPendingUser("");
      setDraft({ text: "", thinking: "", tools: [] });
    } catch (cause) {
      if (!messageRequestsRef.current.isCurrent(request)) return;
      setError(errorMessage(cause, "无法加载消息"));
    } finally {
      if (messageRequestsRef.current.isCurrent(request)) setLoadingMessages(false);
    }
  }, []);

  useEffect(() => {
    if (!selectedId) return;
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
    setEditingTarget(null);
    setInput("");
    router.push(`/?conversation=${id}`);
  };

  const toggleArchived = async (conversation: Conversation, archived: boolean) => {
    try {
      const updated = await archiveConversation(conversation.id, archived);
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
        setConversations((current) => [updated, ...current.filter((item) => item.id !== updated.id)]);
        setShowArchived(false);
      }
      notifyWorkspaceConversationsChanged();
    } catch (cause) {
      setError(errorMessage(cause, archived ? "归档失败" : "恢复会话失败"));
    }
  };

  const renameSelectedConversation = async () => {
    if (!selected || renaming || sending) return;
    setRenameDraft(selected.title);
    setRenameDialogOpen(true);
  };

  const confirmRename = async () => {
    if (!selected || renaming) return;
    const nextTitle = renameDraft.trim();
    if (!nextTitle || nextTitle === selected.title) { setRenameDialogOpen(false); return; }
    setRenaming(true);
    setError("");
    try {
      const updated = await updateConversation(selected.id, { title: nextTitle });
      setConversations((current) => current.map((item) => item.id === updated.id ? updated : item));
      setArchivedConversations((current) => current.map((item) => item.id === updated.id ? updated : item));
      setRenameDialogOpen(false);
      notifyWorkspaceConversationsChanged();
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
      notifyWorkspaceConversationsChanged();
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
    if (event.type === "title") {
      setConversations((current) => current.map((item) => item.id === selectedId ? { ...item, title: event.title } : item));
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

  const send = async (contentOverride?: string, targetIdOverride?: number, conversationIdOverride?: number) => {
    const content = (contentOverride ?? input).trim();
    const conversationId = conversationIdOverride ?? selectedId;
    if (!content || !conversationId || sending) return;
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
        await truncateMessages(conversationId, after);
        setTurns(toTurns(apiMessages.slice(0, targetIndex)));
      }
      await streamChat(conversationId, content, handleEvent, controller.signal);
      if (ttsStatus?.mode === "auto" && streamDoneRef.current && draftTextRef.current.trim()) {
        await flushAutoSpeech();
      }
    } catch (cause) {
      if (!streamDoneRef.current) stopTtsPlayback();
      if (!(cause instanceof DOMException && cause.name === "AbortError")) setError(errorMessage(cause, "聊天失败"));
    } finally {
      abortRef.current = null;
      setSending(false);
      setEditingTarget(null);
      // 临时态由 loadMessages 在换上权威历史的同一帧里清掉，这里不要提前清 ——
      // 提前清会让刚说完的回答先消失，等 fetch 回来再出现。
      await Promise.all([
        loadMessages(conversationId, true),
        loadConversations().catch(() => undefined),
      ]);
    }
  };

  const stop = () => {
    abortRef.current?.abort();
    stopTtsPlayback();
  };
  const startHomeConversation = async (event: FormEvent) => {
    event.preventDefault();
    const content = input.trim();
    if (!content || sending) return;
    try {
      const created = await createConversation();
      setConversations((current) => [created, ...current]);
      notifyWorkspaceConversationsChanged();
      skipNextMessageLoadRef.current = created.id;
      setSelectedId(created.id);
      setTurns([]);
      router.replace(`/?conversation=${created.id}`);
      void send(content, undefined, created.id);
    } catch (cause) {
      setError(errorMessage(cause, "无法创建会话"));
    }
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
    // sending 期间无条件给出助手气泡：首个 token 之前它只有一个闪烁光标，
    // 否则从发送到首字之间界面上没有任何「在回答」的迹象，看着像卡死。
    // 流结束后不看 sending，让 draft 一直留在屏幕上，直到 loadMessages
    // 把权威历史换进来（见 loadMessages）—— 中间不能有空档，否则正文会闪一下。
    if (sending || draft.text || draft.thinking || draft.tools.length) {
      result.push({ kind: "assistant", text: draft.text, thinking: draft.thinking, tools: draft.tools });
    }
    return result;
  }, [draft, pendingUser, sending, turns]);

  if (loadingConversations) return <WorkspacePageFallback active="chat" message="正在连接助手…" />;

  return (
    <div className="app-shell">
      <main className="main-panel">
        {selectedId === null ? <HomeDashboard conversations={conversations} input={input} memoryCount={memoryCount} sending={sending} composerRef={composerRef} onInput={setInput} onTranscription={appendTranscription} onKeyDown={onKeyDown} onSubmit={startHomeConversation} onOpenConversation={selectConversation} /> : <>
          <div className="chat-conversation-toolbar">
            <div className="chat-conversation-title"><span>{showArchived ? "已归档对话" : "当前对话"}</span><strong>{selected?.title ?? "正在打开对话…"}</strong></div>
            {selected && <div className="chat-conversation-actions">
              <button type="button" className="icon-button" aria-label="修改会话标题" title="修改会话标题" onClick={() => void renameSelectedConversation()} disabled={renaming || sending}><Pencil size={14} /></button>
              <button type="button" className="icon-button" aria-label={showArchived ? "恢复会话" : "归档会话"} title={showArchived ? "恢复会话" : "归档会话"} onClick={() => void toggleArchived(selected, !showArchived)} disabled={sending}>{showArchived ? <ArchiveRestore size={14} /> : <Archive size={14} />}</button>
              <button type="button" className="icon-button chat-delete-button" aria-label="删除会话" title="删除会话" onClick={() => setDeleteTarget(selected)} disabled={sending}><Trash2 size={14} /></button>
            </div>}
          </div>
          <div className="message-scroll" ref={scrollRef} onScroll={(event) => { const element = event.currentTarget; shouldAutoScroll.current = element.scrollHeight - element.scrollTop - element.clientHeight < 90; }}>
            {loadingMessages && !pendingUser && !sending ? <div className="centered-empty">加载消息中…</div> : displayTurns.map((turn, index) => { const previous = index > 0 ? displayTurns[index - 1] : undefined; const previousUser = previous?.kind === "user" ? previous : undefined; const isAssistant = turn.kind === "assistant"; const hasSpeechButton = isAssistant && turn.messageId !== undefined && ttsStatus?.mode !== undefined && ttsStatus.mode !== "off"; return <TurnView turn={turn} showThinking={preferences.showThinking} showToolActivity={preferences.showToolActivity} showUsage={preferences.showUsage} highlighted={turn.messageId === highlightedMessageId} ttsAvailable={ttsAvailable} ttsDisabledReason={ttsDisabledReason} ttsLoading={isAssistant && turn.messageId !== undefined && ttsLoadingId === turn.messageId} ttsPlaying={isAssistant && turn.messageId !== undefined && ttsPlayingId === turn.messageId} turnRef={(node) => { if (turn.messageId !== undefined) { if (node) messageRefs.current.set(turn.messageId, node); else messageRefs.current.delete(turn.messageId); } }} onEdit={turn.kind === "user" && !sending ? () => editMessage(turn) : undefined} onRegenerate={turn.kind === "assistant" && !sending && previousUser?.messageId !== undefined ? () => void send(previousUser.text, previousUser.messageId) : undefined} onSpeak={hasSpeechButton ? () => void speakText(turn.text, turn.messageId) : undefined} streaming={sending && index === displayTurns.length - 1 && turn.kind === "assistant"} key={`${turn.kind}-${index}`} />; })}
          </div>
          <div className="composer-wrap">{error && <div className="error-banner">{error}</div>}<form className="composer" onSubmit={onSubmit}><textarea ref={composerRef} rows={1} value={input} onChange={(event) => setInput(event.target.value)} onKeyDown={onKeyDown} placeholder={editingTarget !== null ? "编辑这条消息后重新发送…" : "写下你的问题或想让我记住的事…"} disabled={sending} /><div className="composer-bottom"><span className="composer-hint">{editingTarget !== null ? "编辑重发 · Esc 取消" : "Enter 发送 · Shift + Enter 换行"}</span><VoiceInputButton disabled={sending} onTranscript={appendTranscription} />{sending ? <button type="button" className="ghost-button stop-button" onClick={stop}><Square size={13} />停止</button> : <button type="submit" className="primary-button" disabled={!input.trim()}>{editingTarget !== null ? "重发" : "发送"}</button>}</div></form></div>
        </>}
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
      <InputDialog open={renameDialogOpen} title="修改会话标题" description="给这段对话一个更容易记住的名字。标题只会影响当前会话。" value={renameDraft} onChange={setRenameDraft} onCancel={() => setRenameDialogOpen(false)} onConfirm={() => void confirmRename()} busy={renaming} />
    </div>
  );
}
