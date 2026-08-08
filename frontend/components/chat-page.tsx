"use client";

import { FormEvent, KeyboardEvent, RefObject, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { ArrowDown, ArrowRight, CalendarClock, CalendarDays, Check, ChevronDown, Copy, ExternalLink, Gauge, ListChecks, LoaderCircle, Pencil, RefreshCw, Send, Sparkles, Square, TriangleAlert, Volume2 } from "lucide-react";
import { apiUrl, errorMessage, getConversationContext, getMemoryStats, getModelCatalog, getNextSpeech, getTtsStatus, listConversations, listMessages, prepareSpeech, stopSpeech, streamChat, truncateMessages } from "@/lib/api";
import { defaultPreferences, preferencesChangeEvent, readPreferences, type UserPreferences } from "@/lib/preferences";
import { toTurns, toolLabel } from "@/lib/turns";
import type { ChatEvent, Conversation, ConversationContext, ModelCatalog, ToolActivity, Turn, TtsStatus } from "@/lib/types";
import { Markdown } from "@/components/markdown";
import { conversationsChangedEvent, notifyWorkspaceConversationsChanged, notifyWorkspaceSelectedConversationChanged, type WorkspaceConversationChange, WorkspacePageFallback } from "@/components/workspace-topbar";
import { LatestRequest } from "@/lib/latest-request";
import { resetMediaElement } from "@/lib/media-playback";
import { VoiceInputButton } from "@/components/voice-input-button";
import { useI18n } from "@/components/i18n-provider";
import { usePhoenixUrl } from "@/lib/phoenix";

interface LiveTool extends ToolActivity { status: "running" | "done"; }

function dateLabel(value: string, locale: string) {
  return new Intl.DateTimeFormat(locale, { month: "numeric", day: "numeric" }).format(new Date(value));
}

function displayTool(tool: LiveTool | ToolActivity, t: ReturnType<typeof useI18n>["t"]) {
  const running = "status" in tool && tool.status === "running";
  return (
    <div className={`tool-activity ${running ? "running" : tool.ok ? "" : "failed"}`} key={`${tool.name}-${tool.summary}-${JSON.stringify(tool.input)}`}>
      <span className="tool-activity-icon" aria-hidden="true">{running ? <LoaderCircle size={13} className="spin" /> : tool.ok ? "✓" : "!"}</span>
      <span className="tool-activity-label">{toolLabel(tool)}</span>
      <span className="tool-activity-state">{running ? t("chat.tool.processing") : tool.ok ? t("chat.tool.done") : t("chat.tool.attention")}</span>
      {!running && tool.summary && <span className="tool-summary" title={tool.summary}>{tool.summary}</span>}
    </div>
  );
}

function ToolActivityGroup({ tools }: { tools: (LiveTool | ToolActivity)[] }) {
  const { t } = useI18n();
  const runningCount = tools.filter((tool) => "status" in tool && tool.status === "running").length;
  const failedCount = tools.filter((tool) => !("status" in tool && tool.status === "running") && !tool.ok).length;
  const knowledgeBaseCount = tools.filter((tool) => tool.name.startsWith("kb_")).length;
  const timelineCount = tools.filter((tool) => tool.name.startsWith("timeline_")).length;
  const groupName = knowledgeBaseCount === tools.length ? t("chat.tool.knowledge") : timelineCount === tools.length ? t("chat.tool.timeline") : knowledgeBaseCount > 0 || timelineCount > 0 ? t("chat.tool.general") : t("chat.tool.memory");
  const label = runningCount ? t("chat.tool.runningLabel", { count: tools.length, group: groupName }) : t("chat.tool.doneLabel", { count: tools.length, group: groupName });
  const state = runningCount ? t("chat.tool.runningState", { count: runningCount }) : failedCount ? t("chat.tool.attentionState", { count: failedCount }) : t("chat.tool.done");

  return <details className={`tool-group ${failedCount ? "has-failure" : ""}`} open={runningCount > 0}>
    <summary><span className="tool-group-icon"><ListChecks size={13} /></span><span className="tool-group-label">{label}</span><span className="tool-group-state">{state}</span><ChevronDown size={13} className="tool-group-chevron" /></summary>
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
  if (!catalog) return null;
  return <label className="chat-model-picker"><span>模型</span><select value={value ?? ""} disabled={disabled} onChange={(event) => onChange(event.target.value ? Number(event.target.value) : null)} aria-label="选择聊天模型"><option value="">跟随默认模型</option>{catalog.services.map((service) => <optgroup label={service.name} key={service.id}>{catalog.profiles.filter((profile) => profile.service_id === service.id).map((profile) => <option value={profile.id} disabled={!profile.available && profile.id !== value} key={profile.id}>{profile.display_name}{profile.is_default ? " · 默认" : profile.available ? "" : " · 不可用"}</option>)}</optgroup>)}</select></label>;
}

function HomeDashboard({ conversations, input, memoryCount, sending, backgroundResponseTitle, composerRef, onInput, onTranscription, onKeyDown, onSubmit, onOpenConversation, onReturnToResponse, modelCatalog, modelProfileId, onModelChange }: {
  conversations: Conversation[];
  input: string;
  memoryCount: number | null;
  sending: boolean;
  backgroundResponseTitle?: string;
  composerRef: RefObject<HTMLTextAreaElement | null>;
  onInput: (value: string) => void;
  onTranscription: (value: string) => void;
  onKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => void;
  onSubmit: (event: FormEvent) => void;
  onOpenConversation: (id: number) => void;
  onReturnToResponse: () => void;
  modelCatalog: ModelCatalog | null;
  modelProfileId: number | null;
  onModelChange: (value: number | null) => void;
}) {
  const { locale, t } = useI18n();
  const recent = conversations.slice(0, 2);
  return <div className="memory-home-scroll">
    <div className="memory-home">
      <section className="memory-home-hero">
        <div className="memory-home-copy">
          <div className="home-hero-meta"><p className="memory-date"><i /><i /><i />{homeDateLabel(locale)}</p><span><Sparkles size={11} />{memoryCount ?? "—"} {t("chat.home.memories")}</span></div>
          <h1>{greeting(t)}, Lance.<br /><em>{t("chat.home.question")}</em></h1>
          <p>{t("chat.home.description")}</p>
        </div>
      </section>

      {backgroundResponseTitle && <button type="button" className="home-background-stream" onClick={onReturnToResponse}><LoaderCircle size={13} className="spin" /><span>{t("chat.backgroundResponse", { title: backgroundResponseTitle })}</span><ArrowRight size={13} /></button>}
      <form className="home-capture" onSubmit={onSubmit}>
        <div className="home-capture-main"><span><Sparkles size={17} /></span><textarea ref={composerRef} rows={2} value={input} onChange={(event) => onInput(event.target.value)} onKeyDown={onKeyDown} placeholder={backgroundResponseTitle ? t("chat.composer.backgroundPlaceholder") : t("chat.home.placeholder")} aria-label={backgroundResponseTitle ? t("chat.composer.backgroundPlaceholder") : t("chat.home.placeholder")} disabled={sending} /></div>
        <div className="home-capture-foot"><div className="home-pills"><button type="button" disabled={sending} onClick={() => onInput(t("chat.home.prompt.organize"))}>{t("chat.home.prompt.organize")}</button><button type="button" disabled={sending} onClick={() => onInput(t("chat.home.prompt.review"))}>{t("chat.home.prompt.review")}</button></div><div className="home-capture-actions"><ModelPicker catalog={modelCatalog} value={modelProfileId} disabled={sending} onChange={onModelChange} /><VoiceInputButton disabled={sending} onTranscript={onTranscription} /><button className="home-send" type="submit" disabled={!input.trim() || sending} aria-label={t("chat.send")}><Send size={16} /></button></div></div>
      </form>

      <section className="memory-home-grid">
        <div className="home-panel">
          <div className="home-panel-heading"><div><span>{t("chat.home.recentKicker")}</span><h2>{t("chat.home.recentTitle")}</h2></div><button type="button" onClick={() => conversations[0] && onOpenConversation(conversations[0].id)}>{t("chat.home.continue")} <ArrowRight size={13} /></button></div>
          <div className="home-stream">
            {recent.length ? recent.map((conversation, index) => <button className="home-stream-item" type="button" onClick={() => onOpenConversation(conversation.id)} key={conversation.id}><i className={`home-stream-pin tone-${index + 1}`}><span /></i><span><time>{dateLabel(conversation.updated_at, locale)}</time><strong>{conversation.title}</strong></span><ArrowRight size={15} /></button>) : <div className="home-stream-empty">{t("chat.home.empty")}</div>}
          </div>
        </div>
        <aside className="home-rail home-quick-links">
          <Link className="home-quick-card" href="/review"><span><CalendarDays size={17} /></span><div><small>{t("chat.home.reviewKicker")}</small><strong>{t("nav.review")}</strong><em>{t("chat.home.reviewAction")}</em></div><ArrowRight size={14} /></Link>
          <Link className="home-quick-card" href="/timeline"><span><CalendarClock size={17} /></span><div><small>{t("nav.timeline")}</small><strong>{t("timeline.view.today")}</strong><em>{t("timeline.view.upcoming")}</em></div><ArrowRight size={14} /></Link>
        </aside>
      </section>
    </div>
  </div>;
}

function TurnView({ turn, streaming = false, highlighted = false, showThinking = true, showToolActivity = true, showUsage = true, ttsLoading = false, ttsPlaying = false, ttsAvailable = false, ttsDisabledReason, turnRef, onEdit, onRegenerate, onSpeak }: { turn: Turn; streaming?: boolean; highlighted?: boolean; showThinking?: boolean; showToolActivity?: boolean; showUsage?: boolean; ttsLoading?: boolean; ttsPlaying?: boolean; ttsAvailable?: boolean; ttsDisabledReason?: string; turnRef?: (node: HTMLDivElement | null) => void; onEdit?: () => void; onRegenerate?: () => void; onSpeak?: () => void }) {
  const { t } = useI18n();
  const [copied, setCopied] = useState(false);
  const unavailableReason = ttsDisabledReason ?? t("chat.tts.unknown");
  const copyTurn = async () => {
    await navigator.clipboard.writeText(turn.text);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  };
  if (turn.kind === "user") {
    return <div className={`turn user-turn ${highlighted ? "message-highlight" : ""}`} ref={turnRef} data-message-id={turn.messageId}><div className="user-message-group"><div className="user-bubble">{turn.text}</div><div className="turn-actions"><button onClick={() => void copyTurn()}>{copied ? <Check size={12} /> : <Copy size={12} />}{copied ? t("chat.copied") : t("chat.copy")}</button>{onEdit && <button onClick={onEdit}><Pencil size={12} />{t("chat.editResend")}</button>}</div></div></div>;
  }
  return (
    <div className={`turn assistant-turn ${streaming ? "is-streaming" : ""} ${highlighted ? "message-highlight" : ""}`} ref={turnRef} data-message-id={turn.messageId}>
      {showThinking && turn.thinking && (
        <details className="thinking">
          <summary>{streaming ? t("chat.thinkingActive") : t("chat.thinking")}</summary>
          <div className="thinking-body">{turn.thinking}</div>
        </details>
      )}
      {showToolActivity && turn.tools.length > 0 && <ToolActivityGroup tools={turn.tools} />}
      {streaming && !turn.text && !turn.thinking && turn.tools.length === 0 && <div className="chat-response-phase"><span /><span>{t("chat.phase.connecting")}</span></div>}
      {(turn.text || streaming) && <div className="assistant-content"><Markdown highlightCode={!streaming}>{turn.text}</Markdown>{streaming && <span className="streaming-cursor" />}</div>}
      {turn.usage?.interrupted && <div className="interrupted-answer"><TriangleAlert size={13} /><span>{t("chat.interrupted")}</span></div>}
      {showUsage && !turn.usage?.interrupted && turn.usage && usageLabel(turn.usage) && <div className="message-usage">{usageLabel(turn.usage)}</div>}
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
  if (!context) return null;
  const number = (value: number) => value.toLocaleString(locale);
  const cacheRate = context.prompt_tokens > 0 ? Math.round(context.cached_tokens / context.prompt_tokens * 100) : 0;
  return <details className="chat-context-indicator">
    <summary><Gauge size={12} /><span>{t("chat.context.label")}</span><b>{context.prompt_tokens ? t("chat.context.tokens", { count: number(context.prompt_tokens) }) : t("chat.context.turns", { count: context.retained_turns })}</b></summary>
    <div className="chat-context-popover">
      <div><span>{t("chat.context.promptTokens")}</span><strong>{number(context.prompt_tokens)} tokens</strong></div>
      <div><span>{t("chat.context.retained")}</span><strong>{t("chat.context.turns", { count: context.retained_turns })}</strong></div>
      <div><span>{t("chat.context.trimmed")}</span><strong>{number(context.trimmed_messages)}</strong></div>
      <div><span>{t("chat.context.budget")}</span><strong>{number(context.history_chars)} / {number(context.history_budget_chars)} chars</strong></div>
      <div><span>{t("chat.context.cache")}</span><strong>{cacheRate}%</strong></div>
    </div>
  </details>;
}

export function ChatPage() {
  const { t } = useI18n();
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
  const [preferences, setPreferences] = useState<UserPreferences>(defaultPreferences);
  const [ttsStatus, setTtsStatus] = useState<TtsStatus | null>(null);
  const [ttsLoadingId, setTtsLoadingId] = useState<number | null>(null);
  const [ttsPlayingId, setTtsPlayingId] = useState<number | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const mountedRef = useRef(true);
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
  const [memoryCount, setMemoryCount] = useState<number | null>(null);
  const [modelCatalog, setModelCatalog] = useState<ModelCatalog | null>(null);
  const [selectedModelProfileId, setSelectedModelProfileId] = useState<number | null>(null);
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

  useEffect(() => {
    void getMemoryStats(30, 1).then((stats) => setMemoryCount(stats.total_memories)).catch(() => undefined);
    void getModelCatalog().then(setModelCatalog).catch(() => undefined);
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
      if (change.type === "renamed") {
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
  }, []);

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

  const send = async (contentOverride?: string, targetIdOverride?: number, conversationIdOverride?: number) => {
    const content = (contentOverride ?? input).trim();
    const conversationId = conversationIdOverride ?? selectedId;
    if (!content || sending) return;
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
    sendingRef.current = true;
    setSending(true);
    setError("");
    setInput("");
    setPendingUser(content);
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
      }, controller.signal);
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
  const startHomeConversation = (event: FormEvent) => {
    event.preventDefault();
    const content = input.trim();
    if (!content || sending) return;
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
    if (event.key === "Escape" && editingTarget !== null) { setEditingTarget(null); setInput(""); }
  };

  const editMessage = (turn: Extract<Turn, { kind: "user" }>) => {
    if (turn.messageId === undefined) return;
    setEditingTarget(turn.messageId);
    setInput(turn.text);
    window.requestAnimationFrame(() => composerRef.current?.focus());
  };

  const cancelEditing = () => {
    setEditingTarget(null);
    setInput("");
    window.requestAnimationFrame(() => composerRef.current?.focus());
  };

  const visibleConversations = showArchived ? archivedConversations : conversations;
  const selected = useMemo(() => visibleConversations.find((item) => item.id === selectedId), [selectedId, visibleConversations]);
  const streamConversation = useMemo(() => conversations.find((item) => item.id === streamConversationId), [conversations, streamConversationId]);
  const streamBelongsToSelection = streamConversationId !== null && streamConversationId === selectedId;
  const activeTraceId = streamConversationId === null ? undefined : traceIds[streamConversationId];
  const displayTurns = useMemo(() => {
    const result = [...turns] as Turn[];
    if (streamBelongsToSelection && pendingUser) result.push({ kind: "user", text: pendingUser });
    // sending 期间无条件给出助手气泡：首个 token 之前它只有一个闪烁光标，
    // 否则从发送到首字之间界面上没有任何「在回答」的迹象，看着像卡死。
    // 流结束后不看 sending，让 draft 一直留在屏幕上，直到 loadMessages
    // 把权威历史换进来（见 loadMessages）—— 中间不能有空档，否则正文会闪一下。
    if (streamBelongsToSelection && (sending || draft.text || draft.thinking || draft.tools.length)) {
      result.push({ kind: "assistant", text: draft.text, thinking: draft.thinking, tools: draft.tools });
    }
    return result;
  }, [draft, pendingUser, sending, streamBelongsToSelection, turns]);

  if (loadingConversations) return <WorkspacePageFallback active="chat" messageKey="loading.connecting" />;

  return (
    <div className="app-shell">
      <main className="main-panel">
        {selectedId === null ? <HomeDashboard conversations={conversations} input={input} memoryCount={memoryCount} sending={sending} backgroundResponseTitle={sending && streamConversationId !== null ? streamConversation?.title ?? t("chat.current") : undefined} composerRef={composerRef} onInput={setInput} onTranscription={appendTranscription} onKeyDown={onKeyDown} onSubmit={startHomeConversation} onOpenConversation={selectConversation} onReturnToResponse={() => { if (streamConversationId !== null) selectConversation(streamConversationId); }} modelCatalog={modelCatalog} modelProfileId={selectedModelProfileId} onModelChange={setSelectedModelProfileId} /> : <>
          <div className="chat-conversation-toolbar">
            <div className="chat-conversation-toolbar-inner">
              <div className="chat-conversation-title"><span>{showArchived ? t("chat.archived") : t("chat.current")}</span><strong>{selected?.title ?? t("chat.opening")}</strong>{sending && streamBelongsToSelection && <em className="chat-live-status"><i />{t("chat.responding")}</em>}</div>
              <div className="chat-conversation-toolbar-right">
                <ModelPicker catalog={modelCatalog} value={selectedModelProfileId} disabled={sending} onChange={setSelectedModelProfileId} />
                {streamBelongsToSelection && activeTraceId && <TraceControls traceId={activeTraceId} />}
                {sending && !streamBelongsToSelection && streamConversationId !== null && <button type="button" className="background-stream-button" onClick={() => selectConversation(streamConversationId)} title={t("chat.returnToResponse")}><LoaderCircle size={13} className="spin" /><span>{t("chat.backgroundResponse", { title: streamConversation?.title ?? t("chat.current") })}</span><ArrowRight size={13} /></button>}
              </div>
            </div>
          </div>
          <div className="message-scroll" ref={scrollRef} onWheel={(event) => { if (event.deltaY < 0) pauseAutoScroll(); }} onTouchStart={(event) => { touchStartYRef.current = event.touches[0]?.clientY ?? null; }} onTouchMove={(event) => { const start = touchStartYRef.current; const current = event.touches[0]?.clientY; if (start !== null && current !== undefined && current > start + 4) pauseAutoScroll(); }} onTouchEnd={() => { touchStartYRef.current = null; }} onScroll={(event) => handleMessageScroll(event.currentTarget)}>
            {loadingMessages && !(streamBelongsToSelection && pendingUser) ? <div className="centered-empty">{t("chat.loadingMessages")}</div> : displayTurns.length === 0 ? <div className="chat-empty-state"><span><Sparkles size={18} /></span><h2>{t("chat.empty.title")}</h2><p>{t("chat.empty.description")}</p></div> : displayTurns.map((turn, index) => { const previous = index > 0 ? displayTurns[index - 1] : undefined; const previousUser = previous?.kind === "user" ? previous : undefined; const isAssistant = turn.kind === "assistant"; const hasSpeechButton = isAssistant && turn.messageId !== undefined && ttsStatus?.mode !== undefined && ttsStatus.mode !== "off"; return <TurnView turn={turn} showThinking={preferences.showThinking} showToolActivity={preferences.showToolActivity} showUsage={preferences.showUsage} highlighted={turn.messageId === highlightedMessageId} ttsAvailable={ttsAvailable} ttsDisabledReason={ttsDisabledReason} ttsLoading={isAssistant && turn.messageId !== undefined && ttsLoadingId === turn.messageId} ttsPlaying={isAssistant && turn.messageId !== undefined && ttsPlayingId === turn.messageId} turnRef={(node) => { if (turn.messageId !== undefined) { if (node) messageRefs.current.set(turn.messageId, node); else messageRefs.current.delete(turn.messageId); } }} onEdit={turn.kind === "user" && !sending ? () => editMessage(turn) : undefined} onRegenerate={turn.kind === "assistant" && !sending && previousUser?.messageId !== undefined ? () => void send(previousUser.text, previousUser.messageId) : undefined} onSpeak={hasSpeechButton ? () => void speakText(turn.text, turn.messageId) : undefined} streaming={sending && streamBelongsToSelection && index === displayTurns.length - 1 && turn.kind === "assistant"} key={`${turn.kind}-${turn.messageId ?? index}`} />; })}
          </div>
          {awayFromBottom && <button className="chat-scroll-latest" type="button" aria-label={t("chat.scrollToBottom")} title={t("chat.scrollToBottom")} onClick={scrollToLatest}><ArrowDown size={17} /><span>{t("chat.scrollToBottom")}</span></button>}
          <div className="composer-wrap">
            {error && <div className="error-banner">{error}</div>}
            <form className="composer" onSubmit={onSubmit}>
              <textarea ref={composerRef} rows={1} value={input} onChange={(event) => setInput(event.target.value)} onKeyDown={onKeyDown} placeholder={sending && !streamBelongsToSelection ? t("chat.composer.backgroundPlaceholder") : editingTarget !== null ? t("chat.composer.editPlaceholder") : t("chat.composer.placeholder")} disabled={sending} />
              <div className="composer-bottom">
                <div className="composer-meta">
                  <ContextIndicator context={conversationContext} />
                  {editingTarget !== null && <button type="button" className="composer-cancel-edit" onClick={cancelEditing}>{t("chat.cancelEdit")}</button>}
                  <span className="composer-hint">{sending && !streamBelongsToSelection ? t("chat.backgroundHint") : editingTarget !== null ? t("chat.editHint") : t("chat.sendHint")}</span>
                </div>
                <div className="composer-actions">
                  <VoiceInputButton disabled={sending} onTranscript={appendTranscription} />
                  {sending
                    ? streamBelongsToSelection ? <button type="button" className="ghost-button stop-button composer-submit" onClick={stop} aria-label={t("chat.stop")} title={t("chat.stop")}><Square size={14} /></button> : <button type="button" className="ghost-button composer-submit" onClick={() => streamConversationId !== null && selectConversation(streamConversationId)} aria-label={t("chat.returnToResponse")} title={t("chat.returnToResponse")}><ArrowRight size={15} /></button>
                    : <button type="submit" className="primary-button composer-submit" disabled={!input.trim()} aria-label={editingTarget !== null ? t("chat.resend") : t("chat.send")} title={editingTarget !== null ? t("chat.resend") : t("chat.send")}><Send size={17} /></button>}
                </div>
              </div>
            </form>
          </div>
        </>}
        <audio ref={audioRef} className="tts-audio" onEnded={handleSpeechEnded} onError={handleSpeechError} />
      </main>
    </div>
  );
}
