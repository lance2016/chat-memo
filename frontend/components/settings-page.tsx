"use client";

import { useCallback, useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import Link from "next/link";
import { Activity, AudioLines, BellRing, BrainCircuit, Bug, CalendarClock, Check, ChevronRight, Clipboard, Clock3, Copy, Download, Eye, Gauge, HardDriveDownload, Headphones, Mic2, RefreshCw, RotateCcw, Save, Send, ServerCog, Settings2, SlidersHorizontal, Smartphone, Trash2, UserRound, Volume2, X, type LucideIcon } from "lucide-react";
import { apiBaseLabel, clearDebugRequests, createBackup, errorMessage, getAsrStatus, getDebugPrompt, getDebugRequest, getHealth, getNotifyStatus, getRuntimeSettings, getTtsStatus, getTtsVoices, listDebugRequests, sendTestNotification, synthesizeSpeech, updateRuntimeSettings, warmupSpeech } from "@/lib/api";
import { defaultPreferences, preferencesChangeEvent, readPreferences, writePreferences, type UserPreferences } from "@/lib/preferences";
import type { AsrStatus, BackupResult, DebugPrompt, DebugRequestDetail, DebugRequestList, HealthStatus, NotifyStatus, RuntimeSettingField, RuntimeSettings, TtsStatus } from "@/lib/types";
import { confirmAppNavigation, useNavigationGuard } from "@/lib/navigation-guard";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { ToolCatalog } from "@/components/tool-catalog";
import { useI18n } from "@/components/i18n-provider";

type SettingsSectionKey = "general" | "assistant" | "model" | "review" | "notify" | "voice" | "voiceInput" | "system" | "advanced";

const settingsSections: Array<{ key: SettingsSectionKey; icon: typeof Settings2 }> = [
  { key: "general", icon: SlidersHorizontal },
  { key: "assistant", icon: UserRound },
  { key: "model", icon: BrainCircuit },
  { key: "review", icon: Clock3 },
  { key: "notify", icon: BellRing },
  { key: "voice", icon: Headphones },
  { key: "voiceInput", icon: Mic2 },
  { key: "system", icon: HardDriveDownload },
  { key: "advanced", icon: Bug },
];

const reviewFieldKeys = new Set(["consolidate_model", "consolidate_auto", "consolidate_hour"]);
const modelPrimaryFieldKeys = new Set(["provider", "model", "deepseek_model", "effort", "deepseek_thinking"]);
const ttsPrimaryFieldKeys = new Set(["tts_mode", "tts_model", "tts_voice", "tts_instruct", "tts_speed_percent"]);
// 「怎么送到手机」和「什么时候响」分两屏 —— 配 Bark 是一次性动作，调提前量是长期反复调的。
const notifyChannelFieldKeys = new Set(["notify_enabled", "notify_channels", "bark_server", "bark_key", "bark_sound", "bark_icon", "notify_public_base_url"]);

const fieldHelp: Record<string, string> = {
  owner_name: "助手在对话中对你的称呼",
  custom_instructions: "每次请求都会遵循的固定工作方式",
  provider: "日常对话使用的模型服务",
  model: "Anthropic 对话模型",
  deepseek_model: "DeepSeek 对话模型",
  max_tokens: "单次回答允许生成的最大 token 数",
  deepseek_max_tokens: "单次回答允许生成的最大 token 数",
  effort: "更高强度通常更慢，也会消耗更多 token",
  deepseek_thinking: "新会话默认是否启用深度思考",
  max_tool_iterations: "限制模型连续调用记忆工具的轮次",
  consolidate_model: "留空时沿用日常聊天模型",
  title_model: "智谱兼容配置；设置 SILICONFLOW_API_KEY 后优先使用环境变量中的硅基流动标题模型",
  consolidate_auto: "按固定时间自动整理当天对话",
  consolidate_hour: "使用后端所在时区的整点时间",
  tts_mode: "关闭、手动播放或回答完成后自动播放",
  history_max_chars: "限制每轮发给模型的历史长度，避免长会话撞到上下文窗口",
  notify_timeout: "推送通道和提醒文案模型调用的最长等待时间",
  tts_model: "选择本地语音服务已加载或当前配置的模型",
  tts_voice: "可选音色会随语音模型自动更新",
  tts_lang_code: "语音合成的主要语言",
  tts_instruct: "控制语气、情绪与表达节奏",
  tts_format: "浏览器接收的音频编码格式",
  tts_stream: "边合成边传输，通常可缩短首段等待",
  tts_speed_percent: "100% 为模型默认语速",
  tts_max_chars: "超过长度时只朗读前面的内容",
  tts_timeout: "语音服务单次请求最长等待时间",
  tts_warmup: "后端启动时预先加载语音模型，缩短首次播放等待",
  asr_model: "选择本地缓存的语音识别模型；0.6B 更快，1.7B 通常更准确",
  asr_language: "固定语言可省去自动判断；中英混说时选择自动检测",
  asr_max_tokens: "短语音建议保持 512，降低静音或噪声导致的异常长识别",
  asr_timeout: "语音识别服务单次请求最长等待时间",
  debug_prompts: "临时保存最近请求，可能包含完整对话原文",
};

const voiceLabels: Record<string, string> = {
  Vivian: "明亮灵动女声 · 中文",
  Serena: "温柔年轻女声 · 中文",
  Uncle_Fu: "低沉醇厚男声 · 中文",
  Dylan: "清晰自然男声 · 北京话",
  Eric: "明快微沙男声 · 四川话",
  Ryan: "节奏感男声 · 英文",
  Aiden: "阳光清晰男声 · 英文",
  Ono_Anna: "轻快俏皮女声 · 日文",
  Sohee: "温暖细腻女声 · 韩文",
};

const settingChoiceLabels: Record<string, string> = {
  off: "关闭语音",
  manual: "手动播放",
  auto: "回答后自动播放",
  mp3: "MP3 · 兼容性最佳",
  wav: "WAV · 无损",
  flac: "FLAC · 无损压缩",
  opus: "Opus · 体积更小",
  Chinese: "中文 · 更快更稳定",
  English: "英文 · 更快更稳定",
  Auto: "自动检测 · 适合多语言",
};

function formatModelSize(bytes: number) {
  if (bytes <= 0) return "已缓存";
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
}

function Toggle({ checked, onChange, label, description }: { checked: boolean; onChange: (checked: boolean) => void; label: string; description: string }) {
  return <label className="settings-toggle-row"><span className="settings-toggle-copy"><strong>{label}</strong><span>{description}</span></span><input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} /><span className="toggle-track" aria-hidden="true"><span /></span></label>;
}

function SettingValue({ label, value, tone = "" }: { label: string; value: string; tone?: string }) {
  return <div className="settings-value"><span>{label}</span><strong className={tone}>{value}</strong></div>;
}

type SettingsVisualGroupTone = "neutral" | "accent" | "violet" | "warm" | "success";

function SettingsVisualGroup({ icon: Icon, title, description, tone = "neutral", action, children, className = "" }: { icon: LucideIcon; title: string; description: string; tone?: SettingsVisualGroupTone; action?: ReactNode; children: ReactNode; className?: string }) {
  const headingId = useId();
  return <section className={`settings-visual-group tone-${tone} ${className}`.trim()} aria-labelledby={headingId}>
    <header className="settings-visual-group-heading">
      <span className="settings-visual-group-icon" aria-hidden="true"><Icon size={15} /></span>
      <div><h3 id={headingId}>{title}</h3><p>{description}</p></div>
      {action && <div className="settings-visual-group-action">{action}</div>}
    </header>
    <div className="settings-visual-group-body">{children}</div>
  </section>;
}

function RuntimeField({ field, value, source, providers, ttsStatus, asrStatus, ttsVoices, ttsVoicesLoading, disabled, pendingReset = false, onChange, onRestore }: { field: RuntimeSettingField; value: unknown; source?: "db" | "env" | "default"; providers: RuntimeSettings["providers"]; ttsStatus?: TtsStatus | null; asrStatus?: AsrStatus | null; ttsVoices: string[]; ttsVoicesLoading: boolean; disabled: boolean; pendingReset?: boolean; onChange: (value: unknown) => void; onRestore: () => void }) {
  const stringValue = value === null || value === undefined ? "" : String(value);
  const providerChoices = field.key === "provider" ? providers : [];
  const isTtsModel = field.key === "tts_model";
  const isTtsVoice = field.key === "tts_voice";
  const isAsrModel = field.key === "asr_model";
  const modelChoices = isTtsModel ? Array.from(new Set([stringValue, ttsStatus?.model, ...(ttsStatus?.cached_models ?? []).map((item) => item.id), ...(ttsStatus?.models ?? [])].filter((item): item is string => Boolean(item)))) : [];
  const voiceChoices = isTtsVoice ? Array.from(new Set([stringValue, ...ttsVoices].filter(Boolean))) : [];
  const asrModelChoices = isAsrModel ? Array.from(new Set([stringValue, asrStatus?.model, ...(asrStatus?.cached_models ?? []).map((item) => item.id), ...(asrStatus?.models ?? []).filter((item) => /asr|whisper|parakeet|voxtral/i.test(item))].filter((item): item is string => Boolean(item)))) : [];
  const choices = field.key === "provider" ? providerChoices.map((item) => item.value) : isTtsModel ? modelChoices : isTtsVoice ? voiceChoices : isAsrModel ? asrModelChoices : field.choices;
  const providerReason = field.key === "provider" ? providerChoices.find((item) => item.value === stringValue)?.reason : "";
  const multiline = field.kind === "text" || field.key === "tts_instruct";

  const controlDisabled = disabled || pendingReset || (isTtsVoice && ttsVoicesLoading);

  return <div className={`runtime-setting-row ${pendingReset ? "pending-reset" : ""}`}>
    <div className="runtime-setting-label"><strong>{field.label}</strong><span>{fieldHelp[field.key] ?? (field.provider ? `仅用于 ${field.provider}` : "保存后立即生效")}</span></div>
    <div className={`runtime-setting-control ${multiline ? "multiline" : ""}`}>
      {field.kind === "bool" && <label className="runtime-checkbox"><input type="checkbox" checked={value === true} disabled={controlDisabled} onChange={(event) => onChange(event.target.checked)} /><span>{value === true ? "开启" : "关闭"}</span></label>}
      {field.kind === "enum" && <select className="runtime-select" value={stringValue} disabled={controlDisabled} onChange={(event) => onChange(event.target.value)}>{choices.map((choice) => { const option = providerChoices.find((item) => item.value === choice); return <option key={choice} value={choice} disabled={option ? !option.available : false}>{settingChoiceLabels[choice] ?? choice}{option && !option.available ? "（不可用）" : ""}</option>; })}</select>}
      {field.kind === "int" && field.key !== "tts_speed_percent" && <input className="runtime-input" type="number" value={stringValue} min={field.minimum ?? undefined} max={field.maximum ?? undefined} disabled={controlDisabled} onChange={(event) => onChange(event.target.value === "" ? "" : Number(event.target.value))} />}
      {field.kind === "int" && field.key === "tts_speed_percent" && <div className="tts-speed-control"><input type="range" value={stringValue} min={field.minimum ?? 50} max={field.maximum ?? 200} step={5} disabled={controlDisabled} onChange={(event) => onChange(Number(event.target.value))} /><output>{stringValue}%</output></div>}
      {multiline && <textarea className={`runtime-textarea ${field.key === "custom_instructions" ? "runtime-textarea-tall" : ""}`} value={stringValue} maxLength={field.maximum ?? undefined} minLength={field.minimum ?? undefined} disabled={controlDisabled} onChange={(event) => onChange(event.target.value)} placeholder={field.key === "custom_instructions" ? "例如：回答控制在三句话以内，代码优先给 diff。" : "例如：用温柔、自然、亲切的语气说话"} />}
      {field.kind === "str" && field.key !== "tts_instruct" && !isTtsModel && !isTtsVoice && !isAsrModel && <input className="runtime-input runtime-input-wide" type="text" value={stringValue} maxLength={field.maximum ?? undefined} minLength={field.minimum ?? undefined} disabled={controlDisabled} onChange={(event) => onChange(event.target.value)} />}
      {field.kind === "str" && (isTtsModel || isTtsVoice || isAsrModel) && <select className="runtime-select" value={stringValue} disabled={controlDisabled} onChange={(event) => onChange(event.target.value)}>{isTtsVoice && !stringValue && <option value="">默认音色</option>}{choices.map((choice) => { const cached = isAsrModel ? asrStatus?.cached_models?.find((item) => item.id === choice) : ttsStatus?.cached_models?.find((item) => item.id === choice); const loaded = isAsrModel ? asrStatus?.models.includes(choice) : ttsStatus?.models.includes(choice); const modelSuffix = isTtsModel || isAsrModel ? [cached ? `已缓存 ${formatModelSize(cached.size_bytes)}` : "", loaded ? "已加载" : ""].filter(Boolean).join(" · ") : ""; return <option key={choice} value={choice}>{isTtsVoice && voiceLabels[choice] ? `${choice} · ${voiceLabels[choice]}` : `${choice}${modelSuffix ? ` · ${modelSuffix}` : ""}`}</option>; })}</select>}
      {isTtsVoice && ttsVoicesLoading && <span className="runtime-inline-status"><RefreshCw size={11} className="spin" />读取音色</span>}
      <span className={`runtime-source ${source === "db" ? "modified" : ""} ${pendingReset ? "pending" : ""}`}>{pendingReset ? "待恢复默认" : source === "db" ? "已覆盖默认" : source === "env" ? "环境覆盖" : "代码默认"}</span>
      {source === "db" && <button className="icon-button runtime-restore" type="button" aria-label={pendingReset ? `取消恢复${field.label}` : `恢复${field.label}默认值`} title={pendingReset ? "取消恢复" : "恢复默认"} disabled={disabled} onClick={onRestore}><RotateCcw size={12} /></button>}
    </div>
    {multiline && <span className="runtime-setting-hint runtime-character-count">已用 {stringValue.length}{field.maximum ? ` / ${field.maximum}` : ""} 字</span>}
    {providerReason && <span className="runtime-setting-hint">{providerReason}</span>}
  </div>;
}

function ttsStatusPresentation(status: TtsStatus | null, loading: boolean) {
  if (loading) return { tone: "unknown", label: "正在检查语音服务" };
  if (!status) return { tone: "unknown", label: "语音状态未知" };
  if (status.mode === "off") return { tone: "unknown", label: "语音已关闭" };
  if (!status.reachable) return { tone: "offline", label: "语音服务离线" };
  if (status.detail) return { tone: "warning", label: "语音服务需检查" };
  return { tone: "online", label: "语音服务在线" };
}

function debugTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(date);
}

function DebugDialog({ kind, prompt, request, loading, error, copied, onClose, onCopy }: { kind: "prompt" | "request"; prompt: DebugPrompt | null; request: DebugRequestDetail | null; loading: boolean; error: string; copied: "prompt" | "payload" | null; onClose: () => void; onCopy: (text: string, target: "prompt" | "payload") => void }) {
  const isPrompt = kind === "prompt";
  const promptText = prompt?.system ?? "";
  const payloadText = request ? JSON.stringify(request.payload, null, 2) : "";
  return <div className="debug-dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <section className="debug-dialog" role="dialog" aria-modal="true" aria-labelledby="debug-dialog-title">
      <header className="debug-dialog-header"><div><span className="card-kicker">{isPrompt ? "SYSTEM PROMPT" : "MODEL REQUEST"}</span><h2 id="debug-dialog-title">{isPrompt ? "当前 system prompt" : request ? `请求 #${request.id}` : "请求详情"}</h2></div><button className="icon-button" type="button" aria-label="关闭调试详情" onClick={onClose}><X size={16} /></button></header>
      {loading ? <div className="settings-loading"><RefreshCw size={15} className="spin" />读取调试数据…</div> : error ? <div className="debug-dialog-error">{error}</div> : isPrompt && prompt ? <>
        <div className="debug-dialog-meta"><span>{prompt.chars.toLocaleString()} 字符</span><span>约 {prompt.approx_tokens.toLocaleString()} tokens</span></div>
        <pre className="debug-code debug-prompt-code">{promptText || "（当前 system prompt 为空）"}</pre>
        <p className="debug-dialog-note">{prompt.note}</p>
        <footer className="debug-dialog-footer"><button className="ghost-button" type="button" onClick={() => onCopy(promptText, "prompt")} disabled={!promptText}><Copy size={13} />{copied === "prompt" ? "已复制" : "复制 prompt"}</button></footer>
      </> : request ? <>
        <div className="debug-dialog-meta"><span>{request.provider} · {request.model}</span><span>第 {request.iteration + 1} 次请求</span><span>{debugTime(request.at)}</span></div>
        <div className="debug-stat-row"><span><b>{request.messages}</b> messages</span><span><b>{request.tools}</b> tools</span><span><b>{request.system_chars.toLocaleString()}</b> system chars</span><span><b>{request.seconds.toFixed(2)}s</b></span></div>
        {request.error && <div className="debug-request-error">{request.error}</div>}
        <div className="debug-detail-block"><div className="debug-detail-heading"><strong>请求轮廓</strong></div><pre className="debug-code debug-outline-code">{request.outline.join("\n") || "（没有可显示的轮廓）"}</pre></div>
        <div className="debug-detail-block"><div className="debug-detail-heading"><strong>完整 payload</strong><button className="ghost-button" type="button" onClick={() => onCopy(payloadText, "payload")} disabled={!payloadText}><Clipboard size={12} />{copied === "payload" ? "已复制" : "复制 JSON"}</button></div><details className="debug-payload"><summary>展开 JSON</summary><pre className="debug-code debug-payload-code">{payloadText}</pre></details></div>
      </> : null}
    </section>
  </div>;
}

export function SettingsPage() {
  const { t } = useI18n();
  const [activeSection, setActiveSection] = useState<SettingsSectionKey>("general");
  const [runtime, setRuntime] = useState<RuntimeSettings | null>(null);
  const [draftValues, setDraftValues] = useState<Record<string, unknown>>({});
  const [pendingResets, setPendingResets] = useState<Set<string>>(() => new Set());
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [runtimeMessage, setRuntimeMessage] = useState("");
  const [preferences, setPreferences] = useState<UserPreferences>(defaultPreferences);
  const [backupLoading, setBackupLoading] = useState(false);
  const [backup, setBackup] = useState<BackupResult | null>(null);
  const [ttsStatus, setTtsStatus] = useState<TtsStatus | null>(null);
  const [ttsStatusLoading, setTtsStatusLoading] = useState(false);
  const [ttsStatusError, setTtsStatusError] = useState("");
  const [asrStatus, setAsrStatus] = useState<AsrStatus | null>(null);
  const [asrStatusLoading, setAsrStatusLoading] = useState(false);
  const [asrStatusError, setAsrStatusError] = useState("");
  const [notifyStatus, setNotifyStatus] = useState<NotifyStatus | null>(null);
  const [notifyTesting, setNotifyTesting] = useState(false);
  const [notifyMessage, setNotifyMessage] = useState("");
  const [ttsVoices, setTtsVoices] = useState<string[]>([]);
  const [ttsVoicesLoading, setTtsVoicesLoading] = useState(false);
  const ttsVoiceRequestRef = useRef(0);
  const [ttsPreviewLoading, setTtsPreviewLoading] = useState(false);
  const [ttsPreviewMessage, setTtsPreviewMessage] = useState("");
  const previewAudioRef = useRef<HTMLAudioElement>(null);
  const previewUrlRef = useRef("");
  const ttsReachableRef = useRef<boolean | null>(null);
  const [debugRequests, setDebugRequests] = useState<DebugRequestList | null>(null);
  const [debugRequestsLoading, setDebugRequestsLoading] = useState(false);
  const [debugPrompt, setDebugPrompt] = useState<DebugPrompt | null>(null);
  const [debugPromptLoading, setDebugPromptLoading] = useState(false);
  const [debugDetail, setDebugDetail] = useState<DebugRequestDetail | null>(null);
  const [debugDetailLoading, setDebugDetailLoading] = useState(false);
  const [debugDialog, setDebugDialog] = useState<"prompt" | "request" | null>(null);
  const [debugError, setDebugError] = useState("");
  const [debugCopied, setDebugCopied] = useState<"prompt" | "payload" | null>(null);
  const [clearDebugPending, setClearDebugPending] = useState(false);
  const [clearingDebug, setClearingDebug] = useState(false);

  const loadRuntime = useCallback(async () => {
    setLoading(true);
    setError("");
    const [runtimeResult, healthResult] = await Promise.allSettled([getRuntimeSettings(), getHealth()]);
    if (runtimeResult.status === "fulfilled") {
      setRuntime(runtimeResult.value);
      setDraftValues(runtimeResult.value.values ?? {});
      setPendingResets(new Set());
    }
    if (healthResult.status === "fulfilled") setHealth(healthResult.value);
    if (runtimeResult.status === "rejected" && healthResult.status === "rejected") setError(errorMessage(runtimeResult.reason, "无法读取后端设置"));
    setLoading(false);
  }, []);

  const refreshTtsStatus = useCallback(async () => {
    setTtsStatusLoading(true);
    setTtsStatusError("");
    try {
      const status = await getTtsStatus();
      const becameOnline = ttsReachableRef.current === false && status.reachable;
      ttsReachableRef.current = status.reachable;
      setTtsStatus(status);
      setTtsVoices(status.voices ?? []);
      if (becameOnline) void warmupSpeech().catch(() => undefined);
    } catch (cause) {
      ttsReachableRef.current = false;
      setTtsStatusError(errorMessage(cause, "无法读取语音服务状态"));
    } finally {
      setTtsStatusLoading(false);
    }
  }, []);

  const refreshNotifyStatus = useCallback(async () => {
    try {
      setNotifyStatus(await getNotifyStatus());
    } catch {
      // 状态读不到不该挡住整个设置页；面板会显示成「还没有推送记录」。
      setNotifyStatus(null);
    }
  }, []);

  const runNotifyTest = async () => {
    setNotifyTesting(true);
    setNotifyMessage("");
    try {
      const result = await sendTestNotification();
      setNotifyMessage(result.delivered
        ? t("settings.notify.testOk", { channels: result.channels })
        : t("settings.notify.testFailed", { error: result.error || "unknown" }));
      await refreshNotifyStatus();
    } catch (cause) {
      setNotifyMessage(t("settings.notify.testFailed", { error: errorMessage(cause, "unknown") }));
    } finally {
      setNotifyTesting(false);
    }
  };

  const refreshAsrStatus = useCallback(async () => {
    setAsrStatusLoading(true);
    setAsrStatusError("");
    try {
      setAsrStatus(await getAsrStatus());
    } catch (cause) {
      setAsrStatusError(errorMessage(cause, "无法读取语音识别状态"));
    } finally {
      setAsrStatusLoading(false);
    }
  }, []);

  const refreshDebugRequests = useCallback(async () => {
    setDebugRequestsLoading(true);
    setDebugError("");
    try {
      setDebugRequests(await listDebugRequests());
    } catch (cause) {
      setDebugError(errorMessage(cause, "无法读取调试请求"));
    } finally {
      setDebugRequestsLoading(false);
    }
  }, []);

  useEffect(() => {
    setPreferences(readPreferences());
    void loadRuntime();
    void refreshTtsStatus();
    void refreshAsrStatus();
    const handlePreferenceChange = (event: Event) => {
      const detail = (event as CustomEvent<UserPreferences>).detail;
      if (detail) setPreferences(detail);
    };
    window.addEventListener(preferencesChangeEvent(), handlePreferenceChange);
    return () => window.removeEventListener(preferencesChangeEvent(), handlePreferenceChange);
  }, [loadRuntime, refreshAsrStatus, refreshTtsStatus]);

  useEffect(() => () => {
    previewAudioRef.current?.pause();
    if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
  }, []);

  const updatePreference = <K extends keyof UserPreferences>(key: K, value: UserPreferences[K]) => {
    const next = { ...preferences, [key]: value };
    setPreferences(next);
    writePreferences(next);
  };

  const activeProvider = typeof draftValues.provider === "string" ? draftValues.provider : runtime?.provider;
  const activeFields = useMemo(() => runtime?.fields?.filter((field) => !field.provider || field.provider === activeProvider) ?? [], [activeProvider, runtime]);
  const ungroupedFields = useMemo(() => activeFields.filter((field) => (field.group ?? "") === ""), [activeFields]);
  const modelFields = useMemo(() => ungroupedFields.filter((field) => !reviewFieldKeys.has(field.key)), [ungroupedFields]);
  const modelPrimaryFields = useMemo(() => modelFields.filter((field) => modelPrimaryFieldKeys.has(field.key)), [modelFields]);
  const modelAdvancedFields = useMemo(() => modelFields.filter((field) => !modelPrimaryFieldKeys.has(field.key)), [modelFields]);
  const reviewFields = useMemo(() => ungroupedFields.filter((field) => reviewFieldKeys.has(field.key)), [ungroupedFields]);
  const reviewPrimaryFields = useMemo(() => reviewFields.filter((field) => field.key !== "consolidate_model"), [reviewFields]);
  const reviewAdvancedFields = useMemo(() => reviewFields.filter((field) => field.key === "consolidate_model"), [reviewFields]);
  const promptFields = useMemo(() => activeFields.filter((field) => field.group === "prompt"), [activeFields]);
  const ttsFields = useMemo(() => activeFields.filter((field) => field.group === "tts"), [activeFields]);
  const asrFields = useMemo(() => activeFields.filter((field) => field.group === "asr"), [activeFields]);
  const ttsModeFields = useMemo(() => ttsFields.filter((field) => field.key === "tts_mode"), [ttsFields]);
  const ttsPrimaryFields = useMemo(() => ttsFields.filter((field) => field.key !== "tts_mode" && ttsPrimaryFieldKeys.has(field.key)), [ttsFields]);
  const ttsAdvancedFields = useMemo(() => ttsFields.filter((field) => !ttsPrimaryFieldKeys.has(field.key)), [ttsFields]);
  const notifyFields = useMemo(() => activeFields.filter((field) => field.group === "notify"), [activeFields]);
  const notifyChannelFields = useMemo(() => notifyFields.filter((field) => notifyChannelFieldKeys.has(field.key)), [notifyFields]);
  const notifyTimingFields = useMemo(() => notifyFields.filter((field) => !notifyChannelFieldKeys.has(field.key)), [notifyFields]);
  const debugFields = useMemo(() => activeFields.filter((field) => field.group === "debug"), [activeFields]);
  const changedKeys = useMemo(() => Array.from(new Set([
    ...activeFields.filter((field) => !Object.is(draftValues[field.key], runtime?.values?.[field.key])).map((field) => field.key),
    ...pendingResets,
  ])), [activeFields, draftValues, pendingResets, runtime]);
  useNavigationGuard(changedKeys.length > 0, "设置页有尚未保存的修改，确定放弃并离开吗？");
  const apiBase = apiBaseLabel();
  const connectionLabel = health?.status === "ok" ? "已连接" : health ? health.status : "未知";
  const thinkingDefault = runtime ? runtime.thinking_default ? "开启" : "关闭" : "—";
  const ttsMode = draftValues.tts_mode === "manual" || draftValues.tts_mode === "auto" ? draftValues.tts_mode : "off";
  const ttsPresentation = ttsStatusPresentation(ttsStatus, ttsStatusLoading);
  const selectedTtsModel = typeof draftValues.tts_model === "string" ? draftValues.tts_model : ttsStatus?.model ?? "";
  const selectedCachedModel = ttsStatus?.cached_models?.find((item) => item.id === selectedTtsModel);
  const selectedModelLoaded = ttsStatus?.models.includes(selectedTtsModel) ?? false;
  const selectedAsrModel = typeof draftValues.asr_model === "string" ? draftValues.asr_model : asrStatus?.model ?? "";
  const selectedCachedAsrModel = asrStatus?.cached_models.find((item) => item.id === selectedAsrModel);
  const selectedAsrModelLoaded = asrStatus?.models.includes(selectedAsrModel) ?? false;

  useEffect(() => {
    if (debugFields.length > 0) void refreshDebugRequests();
  }, [debugFields.length, refreshDebugRequests]);

  useEffect(() => {
    if (activeSection === "notify") void refreshNotifyStatus();
  }, [activeSection, refreshNotifyStatus]);

  const saveRuntime = async () => {
    if (!runtime || !changedKeys.length || saving) return;
    setSaving(true);
    setError("");
    setRuntimeMessage("");
    try {
      const changes = Object.fromEntries(changedKeys.map((key) => [key, pendingResets.has(key) ? null : draftValues[key]]));
      const updated = await updateRuntimeSettings(changes);
      setRuntime(updated);
      setDraftValues(updated.values ?? {});
      setPendingResets(new Set());
      void refreshTtsStatus();
      void refreshAsrStatus();
      if (debugFields.length > 0) void refreshDebugRequests();
      setRuntimeMessage("设置已保存，已立即生效");
    } catch (cause) {
      setError(errorMessage(cause, "无法保存后端设置"));
    } finally {
      setSaving(false);
    }
  };

  const changeRuntimeField = (field: RuntimeSettingField, value: unknown) => {
    setRuntimeMessage("");
    setPendingResets((current) => {
      if (!current.has(field.key)) return current;
      const next = new Set(current);
      next.delete(field.key);
      return next;
    });
    setDraftValues((current) => ({ ...current, [field.key]: value }));
  };

  const changeTtsField = (field: RuntimeSettingField, value: unknown) => {
    changeRuntimeField(field, value);
    if (field.key !== "tts_model" || typeof value !== "string" || !value) return;

    const requestId = ++ttsVoiceRequestRef.current;
    setTtsVoicesLoading(true);
    setTtsPreviewMessage("");
    void getTtsVoices(value).then((result) => {
      if (ttsVoiceRequestRef.current !== requestId) return;
      setTtsVoices(result.voices);
      setDraftValues((current) => {
        const currentVoice = typeof current.tts_voice === "string" ? current.tts_voice : "";
        if (!result.voices.length || result.voices.includes(currentVoice)) return current;
        return { ...current, tts_voice: result.voices[0] };
      });
    }).catch((cause) => {
      if (ttsVoiceRequestRef.current !== requestId) return;
      setTtsVoices([]);
      setTtsPreviewMessage(errorMessage(cause, "无法读取该模型的音色"));
    }).finally(() => {
      if (ttsVoiceRequestRef.current === requestId) setTtsVoicesLoading(false);
    });
  };

  const toggleRuntimeReset = (field: RuntimeSettingField) => {
    setRuntimeMessage("");
    setPendingResets((current) => {
      const next = new Set(current);
      if (next.has(field.key)) next.delete(field.key);
      else next.add(field.key);
      return next;
    });
  };

  const discardRuntimeChanges = () => {
    setDraftValues(runtime?.values ?? {});
    setPendingResets(new Set());
    setRuntimeMessage("");
  };

  const runBackup = async () => {
    if (backupLoading) return;
    setBackupLoading(true);
    setError("");
    setBackup(null);
    try {
      setBackup(await createBackup());
    } catch (cause) {
      setError(errorMessage(cause, "备份失败"));
    } finally {
      setBackupLoading(false);
    }
  };

  const runTtsPreview = async () => {
    if (ttsPreviewLoading) return;
    if (ttsMode === "off") {
      setTtsPreviewMessage("试听前请先将语音播放设为手动或自动，并保存设置。");
      return;
    }
    if (!ttsStatus || ttsStatus.mode === "off") {
      setTtsPreviewMessage("请先保存语音播放设置，再试听当前音色。");
      return;
    }
    if (!ttsStatus.enabled) {
      setTtsPreviewMessage("语音播放当前未启用。");
      return;
    }
    setTtsPreviewLoading(true);
    setTtsPreviewMessage("");
    try {
      const blob = await synthesizeSpeech({
        text: "你好，这是语音试听。",
        model: typeof draftValues.tts_model === "string" && draftValues.tts_model ? draftValues.tts_model : undefined,
        voice: typeof draftValues.tts_voice === "string" && draftValues.tts_voice ? draftValues.tts_voice : undefined,
        instruct: typeof draftValues.tts_instruct === "string" && draftValues.tts_instruct ? draftValues.tts_instruct : undefined,
      });
      if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
      const url = URL.createObjectURL(blob);
      previewUrlRef.current = url;
      const audio = previewAudioRef.current;
      if (!audio) throw new Error("浏览器音频播放器不可用");
      audio.src = url;
      audio.load();
      await audio.play();
      setTtsPreviewMessage("试听已开始");
    } catch (cause) {
      setTtsPreviewMessage(errorMessage(cause, "试听失败"));
    } finally {
      setTtsPreviewLoading(false);
    }
  };

  const openDebugPrompt = async () => {
    setDebugDialog("prompt");
    setDebugPrompt(null);
    setDebugPromptLoading(true);
    setDebugError("");
    try {
      setDebugPrompt(await getDebugPrompt());
    } catch (cause) {
      setDebugError(errorMessage(cause, "无法读取 system prompt"));
    } finally {
      setDebugPromptLoading(false);
    }
  };

  const openDebugRequest = async (id: number) => {
    setDebugDialog("request");
    setDebugDetail(null);
    setDebugDetailLoading(true);
    setDebugError("");
    try {
      setDebugDetail(await getDebugRequest(id));
    } catch (cause) {
      setDebugError(errorMessage(cause, "无法读取请求详情"));
    } finally {
      setDebugDetailLoading(false);
    }
  };

  const clearDebug = async () => {
    if (clearingDebug) return;
    setClearingDebug(true);
    setDebugError("");
    try {
      await clearDebugRequests();
      setDebugRequests((current) => current ? { ...current, items: [] } : current);
      setDebugDetail(null);
      setDebugDialog(null);
      setClearDebugPending(false);
    } catch (cause) {
      setDebugError(errorMessage(cause, "无法清空调试请求"));
    } finally {
      setClearingDebug(false);
    }
  };

  const copyDebugText = async (text: string, target: "prompt" | "payload") => {
    try {
      await navigator.clipboard.writeText(text);
      setDebugCopied(target);
      window.setTimeout(() => setDebugCopied((current) => current === target ? null : current), 1600);
    } catch {
      setDebugError("复制失败，请手动选择文本复制");
    }
  };

  const renderRuntimeFields = (fields: RuntimeSettingField[]) => <div className="runtime-setting-list refined">
    {fields.map((field) => <RuntimeField
      key={field.key}
      field={field}
      value={draftValues[field.key]}
      source={runtime?.sources?.[field.key]}
      providers={runtime?.providers ?? []}
      ttsStatus={ttsStatus}
      asrStatus={asrStatus}
      ttsVoices={ttsVoices}
      ttsVoicesLoading={ttsVoicesLoading}
      disabled={saving}
      pendingReset={pendingResets.has(field.key)}
      onChange={(value) => changeTtsField(field, value)}
      onRestore={() => toggleRuntimeReset(field)}
    />)}
  </div>;

  return <div className="settings-shell">
    <main className="settings-content settings-content-refined">
      {error && <div className="settings-error"><X size={15} /><span>{error}</span><button className="ghost-button" onClick={() => void loadRuntime()} disabled={loading}><RefreshCw size={12} />{t("settings.retry")}</button></div>}
      {runtimeMessage && <div className="settings-success"><Check size={14} />{runtimeMessage}</div>}

      <div className="settings-layout settings-layout-aligned">
        <aside className="settings-section-nav settings-nav-rail" aria-label={t("settings.navLabel")}>
          {settingsSections.map(({ key, icon: Icon }) => <button className={activeSection === key ? "active" : ""} type="button" aria-current={activeSection === key ? "page" : undefined} key={key} onClick={() => setActiveSection(key)}><Icon size={15} /><span><strong>{t(`settings.section.${key}.label`)}</strong><small>{t(`settings.section.${key}.description`)}</small></span><ChevronRight size={13} /></button>)}
        </aside>

        <div className="settings-section-content settings-detail-column">
          {activeSection === "general" && <section className="settings-card settings-panel-card">
            <div className="settings-card-heading"><div><span className="card-kicker">CHAT & DISPLAY</span><h2>聊天与显示</h2><p>消息发送、回答跟随和过程信息都在这里。</p></div><SlidersHorizontal size={17} /></div>
            <SettingsVisualGroup icon={SlidersHorizontal} title="对话行为" description="发送方式与回答跟随" tone="accent">
              <div className="settings-toggle-list">
                <Toggle label={t("settings.general.enter")} description={t("settings.general.enterDescription")} checked={preferences.enterToSend} onChange={(value) => updatePreference("enterToSend", value)} />
                <Toggle label={t("settings.general.scroll")} description={t("settings.general.scrollDescription")} checked={preferences.autoScroll} onChange={(value) => updatePreference("autoScroll", value)} />
              </div>
            </SettingsVisualGroup>
            <SettingsVisualGroup icon={Eye} title="界面信息" description="控制思考、工具活动和 token 用量的呈现" tone="neutral">
              <div className="settings-toggle-list">
                <Toggle label={t("settings.general.thinking")} description={t("settings.general.thinkingDescription")} checked={preferences.showThinking} onChange={(value) => updatePreference("showThinking", value)} />
                <Toggle label={t("settings.general.tools")} description={t("settings.general.toolsDescription")} checked={preferences.showToolActivity} onChange={(value) => updatePreference("showToolActivity", value)} />
                <Toggle label={t("settings.general.usage")} description={t("settings.general.usageDescription")} checked={preferences.showUsage} onChange={(value) => updatePreference("showUsage", value)} />
              </div>
            </SettingsVisualGroup>
            <div className="settings-card-actions"><span>{t("settings.general.saved")}</span><button className="ghost-button" onClick={() => { setPreferences(defaultPreferences); writePreferences(defaultPreferences); }}>{t("settings.general.reset")}</button></div>
          </section>}

          {activeSection === "assistant" && <section className="settings-card settings-panel-card">
            <div className="settings-card-heading"><div><span className="card-kicker">ASSISTANT</span><h2>助手规则</h2><p>设置称呼，以及助手每次对话都应遵循的工作方式。</p></div><UserRound size={17} /></div>
            <SettingsVisualGroup icon={UserRound} title="称呼与固定指令" description="长期有效的回答规则，不用于保存事实和计划" tone="violet" action={<button className="ghost-button" type="button" onClick={() => void openDebugPrompt()} disabled={debugPromptLoading}><Eye size={13} />{debugPromptLoading ? "读取中…" : "查看 Prompt"}</button>}>
              {loading ? <div className="settings-loading"><RefreshCw size={15} className="spin" />读取设置…</div> : promptFields.length ? renderRuntimeFields(promptFields) : <div className="settings-empty">当前后端没有提供人格配置。</div>}
              <div className="prompt-boundary-note">事实、偏好和计划请交给长期记忆。</div>
            </SettingsVisualGroup>
          </section>}

          {activeSection === "model" && <section className="settings-card settings-panel-card">
            <div className="settings-card-heading"><div><span className="card-kicker">MODEL</span><h2>模型与回答</h2><p>选择日常对话模型和思考方式。</p></div><Activity size={17} /></div>
            {loading ? <div className="settings-loading"><RefreshCw size={15} className="spin" />读取模型设置…</div> : modelFields.length ? <>
              <SettingsVisualGroup icon={BrainCircuit} title="日常模型" description="模型厂商、具体模型与默认推理方式" tone="accent">{modelPrimaryFields.length ? renderRuntimeFields(modelPrimaryFields) : <div className="settings-empty">当前没有日常模型字段。</div>}</SettingsVisualGroup>
              <SettingsVisualGroup icon={Gauge} title="回答与工具限制" description="输出上限、标题模型和最大工具次数" tone="warm">{modelAdvancedFields.length ? renderRuntimeFields(modelAdvancedFields) : <div className="settings-empty">当前没有回答限制字段。</div>}</SettingsVisualGroup>
            </> : <div className="settings-empty">当前后端没有提供模型配置。</div>}
          </section>}

          {activeSection === "review" && <section className="settings-card settings-panel-card">
            <div className="settings-card-heading"><div><span className="card-kicker">MEMORY REVIEW</span><h2>记忆整理</h2><p>管理每日整理的时间和使用模型。</p></div><Link className="ghost-button" href="/review" onClick={(event) => { if (!confirmAppNavigation()) event.preventDefault(); }}>每日回顾<ChevronRight size={13} /></Link></div>
            {loading ? <div className="settings-loading"><RefreshCw size={15} className="spin" />读取整理设置…</div> : reviewFields.length ? <>
              <SettingsVisualGroup icon={CalendarClock} title="自动整理" description="选择是否自动运行及每天的执行时间" tone="accent">{renderRuntimeFields(reviewPrimaryFields)}</SettingsVisualGroup>
              <SettingsVisualGroup icon={BrainCircuit} title="整理模型" description="留空时沿用日常聊天模型" tone="violet">{reviewAdvancedFields.length ? renderRuntimeFields(reviewAdvancedFields) : <div className="settings-empty">当前没有独立的整理模型配置。</div>}</SettingsVisualGroup>
            </> : <div className="settings-empty">当前后端没有提供整理配置。</div>}
            <div className="settings-card-callout"><Clock3 size={14} /><span>关闭自动整理后，仍可在每日回顾页按需整理。</span></div>
          </section>}

          {activeSection === "notify" && <section className="settings-card settings-panel-card">
            <div className="settings-card-heading"><div><span className="card-kicker">NOTIFICATIONS</span><h2>手机提醒</h2><p>设置推送通道、提醒提前量和每日简报。</p></div><div className="notify-section-tools"><div className={`tts-status-badge ${notifyStatus?.ready ? "success" : "neutral"}`}><span className="tts-status-dot" />{notifyStatus?.ready ? t("settings.notify.channelReady") : t("settings.notify.channelMissing")}</div><button className="ghost-button" type="button" onClick={() => void runNotifyTest()} disabled={notifyTesting || !notifyStatus?.ready}><Send size={13} />{notifyTesting ? t("settings.notify.testing") : t("settings.notify.test")}</button></div></div>
            {loading ? <div className="settings-loading"><RefreshCw size={15} className="spin" />读取通知设置…</div> : notifyFields.length ? <>
              <SettingsVisualGroup icon={Smartphone} title="推送通道" description="配置手机怎么收到提醒，测通之后再打开总开关" tone="accent">
                <div className="notify-channel-list">{(notifyStatus?.channels ?? []).map((channel) => <div className={`notify-channel-row ${channel.configured ? "ready" : ""}`} key={channel.name}><span className="notify-channel-dot" /><div><strong>{channel.name}</strong><small>{channel.configured ? t("settings.notify.channelReady") : channel.reason || t("settings.notify.channelMissing")}</small></div><span className="notify-channel-flag">{channel.enabled ? "已启用" : "未启用"}</span></div>)}</div>
                {renderRuntimeFields(notifyChannelFields)}
                {notifyMessage && <div className="settings-card-callout"><BellRing size={14} /><span>{notifyMessage}</span></div>}
                <div className="settings-card-callout"><Smartphone size={14} /><span>手机上安装 Bark 后打开 App 即可看到设备 key。通知跳转地址要填手机访问得到的局域网地址，localhost 点不开。</span></div>
              </SettingsVisualGroup>
              <SettingsVisualGroup icon={CalendarClock} title="提醒时机" description="提前多久叫你、每天几点给简报" tone="violet">
                {renderRuntimeFields(notifyTimingFields)}
                <div className="settings-card-callout"><Clock3 size={14} /><span>提前量按类型内置：会议 15 分钟、出行和生日提前一天、截止日期提前三天。单条事项可以在时间线里单独改。</span></div>
              </SettingsVisualGroup>
            </> : <div className="settings-empty">当前后端没有提供通知配置。</div>}
            <div className="notify-recent"><div className="notify-recent-heading"><strong>{t("settings.notify.recent")}</strong><button className="icon-button neutral-hover" type="button" aria-label={t("settings.notify.recent")} onClick={() => void refreshNotifyStatus()}><RefreshCw size={13} /></button></div>{notifyStatus?.recent.length ? <div className="notify-recent-list">{notifyStatus.recent.map((row) => <div className={`notify-recent-row ${row.delivered_at ? "ok" : "failed"}`} key={row.id}><span className="notify-recent-main"><strong>{row.title}</strong><small>{row.body.split("\n")[0]}</small></span><span className="notify-recent-meta"><span>{row.delivered_at ? `${t("settings.notify.delivered")} · ${row.channels}` : `${t("settings.notify.failed")} · ${row.attempts}`}</span><span>{debugTime(row.created_at)}</span></span>{row.error && <span className="notify-recent-error">{row.error}</span>}</div>)}</div> : <div className="debug-empty"><BellRing size={16} /><strong>{t("settings.notify.noRecent")}</strong><span>配好通道后发一条测试通知试试。</span></div>}</div>
          </section>}

          {activeSection === "voice" && <section className="settings-card settings-panel-card">
            <div className="settings-card-heading"><div><span className="card-kicker">VOICE PLAYBACK</span><h2>语音朗读</h2><p>设置回答是否朗读，以及模型、音色和播放质量。</p></div><div className="tts-section-tools"><div className={`tts-status-badge ${ttsPresentation.tone}`} title={ttsStatus?.detail || undefined}><span className="tts-status-dot" />{ttsPresentation.label}</div>{ttsMode !== "off" && <button className="ghost-button tts-preview-button" type="button" onClick={() => void runTtsPreview()} disabled={ttsPreviewLoading || ttsStatusLoading || !ttsStatus || ttsStatus.mode === "off" || !ttsStatus.enabled}><Headphones size={13} />{ttsPreviewLoading ? "试听中…" : "试听"}</button>}</div></div>
            {loading ? <div className="settings-loading"><RefreshCw size={15} className="spin" />读取语音设置…</div> : ttsFields.length ? <>
              <SettingsVisualGroup icon={Volume2} title="播放方式" description="关闭、手动播放或回答后自动朗读" tone="accent">
                {renderRuntimeFields(ttsModeFields)}
                {ttsMode === "off" && <div className="settings-card-callout"><Headphones size={14} /><span>当前不会朗读回答；模型和音色仍可提前配置。</span></div>}
              </SettingsVisualGroup>
              <SettingsVisualGroup icon={AudioLines} title="声音与表达" description="选择模型、音色、语气和语速" tone="violet">
                <div className="tts-model-overview"><div><span>本地缓存</span><strong>{ttsStatus?.cached_models?.length ?? 0} 个模型</strong></div><div><span>当前选择</span><strong title={selectedTtsModel}>{selectedTtsModel.split("/").at(-1) || "—"}</strong></div><div><span>状态</span><strong className={selectedModelLoaded ? "online" : selectedCachedModel ? "cached" : ""}>{selectedModelLoaded ? "已加载" : selectedCachedModel ? `已缓存 · ${formatModelSize(selectedCachedModel.size_bytes)}` : "未检测到缓存"}</strong></div></div>
                {renderRuntimeFields(ttsPrimaryFields)}
              </SettingsVisualGroup>
              <SettingsVisualGroup icon={Gauge} title="合成与播放" description="语种、格式、流式传输及性能限制" tone="warm">{ttsAdvancedFields.length ? renderRuntimeFields(ttsAdvancedFields) : <div className="settings-empty">当前没有高级合成配置。</div>}</SettingsVisualGroup>
            </> : <div className="settings-empty">当前后端没有提供语音配置。</div>}
            {(ttsStatusError || ttsStatus?.detail || ttsPreviewMessage) && <div className="tts-status-detail">{ttsStatusError || ttsStatus?.detail || ttsPreviewMessage}<button className="icon-button" type="button" aria-label="刷新语音服务状态" title="刷新状态" onClick={() => void refreshTtsStatus()} disabled={ttsStatusLoading}><RefreshCw size={12} className={ttsStatusLoading ? "spin" : ""} /></button></div>}
            <audio ref={previewAudioRef} className="tts-audio" onEnded={() => setTtsPreviewMessage("")} />
          </section>}

          {activeSection === "voiceInput" && <section className="settings-card settings-panel-card">
            <div className="settings-card-heading"><div><span className="card-kicker">VOICE INPUT</span><h2>语音输入</h2><p>设置录音转文字所使用的识别模型和语言。</p></div><Mic2 size={17} /></div>
            <SettingsVisualGroup icon={Mic2} title="识别设置" description={asrStatusLoading ? "正在检查识别服务" : !asrStatus?.reachable ? "识别服务离线" : asrStatus.loaded ? "识别模型已加载" : "录音时按需加载模型"} tone={asrStatus?.reachable ? "success" : "neutral"} className="settings-voice-input-group">
              {loading ? <div className="settings-loading"><RefreshCw size={15} className="spin" />读取识别设置…</div> : asrFields.length ? <>
                <div className="tts-model-overview"><div><span>本地缓存</span><strong>{asrStatus?.cached_models.length ?? 0} 个模型</strong></div><div><span>当前选择</span><strong title={selectedAsrModel}>{selectedAsrModel.split("/").at(-1) || "—"}</strong></div><div><span>状态</span><strong className={selectedAsrModelLoaded ? "online" : selectedCachedAsrModel ? "cached" : ""}>{selectedAsrModelLoaded ? "已加载" : selectedCachedAsrModel ? `已缓存 · ${formatModelSize(selectedCachedAsrModel.size_bytes)}` : "未检测到缓存"}</strong></div></div>
                {renderRuntimeFields(asrFields)}
              </> : <div className="settings-empty">当前后端没有提供语音识别配置。</div>}
              {(asrStatusError || asrStatus?.detail) && <div className="tts-status-detail">{asrStatusError || asrStatus?.detail}<button className="icon-button" type="button" aria-label="刷新语音识别状态" onClick={() => void refreshAsrStatus()} disabled={asrStatusLoading}><RefreshCw size={12} className={asrStatusLoading ? "spin" : ""} /></button></div>}
            </SettingsVisualGroup>
          </section>}

          {activeSection === "system" && <section className="settings-card settings-panel-card">
            <div className="settings-card-heading"><div><span className="card-kicker">DATA & BACKUP</span><h2>数据与备份</h2><p>查看连接状态、创建快照并确认运行环境。</p></div><HardDriveDownload size={17} /></div>
            <SettingsVisualGroup icon={HardDriveDownload} title="连接与备份" description="查看当前服务状态并创建数据快照" tone="success">
              <div className="settings-summary-strip"><div><span>连接</span><strong className={health?.status === "ok" ? "value-success" : ""}>{connectionLabel}</strong></div><div><span>当前模型</span><strong>{runtime?.model ?? "—"}</strong></div><div><span>知识库</span><strong>{runtime ? runtime.kb_enabled ? "已挂载" : "未启用" : "—"}</strong></div></div>
              <div className="settings-system-actions"><div><strong>数据备份</strong><span>创建数据库和长期记忆的当前快照。</span></div><button className="ghost-button" onClick={() => void runBackup()} disabled={backupLoading}><HardDriveDownload size={13} />{backupLoading ? "备份中…" : "创建备份"}</button></div>
              {backup && <div className="settings-backup-result"><Download size={14} /><span>备份完成：{backup.dump_file} · {backup.memory_files} 个记忆文件 · {Math.round(backup.dump_bytes / 1024)} KB</span></div>}
            </SettingsVisualGroup>
            <SettingsVisualGroup icon={ServerCog} title="运行与环境" description="服务地址、Provider 和需要重启的环境变量" tone="neutral" className="settings-runtime-group">
              <div className="settings-values">{loading ? <div className="settings-loading"><RefreshCw size={15} className="spin" />读取运行状态…</div> : <><SettingValue label="服务地址" value={apiBase} /><SettingValue label="Provider" value={runtime?.provider ?? "—"} /><SettingValue label="默认思考" value={thinkingDefault} /><SettingValue label="会话级开关" value={runtime ? runtime.thinking_toggle ? "可用" : "不可用" : "—"} /></>}</div>
              {runtime?.env_only?.length ? <div className="settings-env-only"><div className="settings-env-only-heading"><span className="settings-scope-badge">仅环境变量</span><strong>修改后需要重启后端</strong></div><div className="settings-env-only-list">{runtime.env_only.map((key) => <code key={key}>{key}</code>)}</div></div> : null}
            </SettingsVisualGroup>
          </section>}

          {activeSection === "advanced" && <section className="settings-card settings-panel-card">
            <div className="settings-card-heading"><div><span className="card-kicker">DEVELOPER</span><h2>开发者</h2><p>只在排查模型工具和请求问题时使用。</p></div><Bug size={17} /></div>
            <SettingsVisualGroup icon={Bug} title="开发者选项" description="工具定义与可能包含对话原文的请求调试" tone="warm" className="settings-developer-group">
              <div className="settings-developer-stack">
                <details className="tts-advanced-settings settings-disclosure"><summary><span><strong>工具目录</strong><small>查看模型可用能力和参数约定</small></span><ChevronRight size={14} /></summary><div className="tool-catalog-card"><ToolCatalog /></div></details>
                <details className="tts-advanced-settings settings-disclosure settings-danger-zone">
                  <summary><span><strong>请求调试</strong><small>仅排查问题时开启；快照可能包含完整对话</small></span><ChevronRight size={14} /></summary>
                  {debugFields.length ? renderRuntimeFields(debugFields) : <div className="settings-empty">当前后端没有提供调试配置。</div>}
                  <div className="debug-retention-note"><Bug size={13} /><span>仅在后端内存保留最近 {debugRequests?.capacity ?? 20} 次请求，重启后自动清空。</span></div>
                  <div className="debug-request-panel"><div className="debug-request-panel-heading"><div><strong>最近请求</strong><span>{debugRequests?.enabled ? `${debugRequests.items.length} / ${debugRequests.capacity} 条` : "当前未记录"}</span></div><div><button className="icon-button neutral-hover" type="button" aria-label="刷新调试请求" title="刷新请求列表" onClick={() => void refreshDebugRequests()} disabled={debugRequestsLoading}><RefreshCw size={13} className={debugRequestsLoading ? "spin" : ""} /></button>{debugRequests?.enabled && debugRequests.items.length > 0 && <button className="ghost-button danger-button" type="button" onClick={() => setClearDebugPending(true)}><Trash2 size={12} />清空</button>}</div></div>{debugError && <div className="debug-inline-error">{debugError}</div>}{debugRequestsLoading && !debugRequests ? <div className="settings-loading"><RefreshCw size={14} className="spin" />读取请求列表…</div> : !debugRequests?.enabled ? <div className="debug-empty"><Bug size={16} /><strong>调试记录未开启</strong><span>开启并保存后，新请求会显示在这里。</span></div> : debugRequests.items.length === 0 ? <div className="debug-empty"><Clipboard size={16} /><strong>还没有请求快照</strong><span>发送一条消息后再回来查看。</span></div> : <div className="debug-request-list">{debugRequests.items.map((item) => <button className="debug-request-row" type="button" key={item.id} onClick={() => void openDebugRequest(item.id)}><span className="debug-request-row-main"><strong>请求 #{item.id}</strong><span>第 {item.iteration + 1} 次模型请求</span></span><span className="debug-request-row-meta"><span>{item.provider} · {item.model}</span><span>{item.conversation_id ? `会话 #${item.conversation_id}` : "无会话"} · {debugTime(item.at)}</span></span><span className="debug-request-row-stats"><span>{item.messages} messages</span><span>{item.tools} tools</span><span>{item.seconds.toFixed(2)}s</span></span>{item.error && <span className="debug-request-error">{item.error}</span>}<ChevronRight size={14} /></button>)}</div>}</div>
                </details>
              </div>
            </SettingsVisualGroup>
          </section>}
        </div>
      </div>

      {changedKeys.length > 0 && <div className="settings-savebar" role="status"><div><strong>{changedKeys.length} 项更改尚未保存</strong><span>切换分类不会丢失当前修改</span></div><div><button className="ghost-button" type="button" onClick={discardRuntimeChanges} disabled={saving}>放弃更改</button><button className="primary-button" type="button" onClick={() => void saveRuntime()} disabled={saving}><Save size={13} />{saving ? "保存中…" : "保存更改"}</button></div></div>}
    </main>
    {debugDialog && <DebugDialog kind={debugDialog} prompt={debugPrompt} request={debugDetail} loading={debugDialog === "prompt" ? debugPromptLoading : debugDetailLoading} error={debugError} copied={debugCopied} onClose={() => setDebugDialog(null)} onCopy={(text, target) => void copyDebugText(text, target)} />}
    <ConfirmDialog open={clearDebugPending} title="清空调试请求？" description="最近的模型请求快照会从后端内存中全部移除，服务重启后本来也会自动清空。" confirmLabel="清空请求" busy={clearingDebug} onCancel={() => setClearDebugPending(false)} onConfirm={() => void clearDebug()} />
  </div>;
}
