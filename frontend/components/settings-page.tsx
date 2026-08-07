"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { Activity, BookOpen, Bug, Check, ChevronRight, Clipboard, Clock3, Copy, Download, Eye, HardDriveDownload, Headphones, RefreshCw, RotateCcw, Save, Settings2, SlidersHorizontal, Trash2, X } from "lucide-react";
import { apiBaseLabel, clearDebugRequests, createBackup, errorMessage, getAsrStatus, getDebugPrompt, getDebugRequest, getHealth, getRuntimeSettings, getTtsStatus, getTtsVoices, listDebugRequests, synthesizeSpeech, updateRuntimeSettings, warmupSpeech } from "@/lib/api";
import { defaultPreferences, preferencesChangeEvent, readPreferences, writePreferences, type UserPreferences } from "@/lib/preferences";
import type { AsrStatus, BackupResult, DebugPrompt, DebugRequestDetail, DebugRequestList, HealthStatus, RuntimeSettingField, RuntimeSettings, TtsStatus } from "@/lib/types";
import { confirmAppNavigation, useNavigationGuard } from "@/lib/navigation-guard";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { ToolCatalog } from "@/components/tool-catalog";
import { useI18n } from "@/components/i18n-provider";

type SettingsSectionKey = "general" | "model" | "review" | "voice" | "system";

const settingsSections: Array<{ key: SettingsSectionKey; icon: typeof Settings2 }> = [
  { key: "general", icon: SlidersHorizontal },
  { key: "model", icon: Activity },
  { key: "review", icon: Clock3 },
  { key: "voice", icon: Headphones },
  { key: "system", icon: HardDriveDownload },
];

const reviewFieldKeys = new Set(["consolidate_model", "consolidate_auto", "consolidate_hour"]);
const modelPrimaryFieldKeys = new Set(["provider", "model", "deepseek_model", "effort", "deepseek_thinking"]);
const ttsPrimaryFieldKeys = new Set(["tts_mode", "tts_model", "tts_voice", "tts_instruct", "tts_speed_percent"]);

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

function RuntimeField({ field, value, source, providers, ttsStatus, asrStatus, ttsVoices, ttsVoicesLoading, disabled, pendingReset = false, onChange, onRestore }: { field: RuntimeSettingField; value: unknown; source?: "db" | "env"; providers: RuntimeSettings["providers"]; ttsStatus?: TtsStatus | null; asrStatus?: AsrStatus | null; ttsVoices: string[]; ttsVoicesLoading: boolean; disabled: boolean; pendingReset?: boolean; onChange: (value: unknown) => void; onRestore: () => void }) {
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
      <span className={`runtime-source ${source === "db" ? "modified" : ""} ${pendingReset ? "pending" : ""}`}>{pendingReset ? "待恢复默认" : source === "db" ? "已覆盖默认" : "环境默认"}</span>
      {source === "db" && <button className="icon-button runtime-restore" type="button" aria-label={pendingReset ? `取消恢复${field.label}` : `恢复${field.label}默认值`} title={pendingReset ? "取消恢复" : "恢复环境默认"} disabled={disabled} onClick={onRestore}><RotateCcw size={12} /></button>}
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
  const reviewAutoEnabled = draftValues.consolidate_auto === true;
  const reviewPrimaryFields = useMemo(() => reviewFields.filter((field) => field.key === "consolidate_auto" || (field.key === "consolidate_hour" && reviewAutoEnabled)), [reviewAutoEnabled, reviewFields]);
  const reviewAdvancedFields = useMemo(() => reviewFields.filter((field) => field.key === "consolidate_model"), [reviewFields]);
  const promptFields = useMemo(() => activeFields.filter((field) => field.group === "prompt"), [activeFields]);
  const ttsFields = useMemo(() => activeFields.filter((field) => field.group === "tts"), [activeFields]);
  const asrFields = useMemo(() => activeFields.filter((field) => field.group === "asr"), [activeFields]);
  const ttsModeFields = useMemo(() => ttsFields.filter((field) => field.key === "tts_mode"), [ttsFields]);
  const ttsPrimaryFields = useMemo(() => ttsFields.filter((field) => field.key !== "tts_mode" && ttsPrimaryFieldKeys.has(field.key)), [ttsFields]);
  const ttsAdvancedFields = useMemo(() => ttsFields.filter((field) => !ttsPrimaryFieldKeys.has(field.key)), [ttsFields]);
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
      <header className="settings-heading settings-heading-refined">
        <div><div className="eyebrow">{t("settings.eyebrow")}</div><h1>{t("settings.title")}</h1><p>{t("settings.description")}</p></div>
        <div className="settings-heading-meta" aria-label="运行状态">
          <span className={`status-dot ${health?.status === "ok" ? "online" : ""}`} />
          <span>{health?.status === "ok" ? t("settings.service.ok") : t("settings.service.unknown")}</span>
          <span aria-hidden="true">·</span>
          <BookOpen size={13} />
          <span title={runtime?.kb_enabled ? t("settings.kb.enabledTitle") : t("settings.kb.disabledTitle")}>{loading ? t("settings.kb.checking") : runtime?.kb_enabled ? t("settings.kb.enabled") : t("settings.kb.disabled")}</span>
        </div>
      </header>
      {error && <div className="settings-error"><X size={15} /><span>{error}</span><button className="ghost-button" onClick={() => void loadRuntime()} disabled={loading}><RefreshCw size={12} />{t("settings.retry")}</button></div>}
      {runtimeMessage && <div className="settings-success"><Check size={14} />{runtimeMessage}</div>}

      <div className="settings-layout">
        <aside className="settings-section-nav" aria-label={t("settings.navLabel")}>
          {settingsSections.map(({ key, icon: Icon }) => <button className={activeSection === key ? "active" : ""} type="button" key={key} onClick={() => setActiveSection(key)}><Icon size={15} /><span><strong>{t(`settings.section.${key}.label`)}</strong><small>{t(`settings.section.${key}.description`)}</small></span><ChevronRight size={13} /></button>)}
        </aside>

        <div className="settings-section-content">
          {activeSection === "general" && <section className="settings-card settings-panel-card">
            <div className="settings-card-heading"><div><span className="card-kicker">GENERAL</span><h2>聊天与个性化</h2><p>常用交互偏好保存在当前浏览器。</p></div><SlidersHorizontal size={17} /></div>
            <div className="settings-toggle-list settings-compact-section">
              <Toggle label={t("settings.general.enter")} description={t("settings.general.enterDescription")} checked={preferences.enterToSend} onChange={(value) => updatePreference("enterToSend", value)} />
              <Toggle label={t("settings.general.scroll")} description={t("settings.general.scrollDescription")} checked={preferences.autoScroll} onChange={(value) => updatePreference("autoScroll", value)} />
            </div>
            <details className="tts-advanced-settings settings-disclosure">
              <summary><span><strong>界面信息</strong><small>思考、工具活动和 token 用量</small></span><ChevronRight size={14} /></summary>
              <div className="settings-toggle-list">
                <Toggle label={t("settings.general.thinking")} description={t("settings.general.thinkingDescription")} checked={preferences.showThinking} onChange={(value) => updatePreference("showThinking", value)} />
                <Toggle label={t("settings.general.tools")} description={t("settings.general.toolsDescription")} checked={preferences.showToolActivity} onChange={(value) => updatePreference("showToolActivity", value)} />
                <Toggle label={t("settings.general.usage")} description={t("settings.general.usageDescription")} checked={preferences.showUsage} onChange={(value) => updatePreference("showUsage", value)} />
              </div>
            </details>
            <details className="tts-advanced-settings settings-disclosure">
              <summary><span><strong>助手称呼与工作方式</strong><small>需要时再设定长期遵循的回答规则</small></span><ChevronRight size={14} /></summary>
              {loading ? <div className="settings-loading"><RefreshCw size={15} className="spin" />读取设置…</div> : promptFields.length ? renderRuntimeFields(promptFields) : <div className="settings-empty">当前后端没有提供人格配置。</div>}
              <div className="settings-card-actions settings-disclosure-actions"><span>事实、偏好和计划请交给长期记忆。</span><button className="ghost-button" type="button" onClick={() => void openDebugPrompt()} disabled={debugPromptLoading}><Eye size={13} />{debugPromptLoading ? "读取中…" : "查看 Prompt"}</button></div>
            </details>
            <div className="settings-card-actions"><span>{t("settings.general.saved")}</span><button className="ghost-button" onClick={() => { setPreferences(defaultPreferences); writePreferences(defaultPreferences); }}>{t("settings.general.reset")}</button></div>
          </section>}

          {activeSection === "model" && <section className="settings-card settings-panel-card">
            <div className="settings-card-heading"><div><span className="card-kicker">MODEL</span><h2>模型与回答</h2><p>选择日常对话模型和思考方式。</p></div><Activity size={17} /></div>
            {loading ? <div className="settings-loading"><RefreshCw size={15} className="spin" />读取模型设置…</div> : modelFields.length ? <>
              {modelPrimaryFields.length > 0 && renderRuntimeFields(modelPrimaryFields)}
              {modelAdvancedFields.length > 0 && <details className="tts-advanced-settings settings-disclosure"><summary><span><strong>回答与工具限制</strong><small>输出上限、标题模型和最大工具次数</small></span><ChevronRight size={14} /></summary>{renderRuntimeFields(modelAdvancedFields)}</details>}
            </> : <div className="settings-empty">当前后端没有提供模型配置。</div>}
          </section>}

          {activeSection === "review" && <section className="settings-card settings-panel-card">
            <div className="settings-card-heading"><div><span className="card-kicker">MEMORY & REVIEW</span><h2>记忆与每日回顾</h2><p>决定是否自动整理，以及每天何时运行。</p></div><Link className="ghost-button" href="/review" onClick={(event) => { if (!confirmAppNavigation()) event.preventDefault(); }}>每日回顾<ChevronRight size={13} /></Link></div>
            {loading ? <div className="settings-loading"><RefreshCw size={15} className="spin" />读取整理设置…</div> : reviewFields.length ? <>
              {renderRuntimeFields(reviewPrimaryFields)}
              {reviewAdvancedFields.length > 0 && <details className="tts-advanced-settings settings-disclosure"><summary><span><strong>整理模型</strong><small>默认沿用日常聊天模型</small></span><ChevronRight size={14} /></summary>{renderRuntimeFields(reviewAdvancedFields)}</details>}
            </> : <div className="settings-empty">当前后端没有提供整理配置。</div>}
            <div className="settings-card-callout"><Clock3 size={14} /><span>关闭自动整理后，仍可在每日回顾页按需整理。</span></div>
          </section>}

          {activeSection === "voice" && <section className="settings-card settings-panel-card">
            <div className="settings-card-heading"><div><span className="card-kicker">VOICE</span><h2>语音</h2><p>先选择是否朗读；启用后再调整声音。</p></div><div className="tts-section-tools"><div className={`tts-status-badge ${ttsPresentation.tone}`} title={ttsStatus?.detail || undefined}><span className="tts-status-dot" />{ttsPresentation.label}</div>{ttsMode !== "off" && <button className="ghost-button tts-preview-button" type="button" onClick={() => void runTtsPreview()} disabled={ttsPreviewLoading || ttsStatusLoading || !ttsStatus || ttsStatus.mode === "off" || !ttsStatus.enabled}><Headphones size={13} />{ttsPreviewLoading ? "试听中…" : "试听"}</button>}</div></div>
            {loading ? <div className="settings-loading"><RefreshCw size={15} className="spin" />读取语音设置…</div> : ttsFields.length ? <>
              {renderRuntimeFields(ttsModeFields)}
              {ttsMode === "off" ? <div className="settings-card-callout"><Headphones size={14} /><span>语音已关闭。选择手动或自动朗读后，可继续设置模型和音色。</span></div> : <>
                <div className="tts-model-overview"><div><span>本地缓存</span><strong>{ttsStatus?.cached_models?.length ?? 0} 个模型</strong></div><div><span>当前选择</span><strong title={selectedTtsModel}>{selectedTtsModel.split("/").at(-1) || "—"}</strong></div><div><span>状态</span><strong className={selectedModelLoaded ? "online" : selectedCachedModel ? "cached" : ""}>{selectedModelLoaded ? "已加载" : selectedCachedModel ? `已缓存 · ${formatModelSize(selectedCachedModel.size_bytes)}` : "未检测到缓存"}</strong></div></div>
                {renderRuntimeFields(ttsPrimaryFields)}
                {ttsAdvancedFields.length > 0 && <details className="tts-advanced-settings settings-disclosure"><summary><span><strong>合成与播放选项</strong><small>语种、格式、流式传输及性能限制</small></span><ChevronRight size={14} /></summary>{renderRuntimeFields(ttsAdvancedFields)}</details>}
              </>}
            </> : <div className="settings-empty">当前后端没有提供语音配置。</div>}
            {(ttsStatusError || ttsStatus?.detail || ttsPreviewMessage) && <div className="tts-status-detail">{ttsStatusError || ttsStatus?.detail || ttsPreviewMessage}<button className="icon-button" type="button" aria-label="刷新语音服务状态" title="刷新状态" onClick={() => void refreshTtsStatus()} disabled={ttsStatusLoading}><RefreshCw size={12} className={ttsStatusLoading ? "spin" : ""} /></button></div>}
            <details className="tts-advanced-settings settings-disclosure settings-voice-input-disclosure">
              <summary><span><strong>语音输入</strong><small>{asrStatusLoading ? "正在检查识别服务" : !asrStatus?.reachable ? "识别服务离线" : asrStatus.loaded ? "识别模型已加载" : "录音时按需加载模型"}</small></span><ChevronRight size={14} /></summary>
              {loading ? <div className="settings-loading"><RefreshCw size={15} className="spin" />读取识别设置…</div> : asrFields.length ? <>
                <div className="tts-model-overview"><div><span>本地缓存</span><strong>{asrStatus?.cached_models.length ?? 0} 个模型</strong></div><div><span>当前选择</span><strong title={selectedAsrModel}>{selectedAsrModel.split("/").at(-1) || "—"}</strong></div><div><span>状态</span><strong className={selectedAsrModelLoaded ? "online" : selectedCachedAsrModel ? "cached" : ""}>{selectedAsrModelLoaded ? "已加载" : selectedCachedAsrModel ? `已缓存 · ${formatModelSize(selectedCachedAsrModel.size_bytes)}` : "未检测到缓存"}</strong></div></div>
                {renderRuntimeFields(asrFields)}
              </> : <div className="settings-empty">当前后端没有提供语音识别配置。</div>}
              {(asrStatusError || asrStatus?.detail) && <div className="tts-status-detail">{asrStatusError || asrStatus?.detail}<button className="icon-button" type="button" aria-label="刷新语音识别状态" onClick={() => void refreshAsrStatus()} disabled={asrStatusLoading}><RefreshCw size={12} className={asrStatusLoading ? "spin" : ""} /></button></div>}
            </details>
            <audio ref={previewAudioRef} className="tts-audio" onEnded={() => setTtsPreviewMessage("")} />
          </section>}

          {activeSection === "system" && <section className="settings-card settings-panel-card">
            <div className="settings-card-heading"><div><span className="card-kicker">SYSTEM & DATA</span><h2>系统与数据</h2><p>日常只需关注连接和备份；技术信息按需展开。</p></div><Settings2 size={17} /></div>
            <div className="settings-summary-strip"><div><span>连接</span><strong className={health?.status === "ok" ? "value-success" : ""}>{connectionLabel}</strong></div><div><span>当前模型</span><strong>{runtime?.model ?? "—"}</strong></div><div><span>知识库</span><strong>{runtime ? runtime.kb_enabled ? "已挂载" : "未启用" : "—"}</strong></div></div>
            <div className="settings-system-actions"><div><strong>数据备份</strong><span>创建数据库和长期记忆的当前快照。</span></div><button className="ghost-button" onClick={() => void runBackup()} disabled={backupLoading}><HardDriveDownload size={13} />{backupLoading ? "备份中…" : "创建备份"}</button></div>
            {backup && <div className="settings-backup-result"><Download size={14} /><span>备份完成：{backup.dump_file} · {backup.memory_files} 个记忆文件 · {Math.round(backup.dump_bytes / 1024)} KB</span></div>}
            <details className="tts-advanced-settings settings-disclosure">
              <summary><span><strong>运行与环境信息</strong><small>服务地址、Provider 和环境变量配置</small></span><ChevronRight size={14} /></summary>
              <div className="settings-values">{loading ? <div className="settings-loading"><RefreshCw size={15} className="spin" />读取运行状态…</div> : <><SettingValue label="服务地址" value={apiBase} /><SettingValue label="Provider" value={runtime?.provider ?? "—"} /><SettingValue label="默认思考" value={thinkingDefault} /><SettingValue label="会话级开关" value={runtime ? runtime.thinking_toggle ? "可用" : "不可用" : "—"} /></>}</div>
              {runtime?.env_only?.length ? <div className="settings-env-only"><div className="settings-env-only-heading"><span className="settings-scope-badge">仅环境变量</span><strong>修改后需要重启后端</strong></div><div className="settings-env-only-list">{runtime.env_only.map((key) => <code key={key}>{key}</code>)}</div></div> : null}
            </details>
            <details className="tts-advanced-settings settings-disclosure settings-developer-disclosure">
              <summary><span><strong>开发者选项</strong><small>工具定义与可能包含对话原文的请求调试</small></span><ChevronRight size={14} /></summary>
              <div className="settings-developer-stack">
                <details className="tts-advanced-settings settings-disclosure"><summary><span><strong>工具目录</strong><small>查看模型可用能力和参数约定</small></span><ChevronRight size={14} /></summary><div className="tool-catalog-card"><ToolCatalog /></div></details>
                <details className="tts-advanced-settings settings-disclosure settings-danger-zone">
                  <summary><span><strong>请求调试</strong><small>仅排查问题时开启；快照可能包含完整对话</small></span><ChevronRight size={14} /></summary>
                  {debugFields.length ? renderRuntimeFields(debugFields) : <div className="settings-empty">当前后端没有提供调试配置。</div>}
                  <div className="debug-retention-note"><Bug size={13} /><span>仅在后端内存保留最近 {debugRequests?.capacity ?? 20} 次请求，重启后自动清空。</span></div>
                  <div className="debug-request-panel"><div className="debug-request-panel-heading"><div><strong>最近请求</strong><span>{debugRequests?.enabled ? `${debugRequests.items.length} / ${debugRequests.capacity} 条` : "当前未记录"}</span></div><div><button className="icon-button neutral-hover" type="button" aria-label="刷新调试请求" title="刷新请求列表" onClick={() => void refreshDebugRequests()} disabled={debugRequestsLoading}><RefreshCw size={13} className={debugRequestsLoading ? "spin" : ""} /></button>{debugRequests?.enabled && debugRequests.items.length > 0 && <button className="ghost-button danger-button" type="button" onClick={() => setClearDebugPending(true)}><Trash2 size={12} />清空</button>}</div></div>{debugError && <div className="debug-inline-error">{debugError}</div>}{debugRequestsLoading && !debugRequests ? <div className="settings-loading"><RefreshCw size={14} className="spin" />读取请求列表…</div> : !debugRequests?.enabled ? <div className="debug-empty"><Bug size={16} /><strong>调试记录未开启</strong><span>开启并保存后，新请求会显示在这里。</span></div> : debugRequests.items.length === 0 ? <div className="debug-empty"><Clipboard size={16} /><strong>还没有请求快照</strong><span>发送一条消息后再回来查看。</span></div> : <div className="debug-request-list">{debugRequests.items.map((item) => <button className="debug-request-row" type="button" key={item.id} onClick={() => void openDebugRequest(item.id)}><span className="debug-request-row-main"><strong>请求 #{item.id}</strong><span>第 {item.iteration + 1} 次模型请求</span></span><span className="debug-request-row-meta"><span>{item.provider} · {item.model}</span><span>{item.conversation_id ? `会话 #${item.conversation_id}` : "无会话"} · {debugTime(item.at)}</span></span><span className="debug-request-row-stats"><span>{item.messages} messages</span><span>{item.tools} tools</span><span>{item.seconds.toFixed(2)}s</span></span>{item.error && <span className="debug-request-error">{item.error}</span>}<ChevronRight size={14} /></button>)}</div>}</div>
                </details>
              </div>
            </details>
          </section>}
        </div>
      </div>

      {changedKeys.length > 0 && <div className="settings-savebar" role="status"><div><strong>{changedKeys.length} 项更改尚未保存</strong><span>切换分类不会丢失当前修改</span></div><div><button className="ghost-button" type="button" onClick={discardRuntimeChanges} disabled={saving}>放弃更改</button><button className="primary-button" type="button" onClick={() => void saveRuntime()} disabled={saving}><Save size={13} />{saving ? "保存中…" : "保存更改"}</button></div></div>}
    </main>
    {debugDialog && <DebugDialog kind={debugDialog} prompt={debugPrompt} request={debugDetail} loading={debugDialog === "prompt" ? debugPromptLoading : debugDetailLoading} error={debugError} copied={debugCopied} onClose={() => setDebugDialog(null)} onCopy={(text, target) => void copyDebugText(text, target)} />}
    <ConfirmDialog open={clearDebugPending} title="清空调试请求？" description="最近的模型请求快照会从后端内存中全部移除，服务重启后本来也会自动清空。" confirmLabel="清空请求" busy={clearingDebug} onCancel={() => setClearDebugPending(false)} onConfirm={() => void clearDebug()} />
  </div>;
}
