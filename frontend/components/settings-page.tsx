"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { Activity, BookOpen, Bug, Check, ChevronRight, Clipboard, Clock3, Copy, Download, Eye, HardDriveDownload, Headphones, RefreshCw, RotateCcw, Save, Settings2, SlidersHorizontal, Sparkles, Trash2, X } from "lucide-react";
import { apiBaseLabel, clearDebugRequests, createBackup, errorMessage, getDebugPrompt, getDebugRequest, getHealth, getRuntimeSettings, getTtsStatus, listDebugRequests, synthesizeSpeech, updateRuntimeSettings, warmupSpeech } from "@/lib/api";
import { defaultPreferences, preferencesChangeEvent, readPreferences, writePreferences, type UserPreferences } from "@/lib/preferences";
import type { BackupResult, DebugPrompt, DebugRequestDetail, DebugRequestList, HealthStatus, RuntimeSettingField, RuntimeSettings, TtsStatus } from "@/lib/types";
import { confirmAppNavigation, useNavigationGuard } from "@/lib/navigation-guard";
import { ConfirmDialog } from "@/components/confirm-dialog";

type SettingsSectionKey = "general" | "assistant" | "model" | "review" | "voice" | "advanced" | "system";

const settingsSections: Array<{ key: SettingsSectionKey; label: string; description: string; icon: typeof Settings2 }> = [
  { key: "general", label: "通用与聊天", description: "输入与显示偏好", icon: SlidersHorizontal },
  { key: "assistant", label: "助手人格", description: "称呼与固定指令", icon: Sparkles },
  { key: "model", label: "模型与回答", description: "模型、思考与工具", icon: Activity },
  { key: "review", label: "记忆与回顾", description: "每日整理策略", icon: Clock3 },
  { key: "voice", label: "语音", description: "朗读与音色", icon: Headphones },
  { key: "advanced", label: "高级与调试", description: "请求记录与 Prompt", icon: Bug },
  { key: "system", label: "系统与数据", description: "连接、备份与环境", icon: HardDriveDownload },
];

const reviewFieldKeys = new Set(["consolidate_model", "consolidate_auto", "consolidate_hour"]);

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
  consolidate_auto: "按固定时间自动整理当天对话",
  consolidate_hour: "使用后端所在时区的整点时间",
  tts_mode: "关闭、手动播放或回答完成后自动播放",
  tts_model: "本地语音服务加载的模型",
  tts_voice: "语音合成使用的说话人",
  tts_lang_code: "语音合成的主要语言",
  tts_instruct: "控制语气、情绪与表达节奏",
  tts_format: "浏览器接收的音频编码格式",
  tts_stream: "边合成边传输，通常可缩短首段等待",
  tts_speed_percent: "100% 为模型默认语速",
  tts_max_chars: "超过长度时只朗读前面的内容",
  tts_timeout: "语音服务单次请求最长等待时间",
  tts_warmup: "后端启动时预先加载语音模型，缩短首次播放等待",
  debug_prompts: "临时保存最近请求，可能包含完整对话原文",
};

function Toggle({ checked, onChange, label, description }: { checked: boolean; onChange: (checked: boolean) => void; label: string; description: string }) {
  return <label className="settings-toggle-row"><span className="settings-toggle-copy"><strong>{label}</strong><span>{description}</span></span><input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} /><span className="toggle-track" aria-hidden="true"><span /></span></label>;
}

function SettingValue({ label, value, tone = "" }: { label: string; value: string; tone?: string }) {
  return <div className="settings-value"><span>{label}</span><strong className={tone}>{value}</strong></div>;
}

function RuntimeField({ field, value, source, providers, ttsStatus, disabled, pendingReset = false, onChange, onRestore }: { field: RuntimeSettingField; value: unknown; source?: "db" | "env"; providers: RuntimeSettings["providers"]; ttsStatus?: TtsStatus | null; disabled: boolean; pendingReset?: boolean; onChange: (value: unknown) => void; onRestore: () => void }) {
  const stringValue = value === null || value === undefined ? "" : String(value);
  const providerChoices = field.key === "provider" ? providers : [];
  const modelChoices = field.key === "tts_model" && ttsStatus?.models.length ? Array.from(new Set([stringValue, ...ttsStatus.models].filter(Boolean))) : [];
  const choices = field.key === "provider" ? providerChoices.map((item) => item.value) : modelChoices.length ? modelChoices : field.choices;
  const providerReason = field.key === "provider" ? providerChoices.find((item) => item.value === stringValue)?.reason : "";
  const multiline = field.kind === "text" || field.key === "tts_instruct";

  const controlDisabled = disabled || pendingReset;

  return <div className={`runtime-setting-row ${pendingReset ? "pending-reset" : ""}`}>
    <div className="runtime-setting-label"><strong>{field.label}</strong><span>{fieldHelp[field.key] ?? (field.provider ? `仅用于 ${field.provider}` : "保存后立即生效")}</span></div>
    <div className={`runtime-setting-control ${multiline ? "multiline" : ""}`}>
      {field.kind === "bool" && <label className="runtime-checkbox"><input type="checkbox" checked={value === true} disabled={controlDisabled} onChange={(event) => onChange(event.target.checked)} /><span>{value === true ? "开启" : "关闭"}</span></label>}
      {field.kind === "enum" && <select className="runtime-select" value={stringValue} disabled={controlDisabled} onChange={(event) => onChange(event.target.value)}>{choices.map((choice) => { const option = providerChoices.find((item) => item.value === choice); return <option key={choice} value={choice} disabled={option ? !option.available : false}>{choice}{option && !option.available ? "（不可用）" : ""}</option>; })}</select>}
      {field.kind === "int" && <input className="runtime-input" type="number" value={stringValue} min={field.minimum ?? undefined} max={field.maximum ?? undefined} disabled={controlDisabled} onChange={(event) => onChange(event.target.value === "" ? "" : Number(event.target.value))} />}
      {multiline && <textarea className={`runtime-textarea ${field.key === "custom_instructions" ? "runtime-textarea-tall" : ""}`} value={stringValue} maxLength={field.maximum ?? undefined} minLength={field.minimum ?? undefined} disabled={controlDisabled} onChange={(event) => onChange(event.target.value)} placeholder={field.key === "custom_instructions" ? "例如：回答控制在三句话以内，代码优先给 diff。" : "例如：用温柔、自然、亲切的语气说话"} />}
      {field.kind === "str" && field.key !== "tts_instruct" && modelChoices.length === 0 && <input className="runtime-input runtime-input-wide" type="text" value={stringValue} maxLength={field.maximum ?? undefined} minLength={field.minimum ?? undefined} disabled={controlDisabled} onChange={(event) => onChange(event.target.value)} />}
      {field.kind === "str" && modelChoices.length > 0 && <select className="runtime-select" value={stringValue} disabled={controlDisabled} onChange={(event) => onChange(event.target.value)}>{choices.map((choice) => <option key={choice} value={choice}>{choice}</option>)}</select>}
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
      if (becameOnline) void warmupSpeech().catch(() => undefined);
    } catch (cause) {
      ttsReachableRef.current = false;
      setTtsStatusError(errorMessage(cause, "无法读取语音服务状态"));
    } finally {
      setTtsStatusLoading(false);
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
    const handlePreferenceChange = (event: Event) => {
      const detail = (event as CustomEvent<UserPreferences>).detail;
      if (detail) setPreferences(detail);
    };
    window.addEventListener(preferencesChangeEvent(), handlePreferenceChange);
    return () => window.removeEventListener(preferencesChangeEvent(), handlePreferenceChange);
  }, [loadRuntime, refreshTtsStatus]);

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
  const reviewFields = useMemo(() => ungroupedFields.filter((field) => reviewFieldKeys.has(field.key)), [ungroupedFields]);
  const promptFields = useMemo(() => activeFields.filter((field) => field.group === "prompt"), [activeFields]);
  const ttsFields = useMemo(() => activeFields.filter((field) => field.group === "tts"), [activeFields]);
  const debugFields = useMemo(() => activeFields.filter((field) => field.group === "debug"), [activeFields]);
  const changedKeys = useMemo(() => Array.from(new Set([
    ...activeFields.filter((field) => !Object.is(draftValues[field.key], runtime?.values?.[field.key])).map((field) => field.key),
    ...pendingResets,
  ])), [activeFields, draftValues, pendingResets, runtime]);
  useNavigationGuard(changedKeys.length > 0, "设置页有尚未保存的修改，确定放弃并离开吗？");
  const values = runtime?.values ?? {};
  const consolidationAuto = typeof values.consolidate_auto === "boolean" ? values.consolidate_auto : undefined;
  const consolidationHour = typeof values.consolidate_hour === "number" ? values.consolidate_hour : undefined;
  const consolidationMode = consolidationAuto === undefined ? "后端配置" : consolidationAuto ? "自动整理" : "手动触发";
  const consolidationSchedule = consolidationAuto === undefined ? "后端配置" : consolidationAuto ? `${String(consolidationHour ?? 4).padStart(2, "0")}:00` : "按需触发";
  const consolidationModel = typeof values.consolidate_model === "string" && values.consolidate_model ? values.consolidate_model : runtime?.model ?? "—";
  const apiBase = apiBaseLabel();
  const connectionLabel = health?.status === "ok" ? "已连接" : health ? health.status : "未知";
  const thinkingDefault = runtime ? runtime.thinking_default ? "开启" : "关闭" : "—";
  const ttsMode = draftValues.tts_mode === "manual" || draftValues.tts_mode === "auto" ? draftValues.tts_mode : "off";
  const ttsPresentation = ttsStatusPresentation(ttsStatus, ttsStatusLoading);

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
      disabled={saving}
      pendingReset={pendingResets.has(field.key)}
      onChange={(value) => changeRuntimeField(field, value)}
      onRestore={() => toggleRuntimeReset(field)}
    />)}
  </div>;

  return <div className="settings-shell">
    <main className="settings-content settings-content-refined">
      <header className="settings-heading settings-heading-refined">
        <div><div className="eyebrow">Settings</div><h1>设置</h1><p>按用途管理助手。主题可随时从右上角快速切换。</p></div>
        <div className="settings-header-status">
          <div className={`settings-connection-chip ${health?.status === "ok" ? "online" : ""}`}><span className={`status-dot ${health?.status === "ok" ? "online" : ""}`} /><div><strong>{health?.status === "ok" ? "服务正常" : "服务状态未知"}</strong><span>{health?.provider && health?.model ? `${health.provider} · ${health.model}` : apiBase}</span></div></div>
          <div className={`settings-knowledge-chip ${runtime?.kb_enabled ? "enabled" : ""}`} title={runtime?.kb_enabled ? "只读 Obsidian 知识库已挂载" : "在 .env 设置 VAULT_PATH 后重启容器"}>
            <BookOpen size={15} />
            <div><strong>{loading ? "知识库检测中" : runtime?.kb_enabled ? "知识库已挂载" : "知识库未启用"}</strong><span>{runtime?.kb_enabled ? "只读 Obsidian vault" : "设置 VAULT_PATH 后启用"}</span></div>
          </div>
        </div>
      </header>
      {error && <div className="settings-error"><X size={15} /><span>{error}</span><button className="ghost-button" onClick={() => void loadRuntime()} disabled={loading}><RefreshCw size={12} />重试</button></div>}
      {runtimeMessage && <div className="settings-success"><Check size={14} />{runtimeMessage}</div>}

      <div className="settings-layout">
        <aside className="settings-section-nav" aria-label="设置分类">
          {settingsSections.map(({ key, label, description, icon: Icon }) => <button className={activeSection === key ? "active" : ""} type="button" key={key} onClick={() => setActiveSection(key)}><Icon size={15} /><span><strong>{label}</strong><small>{description}</small></span><ChevronRight size={13} /></button>)}
        </aside>

        <div className="settings-section-content">
          {activeSection === "general" && <section className="settings-card settings-panel-card"><div className="settings-card-heading"><div><span className="card-kicker">GENERAL</span><h2>通用与聊天</h2><p>这些偏好只保存在当前浏览器，修改后立即生效。</p></div><SlidersHorizontal size={17} /></div><div className="settings-toggle-list"><Toggle label="Enter 发送" description="按 Enter 发送消息，Shift + Enter 换行。" checked={preferences.enterToSend} onChange={(value) => updatePreference("enterToSend", value)} /><Toggle label="自动跟随新回答" description="流式回答时自动滚动到底部；手动上滑后暂停跟随。" checked={preferences.autoScroll} onChange={(value) => updatePreference("autoScroll", value)} /><Toggle label="显示思考过程" description="思考内容默认折叠，需要时再展开。" checked={preferences.showThinking} onChange={(value) => updatePreference("showThinking", value)} /><Toggle label="显示记忆操作" description="显示助手读取、更新和删除记忆的简洁状态。" checked={preferences.showToolActivity} onChange={(value) => updatePreference("showToolActivity", value)} /><Toggle label="显示 token 用量" description="在已完成回答下显示输出 token 数。" checked={preferences.showUsage} onChange={(value) => updatePreference("showUsage", value)} /></div><div className="settings-card-actions"><span>浏览器配置会自动保存</span><button className="ghost-button" onClick={() => { setPreferences(defaultPreferences); writePreferences(defaultPreferences); }}>恢复默认</button></div></section>}

          {activeSection === "assistant" && <section className="settings-card settings-panel-card"><div className="settings-card-heading"><div><span className="card-kicker">ASSISTANT</span><h2>助手人格</h2><p>固定称呼和工作方式会加入每次模型请求，但不会被每日整理修改。</p></div><button className="ghost-button" type="button" onClick={() => void openDebugPrompt()} disabled={debugPromptLoading}><Eye size={13} />{debugPromptLoading ? "读取中…" : "查看完整 Prompt"}</button></div>{loading ? <div className="settings-loading"><RefreshCw size={15} className="spin" />读取设置…</div> : promptFields.length ? renderRuntimeFields(promptFields) : <div className="settings-empty">当前后端没有提供人格配置。</div>}<div className="prompt-boundary-note">固定指令适合约束回答方式；姓名、偏好和计划等事实应交给长期记忆。</div></section>}

          {activeSection === "model" && <section className="settings-card settings-panel-card"><div className="settings-card-heading"><div><span className="card-kicker">MODEL</span><h2>模型与回答</h2><p>控制日常对话模型、思考方式、输出长度和工具轮次。</p></div><Activity size={17} /></div>{loading ? <div className="settings-loading"><RefreshCw size={15} className="spin" />读取模型设置…</div> : modelFields.length ? renderRuntimeFields(modelFields) : <div className="settings-empty">当前后端没有提供模型配置。</div>}</section>}

          {activeSection === "review" && <section className="settings-card settings-panel-card"><div className="settings-card-heading"><div><span className="card-kicker">MEMORY & REVIEW</span><h2>记忆与每日回顾</h2><p>设置每日整理的触发方式、时间和专用模型。</p></div><Link className="ghost-button" href="/review" onClick={(event) => { if (!confirmAppNavigation()) event.preventDefault(); }}>打开每日回顾<ChevronRight size={13} /></Link></div><div className="settings-summary-strip"><div><span>整理方式</span><strong>{consolidationMode}</strong></div><div><span>整理时间</span><strong>{consolidationSchedule}</strong></div><div><span>整理模型</span><strong>{consolidationModel}</strong></div></div>{loading ? <div className="settings-loading"><RefreshCw size={15} className="spin" />读取整理设置…</div> : reviewFields.length ? renderRuntimeFields(reviewFields) : <div className="settings-empty">当前后端没有提供整理配置。</div>}<div className="settings-card-callout"><Clock3 size={14} /><span>自动整理关闭时仍可在每日回顾页手动触发，不会影响历史摘要和记忆版本。</span></div></section>}

          {activeSection === "voice" && <section className="settings-card settings-panel-card"><div className="settings-card-heading"><div><span className="card-kicker">VOICE</span><h2>语音</h2><p>配置朗读模式、声音、语速和合成限制。</p></div><div className="tts-section-tools"><div className={`tts-status-badge ${ttsPresentation.tone}`} title={ttsStatus?.detail || undefined}><span className="tts-status-dot" />{ttsPresentation.label}</div><button className="ghost-button tts-preview-button" type="button" onClick={() => void runTtsPreview()} disabled={ttsPreviewLoading || ttsStatusLoading || ttsMode === "off" || !ttsStatus || ttsStatus.mode === "off" || !ttsStatus.enabled}><Headphones size={13} />{ttsPreviewLoading ? "试听中…" : "试听"}</button></div></div>{loading ? <div className="settings-loading"><RefreshCw size={15} className="spin" />读取语音设置…</div> : ttsFields.length ? renderRuntimeFields(ttsFields) : <div className="settings-empty">当前后端没有提供语音配置。</div>}{(ttsStatusError || ttsStatus?.detail || ttsPreviewMessage) && <div className="tts-status-detail">{ttsStatusError || ttsStatus?.detail || ttsPreviewMessage}<button className="icon-button" type="button" aria-label="刷新语音服务状态" title="刷新状态" onClick={() => void refreshTtsStatus()} disabled={ttsStatusLoading}><RefreshCw size={12} className={ttsStatusLoading ? "spin" : ""} /></button></div>}<audio ref={previewAudioRef} className="tts-audio" onEnded={() => setTtsPreviewMessage("")} /></section>}

          {activeSection === "advanced" && <section className="settings-card settings-panel-card"><div className="settings-card-heading"><div><span className="card-kicker">ADVANCED</span><h2>高级与调试</h2><p>仅在排查模型请求时开启。请求快照可能包含完整对话。</p></div><button className="icon-button neutral-hover" type="button" aria-label="刷新调试请求" title="刷新请求列表" onClick={() => void refreshDebugRequests()} disabled={debugRequestsLoading}><RefreshCw size={14} className={debugRequestsLoading ? "spin" : ""} /></button></div>{debugFields.length ? renderRuntimeFields(debugFields) : <div className="settings-empty">当前后端没有提供调试配置。</div>}<div className="debug-retention-note"><Bug size={13} /><span>只在后端内存中保留最近 {debugRequests?.capacity ?? 20} 次请求，服务重启后自动清空。</span></div><div className="debug-request-panel"><div className="debug-request-panel-heading"><div><strong>最近请求</strong><span>{debugRequests?.enabled ? `${debugRequests.items.length} / ${debugRequests.capacity} 条` : "当前未记录"}</span></div>{debugRequests?.enabled && debugRequests.items.length > 0 && <button className="ghost-button danger-button" type="button" onClick={() => setClearDebugPending(true)}><Trash2 size={12} />清空</button>}</div>{debugError && <div className="debug-inline-error">{debugError}</div>}{debugRequestsLoading && !debugRequests ? <div className="settings-loading"><RefreshCw size={14} className="spin" />读取请求列表…</div> : !debugRequests?.enabled ? <div className="debug-empty"><Bug size={16} /><strong>调试记录未开启</strong><span>开启并保存后，新的模型请求会显示在这里。</span></div> : debugRequests.items.length === 0 ? <div className="debug-empty"><Clipboard size={16} /><strong>还没有请求快照</strong><span>发送一条消息后再回来查看。</span></div> : <div className="debug-request-list">{debugRequests.items.map((item) => <button className="debug-request-row" type="button" key={item.id} onClick={() => void openDebugRequest(item.id)}><span className="debug-request-row-main"><strong>请求 #{item.id}</strong><span>第 {item.iteration + 1} 次模型请求</span></span><span className="debug-request-row-meta"><span>{item.provider} · {item.model}</span><span>{item.conversation_id ? `会话 #${item.conversation_id}` : "无会话"} · {debugTime(item.at)}</span></span><span className="debug-request-row-stats"><span>{item.messages} messages</span><span>{item.tools} tools</span><span>{item.seconds.toFixed(2)}s</span></span>{item.error && <span className="debug-request-error">{item.error}</span>}<ChevronRight size={14} /></button>)}</div>}</div></section>}

          {activeSection === "system" && <section className="settings-card settings-panel-card"><div className="settings-card-heading"><div><span className="card-kicker">SYSTEM & DATA</span><h2>系统与数据</h2><p>查看连接状态、运行模型，并手动创建数据备份。</p></div><Settings2 size={17} /></div><div className="settings-values">{loading ? <div className="settings-loading"><RefreshCw size={15} className="spin" />读取运行状态…</div> : <><SettingValue label="连接状态" value={connectionLabel} tone={health?.status === "ok" ? "value-success" : ""} /><SettingValue label="服务地址" value={apiBase} /><SettingValue label="Provider" value={runtime?.provider ?? "—"} /><SettingValue label="模型" value={runtime?.model ?? "—"} /><SettingValue label="知识库（只读）" value={runtime ? runtime.kb_enabled ? "已挂载" : "未启用" : "—"} tone={runtime?.kb_enabled ? "value-success" : ""} /><SettingValue label="默认思考" value={thinkingDefault} /><SettingValue label="会话级开关" value={runtime ? runtime.thinking_toggle ? "可用" : "不可用" : "—"} /></>}</div><div className="settings-system-actions"><div><strong>数据备份</strong><span>创建数据库和长期记忆文件的当前快照。</span></div><button className="ghost-button" onClick={() => void runBackup()} disabled={backupLoading}><HardDriveDownload size={13} />{backupLoading ? "备份中…" : "立即备份"}</button></div>{backup && <div className="settings-backup-result"><Download size={14} /><span>备份完成：{backup.dump_file} · {backup.memory_files} 个记忆文件 · {Math.round(backup.dump_bytes / 1024)} KB</span></div>}{runtime?.env_only?.length ? <div className="settings-env-only"><div className="settings-env-only-heading"><span className="settings-scope-badge">仅环境变量</span><strong>以下配置修改后需要重启后端</strong></div><div className="settings-env-only-list">{runtime.env_only.map((key) => <code key={key}>{key}</code>)}</div></div> : null}</section>}
        </div>
      </div>

      {changedKeys.length > 0 && <div className="settings-savebar" role="status"><div><strong>{changedKeys.length} 项更改尚未保存</strong><span>切换分类不会丢失当前修改</span></div><div><button className="ghost-button" type="button" onClick={discardRuntimeChanges} disabled={saving}>放弃更改</button><button className="primary-button" type="button" onClick={() => void saveRuntime()} disabled={saving}><Save size={13} />{saving ? "保存中…" : "保存更改"}</button></div></div>}
    </main>
    {debugDialog && <DebugDialog kind={debugDialog} prompt={debugPrompt} request={debugDetail} loading={debugDialog === "prompt" ? debugPromptLoading : debugDetailLoading} error={debugError} copied={debugCopied} onClose={() => setDebugDialog(null)} onCopy={(text, target) => void copyDebugText(text, target)} />}
    <ConfirmDialog open={clearDebugPending} title="清空调试请求？" description="最近的模型请求快照会从后端内存中全部移除，服务重启后本来也会自动清空。" confirmLabel="清空请求" busy={clearingDebug} onCancel={() => setClearDebugPending(false)} onConfirm={() => void clearDebug()} />
  </div>;
}
