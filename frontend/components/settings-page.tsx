"use client";

import { useCallback, useEffect, useId, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from "react";
import Link from "next/link";
import { AudioLines, BellRing, BrainCircuit, Bug, CalendarClock, Check, ChevronDown, ChevronLeft, ChevronRight, Clipboard, Clock3, Copy, Crop, Download, Eye, Gauge, HardDriveDownload, Headphones, ImagePlus, Mic2, Package, RefreshCw, RotateCcw, Save, Search, Send, ServerCog, Settings2, SlidersHorizontal, Smartphone, Trash2, TriangleAlert, UserRound, Volume2, Wrench, X, ZoomIn, type LucideIcon } from "lucide-react";
import { apiBaseLabel, clearAllData, clearDebugRequests, createBackup, createModelProfile, createModelService, errorMessage, getAsrStatus, getDebugPrompt, getDebugRequest, getHealth, getModelCatalog, getNotifyStatus, getRuntimeSettings, getTtsStatus, getTtsVoices, listDebugRequests, sendTestNotification, setDefaultModel, synthesizeSpeech, updateModelProfile, updateRuntimeSettings, warmupSpeech } from "@/lib/api";
import { defaultPreferences, isProfileAvatarImage, preferencesChangeEvent, profileInitials, readPreferences, writePreferences, type UserPreferences } from "@/lib/preferences";
import type { AsrStatus, EnvFieldStatus, BackupResult, DebugPrompt, DebugRequestDetail, DebugRequestList, HealthStatus, ModelCatalog, NotifyStatus, RuntimeSettingField, RuntimeSettings, TtsStatus } from "@/lib/types";
import { confirmAppNavigation, useNavigationGuard } from "@/lib/navigation-guard";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { ObservabilityCard } from "@/components/observability-card";
import { SkillsPanel } from "@/components/skills-panel";
import { ToolCatalog } from "@/components/tool-catalog";
import { useI18n } from "@/components/i18n-provider";
import { useToast } from "@/components/toast";
import { notifyWorkspaceConversationsChanged } from "@/components/workspace-topbar";
import { useDialogFocus } from "@/lib/use-dialog-focus";

type SettingsSectionKey = "profile" | "general" | "assistant" | "model" | "tools" | "skills" | "review" | "reminders" | "voice" | "system" | "advanced";

// 分区的图标和色号。色块是给「扫一眼找到」用的 —— 12 个同色线条图标堆在一起，
// 找东西只能逐行读文字；给每个分区一个固定颜色之后，位置记忆才立得住。
const settingsSections: Array<{ key: SettingsSectionKey; icon: typeof Settings2; tone: string }> = [
  { key: "profile", icon: UserRound, tone: "blue" },
  { key: "general", icon: SlidersHorizontal, tone: "slate" },
  { key: "assistant", icon: UserRound, tone: "violet" },
  { key: "model", icon: BrainCircuit, tone: "blue" },
  { key: "tools", icon: Wrench, tone: "teal" },
  { key: "skills", icon: Package, tone: "amber" },
  { key: "review", icon: Clock3, tone: "teal" },
  { key: "reminders", icon: BellRing, tone: "rose" },
  { key: "voice", icon: Headphones, tone: "purple" },
  { key: "system", icon: HardDriveDownload, tone: "green" },
  { key: "advanced", icon: Bug, tone: "slate" },
];

// 分组只用分隔线，不再给组标题文字（macOS 设置就是这样）——
// 「个性化 / 功能 / 系统」这三个词并不能帮人找到任何东西，只是多占三行。
const settingsSectionGroups: SettingsSectionKey[][] = [
  ["profile", "general", "assistant"],
  ["model", "tools", "skills", "review"],
  ["reminders", "voice"],
  ["system", "advanced"],
];

// 「这个字段属于哪个分区」仍由前端决定，因为一个 group 会被拆到多个分区
// （notify 的字段分散在通知页和时间线页）。
// 「这个字段是不是高级选项」已经交给后端的 field.advanced —— 原来这里还有四份
// *PrimaryFieldKeys 名单，加一个配置项要在两处同步，漏一处就是字段凭空消失。
const reviewFieldKeys = new Set(["consolidate_model", "consolidate_auto", "consolidate_hour"]);
const systemFieldKeys = new Set(["backup_auto", "backup_keep"]);
// Thinking is a per-chat composer decision. Keep legacy runtime fields in the
// API for old deployments, but never expose a second, conflicting UI here.
const chatThinkingFieldKeys = new Set(["deepseek_thinking", "openai_thinking", "openai_effort", "effort"]);
// Model profiles are the only model-routing UI. These fields stay in the API as
// an upgrade fallback, but must not leak into the page or settings search.
const legacyModelRoutingFieldKeys = new Set(["provider", "model", "deepseek_model", "openai_model"]);
// 用户真正需要的通知设置只有总开关、设备 key 和提醒规则；通道注册表、图标和提示音暂不暴露。
const notifyTimingFieldKeys = new Set(["notify_briefing", "notify_briefing_hour", "notify_all_day_hour", "notify_default_lead_minutes", "notify_catchup_hours", "notify_smart_copy", "notify_timeout"]);

const isPrimary = (field: RuntimeSettingField) => !field.advanced;
const isAdvanced = (field: RuntimeSettingField) => Boolean(field.advanced);

const fieldHelp: Record<string, string> = {
  owner_name: "助手在对话中对你的称呼",
  custom_instructions: "每次请求都会遵循的固定工作方式",
  provider: "日常对话使用的模型服务",
  model: "Anthropic 对话模型",
  deepseek_model: "DeepSeek 对话模型",
  max_tokens: "单次回答允许生成的最大 token 数",
  deepseek_max_tokens: "单次回答允许生成的最大 token 数",
  max_tool_iterations: "限制模型连续调用记忆工具的轮次",
  consolidate_model: "留空时沿用日常聊天模型",
  title_model: "智谱兼容配置；设置 SILICONFLOW_API_KEY 后优先使用环境变量中的硅基流动标题模型",
  consolidate_auto: "按固定时间自动整理当天对话",
  consolidate_hour: "使用后端所在时区的整点时间",
  tts_mode: "关闭、手动播放或回答完成后自动播放",
  history_max_chars: "限制每轮发给模型的历史长度，避免长会话撞到上下文窗口",
  notify_timeout: "推送通道和提醒文案模型调用的最长等待时间",
  bark_key: "已配置的 key 不会回传；重新输入即可替换，恢复默认可清除。",
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
  skills_enabled: "关掉后模型看不到任何技能；已装的技能不会被删",
  toolkits_disabled: "关闭某类工具后，新对话不会把它交给模型；不会删除数据",
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

type AvatarCropSource = { url: string; image: HTMLImageElement };

function loadProfileAvatar(file: File) {
  return new Promise<AvatarCropSource>((resolve, reject) => {
    if (!file.type.startsWith("image/")) {
      reject(new Error("请选择图片文件"));
      return;
    }
    if (file.size > 8 * 1024 * 1024) {
      reject(new Error("图片不能超过 8 MB"));
      return;
    }
    const url = URL.createObjectURL(file);
    const image = new Image();
    image.decoding = "async";
    image.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("图片解析失败"));
    };
    image.onload = () => resolve({ url, image });
    image.src = url;
  });
}

function AvatarCropDialog({ source, onCancel, onConfirm }: { source: AvatarCropSource; onCancel: () => void; onConfirm: (dataUrl: string) => void }) {
  const { t } = useI18n();
  const dialogRef = useRef<HTMLElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const dragRef = useRef<{ pointerId: number; x: number; y: number; offsetX: number; offsetY: number } | null>(null);
  const [zoom, setZoom] = useState(1);
  const size = 280;
  const [offset, setOffset] = useState(() => {
    const scale = Math.max(size / source.image.width, size / source.image.height);
    return { x: (size - source.image.width * scale) / 2, y: (size - source.image.height * scale) / 2 };
  });

  const metrics = useCallback((nextZoom = zoom) => {
    const scale = Math.max(size / source.image.width, size / source.image.height) * nextZoom;
    const width = source.image.width * scale;
    const height = source.image.height * scale;
    return { scale, width, height };
  }, [source, zoom]);

  const clampOffset = useCallback((next: { x: number; y: number }, nextZoom = zoom) => {
    const { width, height } = metrics(nextZoom);
    return {
      x: Math.min(0, Math.max(size - width, next.x)),
      y: Math.min(0, Math.max(size - height, next.y)),
    };
  }, [metrics, zoom]);

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = size * dpr;
    canvas.height = size * dpr;
    const context = canvas.getContext("2d");
    if (!context) return;
    const { scale } = metrics();
    const safeOffset = clampOffset(offset);
    context.setTransform(dpr, 0, 0, dpr, 0, 0);
    context.clearRect(0, 0, size, size);
    context.fillStyle = "#090c12";
    context.fillRect(0, 0, size, size);
    context.drawImage(source.image, safeOffset.x, safeOffset.y, source.image.width * scale, source.image.height * scale);
  }, [clampOffset, metrics, offset, source]);

  useEffect(() => {
    draw();
  }, [draw]);

  useDialogFocus({ dialogRef, initialFocusRef: closeRef, onClose: onCancel });

  const onPointerDown = (event: React.PointerEvent<HTMLCanvasElement>) => {
    event.currentTarget.setPointerCapture(event.pointerId);
    dragRef.current = { pointerId: event.pointerId, x: event.clientX, y: event.clientY, offsetX: offset.x, offsetY: offset.y };
  };
  const onPointerMove = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const ratio = rect.width ? size / rect.width : 1;
    setOffset(clampOffset({ x: drag.offsetX + (event.clientX - drag.x) * ratio, y: drag.offsetY + (event.clientY - drag.y) * ratio }));
  };
  const stopDragging = () => { dragRef.current = null; };
  const updateZoom = (value: number) => {
    setZoom(value);
    setOffset((current) => clampOffset(current, value));
  };
  const confirm = () => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const webp = canvas.toDataURL("image/webp", 0.84);
    onConfirm(webp.length <= 180_000 ? webp : canvas.toDataURL("image/jpeg", 0.78));
  };

  return <div className="avatar-crop-backdrop" role="presentation" onPointerDown={(event) => { if (event.target === event.currentTarget) onCancel(); }}>
    <section ref={dialogRef} className="avatar-crop-dialog" role="dialog" aria-modal="true" aria-labelledby="avatar-crop-title" tabIndex={-1}>
      <header className="avatar-crop-header"><div><span className="card-kicker">PROFILE IMAGE</span><h2 id="avatar-crop-title">{t("settings.profile.cropTitle")}</h2><p>{t("settings.profile.cropDescription")}</p></div><button ref={closeRef} className="icon-button" type="button" aria-label={t("settings.profile.cropCancel")} onClick={onCancel}><X size={16} /></button></header>
      <div className="avatar-crop-stage"><canvas ref={canvasRef} width={size} height={size} aria-label={t("settings.profile.cropPreview")} onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={stopDragging} onPointerCancel={stopDragging} /></div>
      <p className="avatar-crop-hint">{t("settings.profile.cropHint")}</p>
      <label className="avatar-crop-zoom"><span><ZoomIn size={14} aria-hidden="true" />{t("settings.profile.cropZoom")}</span><input type="range" min="1" max="3" step="0.01" value={zoom} aria-label={t("settings.profile.cropZoom")} onChange={(event) => updateZoom(Number(event.target.value))} /><output>{Math.round(zoom * 100)}%</output></label>
      <footer className="avatar-crop-actions"><button className="ghost-button" type="button" onClick={onCancel}>{t("settings.profile.cropCancel")}</button><button className="primary-button" type="button" onClick={confirm}><Crop size={14} />{t("settings.profile.cropConfirm")}</button></footer>
    </section>
  </div>;
}

function Toggle({ checked, onChange, label, description }: { checked: boolean; onChange: (checked: boolean) => void; label: string; description: string }) {
  return <label className="settings-toggle-row"><span className="settings-toggle-copy"><strong>{label}</strong><span>{description}</span></span><input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} /><span className="toggle-track" aria-hidden="true"><span /></span></label>;
}

function PreferenceSelectRow({ label, ariaLabel, value, options, onChange }: { label: string; ariaLabel: string; value: string; options: Array<{ value: string; label: string }>; onChange: (value: string) => void }) {
  return <label className="settings-preference-row">
    <strong>{label}</strong>
    <span className="settings-preference-select">
      <select aria-label={ariaLabel} value={value} onChange={(event) => onChange(event.target.value)}>
        {options.map((option) => <option value={option.value} key={option.value}>{option.label}</option>)}
      </select>
      <ChevronDown size={15} aria-hidden="true" />
    </span>
  </label>;
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

function RuntimeField({ field, value, source, providers, ttsStatus, asrStatus, ttsVoices, ttsVoicesLoading, disabled, pendingReset = false, highlighted = false, onChange, onRestore }: { field: RuntimeSettingField; value: unknown; source?: "db" | "env" | "default"; providers: RuntimeSettings["providers"]; ttsStatus?: TtsStatus | null; asrStatus?: AsrStatus | null; ttsVoices: string[]; ttsVoicesLoading: boolean; disabled: boolean; pendingReset?: boolean; highlighted?: boolean; onChange: (value: unknown) => void; onRestore: () => void }) {
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

  // 不生效的项照常可改（档案被删/停用时它们又会重新兜底），只是要说清楚现在没用。
  const inactive = field.inactive_reason ?? "";

  // 搜索命中要滚到视野里 —— 命中项常常在折叠块下面，只切分区还是要人再找一遍。
  const rowRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (highlighted) rowRef.current?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [highlighted]);

  return <div ref={rowRef} className={`runtime-setting-row ${pendingReset ? "pending-reset" : ""} ${inactive ? "is-inactive" : ""} ${highlighted ? "is-search-hit" : ""}`}>
    <div className="runtime-setting-label"><strong>{field.label}</strong><span>{fieldHelp[field.key] ?? (field.provider ? `仅用于 ${field.provider}` : "保存后立即生效")}</span></div>
    <div className={`runtime-setting-control ${multiline ? "multiline" : ""}`}>
      {field.kind === "bool" && <label className="runtime-checkbox"><input type="checkbox" aria-label={field.label} checked={value === true} disabled={controlDisabled} onChange={(event) => onChange(event.target.checked)} /><span>{value === true ? "开启" : "关闭"}</span></label>}
      {field.kind === "enum" && <select className="runtime-select" value={stringValue} disabled={controlDisabled} onChange={(event) => onChange(event.target.value)}>{choices.map((choice) => { const option = providerChoices.find((item) => item.value === choice); return <option key={choice} value={choice} disabled={option ? !option.available : false}>{settingChoiceLabels[choice] ?? choice}{option && !option.available ? "（不可用）" : ""}</option>; })}</select>}
      {field.kind === "int" && field.key !== "tts_speed_percent" && <input className="runtime-input" type="number" value={stringValue} min={field.minimum ?? undefined} max={field.maximum ?? undefined} disabled={controlDisabled} onChange={(event) => onChange(event.target.value === "" ? "" : Number(event.target.value))} />}
      {field.kind === "int" && field.key === "tts_speed_percent" && <div className="tts-speed-control"><input type="range" value={stringValue} min={field.minimum ?? 50} max={field.maximum ?? 200} step={5} disabled={controlDisabled} onChange={(event) => onChange(Number(event.target.value))} /><output>{stringValue}%</output></div>}
      {multiline && <textarea className={`runtime-textarea ${field.key === "custom_instructions" ? "runtime-textarea-tall" : ""}`} value={stringValue} maxLength={field.maximum ?? undefined} minLength={field.minimum ?? undefined} disabled={controlDisabled} onChange={(event) => onChange(event.target.value)} placeholder={field.key === "custom_instructions" ? "例如：回答控制在三句话以内，代码优先给 diff。" : "例如：用温柔、自然、亲切的语气说话"} />}
      {field.kind === "str" && field.key !== "tts_instruct" && !isTtsModel && !isTtsVoice && !isAsrModel && <input className="runtime-input runtime-input-wide" type={field.secret ? "password" : "text"} autoComplete={field.secret ? "new-password" : undefined} placeholder={field.secret && source ? "已配置，输入新 key 可替换" : undefined} value={stringValue} maxLength={field.maximum ?? undefined} minLength={field.minimum ?? undefined} disabled={controlDisabled} onChange={(event) => onChange(event.target.value)} />}
      {field.kind === "str" && (isTtsModel || isTtsVoice || isAsrModel) && <select className="runtime-select" value={stringValue} disabled={controlDisabled} onChange={(event) => onChange(event.target.value)}>{isTtsVoice && !stringValue && <option value="">默认音色</option>}{choices.map((choice) => { const cached = isAsrModel ? asrStatus?.cached_models?.find((item) => item.id === choice) : ttsStatus?.cached_models?.find((item) => item.id === choice); const loaded = isAsrModel ? asrStatus?.models.includes(choice) : ttsStatus?.models.includes(choice); const modelSuffix = isTtsModel || isAsrModel ? [cached ? `已缓存 ${formatModelSize(cached.size_bytes)}` : "", loaded ? "已加载" : ""].filter(Boolean).join(" · ") : ""; return <option key={choice} value={choice}>{isTtsVoice && voiceLabels[choice] ? `${choice} · ${voiceLabels[choice]}` : `${choice}${modelSuffix ? ` · ${modelSuffix}` : ""}`}</option>; })}</select>}
      {isTtsVoice && ttsVoicesLoading && <span className="runtime-inline-status"><RefreshCw size={11} className="spin" />读取音色</span>}
      <span className={`runtime-source ${source === "db" ? "modified" : ""} ${pendingReset ? "pending" : ""}`}>{pendingReset ? "待恢复默认" : source === "db" ? "已覆盖默认" : source === "env" ? "环境覆盖" : "代码默认"}</span>
      {source === "db" && <button className="icon-button runtime-restore" type="button" aria-label={pendingReset ? `取消恢复${field.label}` : `恢复${field.label}默认值`} title={pendingReset ? "取消恢复" : "恢复默认"} disabled={disabled} onClick={onRestore}><RotateCcw size={12} /></button>}
    </div>
    {multiline && <span className="runtime-setting-hint runtime-character-count">已用 {stringValue.length}{field.maximum ? ` / ${field.maximum}` : ""} 字</span>}
    {providerReason && <span className="runtime-setting-hint">{providerReason}</span>}
    {inactive && <span className="runtime-setting-hint runtime-inactive-hint"><TriangleAlert size={11} />{inactive}</span>}
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
  const dialogRef = useRef<HTMLElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  useDialogFocus({ dialogRef, initialFocusRef: closeRef, onClose });
  return <div className="debug-dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <section ref={dialogRef} className="debug-dialog" role="dialog" aria-modal="true" aria-labelledby="debug-dialog-title" tabIndex={-1}>
      <header className="debug-dialog-header"><div><span className="card-kicker">{isPrompt ? "SYSTEM PROMPT" : "MODEL REQUEST"}</span><h2 id="debug-dialog-title">{isPrompt ? "当前 system prompt" : request ? `请求 #${request.id}` : "请求详情"}</h2></div><button ref={closeRef} className="icon-button" type="button" aria-label="关闭调试详情" onClick={onClose}><X size={16} /></button></header>
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

function modelProtocolLabel(protocol: ModelCatalog["services"][number]["protocol"]) {
  if (protocol === "openai_responses") return "OpenAI Responses API";
  if (protocol === "openai_compatible") return "OpenAI Chat Completions";
  return "Anthropic API";
}

function ModelServicesPanel() {
  const [catalog, setCatalog] = useState<ModelCatalog | null>(null);
  const [consolidationCatalog, setConsolidationCatalog] = useState<ModelCatalog | null>(null);
  const [titleCatalog, setTitleCatalog] = useState<ModelCatalog | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [serviceName, setServiceName] = useState("");
  const [serviceSlug, setServiceSlug] = useState("");
  const [protocol, setProtocol] = useState<"anthropic" | "openai_compatible" | "openai_responses">("openai_compatible");
  const [baseUrl, setBaseUrl] = useState("");
  const [credentialRef, setCredentialRef] = useState("");
  const [profileServiceId, setProfileServiceId] = useState<number | "">("");
  const [modelId, setModelId] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [contextWindow, setContextWindow] = useState("");
  const [contextDrafts, setContextDrafts] = useState<Record<number, string>>({});
  const [search, setSearch] = useState("");
  const [serviceFilter, setServiceFilter] = useState("all");

  const refresh = async () => {
    setLoading(true);
    try {
      const [next, consolidation, title] = await Promise.all([getModelCatalog(), getModelCatalog("consolidation"), getModelCatalog("title")]);
      setCatalog(next);
      setConsolidationCatalog(consolidation);
      setTitleCatalog(title);
      setProfileServiceId((current) => current || next.services[0]?.id || "");
      setError("");
    } catch (cause) {
      setError(errorMessage(cause, "读取模型目录失败"));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void refresh(); }, []);

  const mutate = async (action: () => Promise<ModelCatalog>, purpose: "chat" | "consolidation" | "title" = "chat") => {
    setBusy(true);
    try {
      const next = await action();
      if (purpose === "consolidation") setConsolidationCatalog(next);
      else if (purpose === "title") setTitleCatalog(next);
      else setCatalog(next);
      setError("");
    } catch (cause) {
      setError(errorMessage(cause, "模型目录更新失败"));
    } finally {
      setBusy(false);
    }
  };

  const addService = async () => {
    if (!serviceName.trim() || !serviceSlug.trim()) return;
    await mutate(() => createModelService({ name: serviceName.trim(), slug: serviceSlug.trim(), protocol, base_url: baseUrl.trim(), credential_ref: credentialRef.trim() }));
    setServiceName("");
    setServiceSlug("");
    setBaseUrl("");
    setCredentialRef("");
  };

  const addProfile = async () => {
    if (!profileServiceId || !modelId.trim()) return;
    const parsedContext = Number(contextWindow);
    await mutate(() => createModelProfile({ service_id: Number(profileServiceId), model_id: modelId.trim(), display_name: displayName.trim() || modelId.trim(), ...(Number.isFinite(parsedContext) && parsedContext >= 16_384 ? { context_window_tokens: parsedContext } : {}) }));
    setModelId("");
    setDisplayName("");
    setContextWindow("");
  };

  const saveContextWindow = async (profile: ModelCatalog["profiles"][number], value: string) => {
    const trimmed = value.trim();
    const parsed = trimmed ? Number(trimmed) : null;
    if (parsed !== null && (!Number.isFinite(parsed) || parsed < 16_384 || parsed > 2_000_000)) return;
    await mutate(() => updateModelProfile(profile.id, { context_window_tokens: parsed }));
  };

  const visibleGroups = useMemo(() => {
    const normalizedSearch = search.trim().toLowerCase();
    return (catalog?.services ?? [])
      .filter((service) => serviceFilter === "all" || String(service.id) === serviceFilter)
      .map((service) => ({
        service,
        profiles: (catalog?.profiles ?? []).filter((profile) => {
          if (profile.service_id !== service.id) return false;
          if (!normalizedSearch) return true;
          return [profile.model_id, profile.display_name, profile.service_name]
            .some((value) => value.toLowerCase().includes(normalizedSearch));
        }),
      }))
      .filter((group) => group.profiles.length > 0 || !normalizedSearch);
  }, [catalog, search, serviceFilter]);

  const visibleProfileCount = visibleGroups.reduce((total, group) => total + group.profiles.length, 0);
  const chatDefault = catalog?.profiles.find((profile) => profile.is_default);
  const titleDefault = titleCatalog?.profiles.find((profile) => profile.is_default);

  return <SettingsVisualGroup icon={BrainCircuit} title="模型服务与模型" description="服务连接只保存凭据引用，具体模型可添加多个并在聊天页快速切换" tone="accent" className="model-services-group">
    {loading ? <div className="settings-loading"><RefreshCw size={15} className="spin" />读取模型目录…</div> : <>
      <div className="model-directory-summary">
        <div><span>聊天默认</span><strong>{chatDefault?.model_id ?? "未选择"}</strong></div>
        <div><span>标题默认</span><strong>{titleDefault?.model_id ?? "自动回退"}</strong></div>
        <div><span>模型数量</span><strong>{visibleProfileCount} / {catalog?.profiles.length ?? 0}</strong></div>
        <div><span>Provider 数量</span><strong>{catalog?.services.length ?? 0}</strong></div>
      </div>
      <div className="model-directory-toolbar">
        <label className="model-directory-search"><Search size={14} aria-hidden="true" /><input aria-label="搜索模型" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索模型名称或 Provider" /></label>
        <select className="runtime-select" aria-label="筛选 Provider" value={serviceFilter} onChange={(event) => setServiceFilter(event.target.value)}><option value="all">所有 Provider</option>{(catalog?.services ?? []).map((service) => <option value={service.id} key={service.id}>{service.name}</option>)}</select>
      </div>
      <div className="model-service-groups">
        {visibleGroups.map(({ service, profiles }) => <details className="model-service-group" key={service.id}>
          <summary>
            <span className="model-service-summary-copy"><strong>{service.name}</strong><small>{modelProtocolLabel(service.protocol)} · {profiles.length} 个模型</small></span>
            <span className={`model-service-status ${service.enabled ? "is-enabled" : "is-disabled"}`}>{service.enabled ? "已启用" : "已停用"}<ChevronRight size={13} /></span>
          </summary>
          <div className="model-profile-list model-profile-list-nested">
            {profiles.map((profile) => <div className={`model-profile-row ${profile.is_default || titleDefault?.id === profile.id || consolidationCatalog?.default_profile_id === profile.id ? "is-default" : ""}`} key={profile.id}>
              <div className="model-profile-main"><strong>{profile.display_name}</strong><span>{profile.model_id}</span></div>
              <div className="model-profile-meta"><span className={profile.available ? "value-success" : "value-warning"}>{profile.available ? "可用" : profile.reason || "不可用"}</span><label className="model-context-capacity" title="输入和输出合计的上下文窗口；未知时不会伪造数值"><span>上下文</span><input className="runtime-input" aria-label={`${profile.model_id} 上下文窗口`} type="number" min={16384} max={2000000} step={1024} placeholder="未配置" value={contextDrafts[profile.id] ?? (profile.context_window_tokens ? String(profile.context_window_tokens) : "")} onChange={(event) => setContextDrafts((current) => ({ ...current, [profile.id]: event.target.value }))} onBlur={(event) => { const value = event.target.value; void saveContextWindow(profile, value); }} /></label><button className="ghost-button" type="button" disabled={busy || !profile.available} onClick={() => void mutate(() => setDefaultModel("chat", profile.id))}>{profile.is_default ? "聊天默认" : "设为聊天默认"}</button><button className="ghost-button" type="button" disabled={busy || !profile.available} onClick={() => void mutate(() => setDefaultModel("consolidation", profile.id), "consolidation")}>{consolidationCatalog?.default_profile_id === profile.id ? "整理默认" : "设为整理默认"}</button><button className="ghost-button" type="button" disabled={busy || !profile.available} onClick={() => void mutate(() => setDefaultModel("title", profile.id), "title")}>{titleDefault?.id === profile.id ? "标题默认" : "设为标题默认"}</button><button className="icon-button" type="button" disabled={busy} title={profile.enabled ? "停用模型" : "启用模型"} onClick={() => void mutate(() => updateModelProfile(profile.id, { enabled: !profile.enabled }))}>{profile.enabled ? <Check size={13} /> : <RotateCcw size={13} />}</button></div>
            </div>)}
          </div>
        </details>)}
        {!visibleGroups.length && <div className="settings-empty">没有匹配的模型。</div>}
      </div>

      <details className="model-service-editor">
        <summary>添加模型服务</summary>
        <div className="model-service-form">
          <input className="runtime-input" value={serviceName} onChange={(event) => setServiceName(event.target.value)} placeholder="服务名称，例如 OpenRouter" />
          <input className="runtime-input" value={serviceSlug} onChange={(event) => setServiceSlug(event.target.value.toLowerCase())} placeholder="slug，例如 openrouter" />
          <select className="runtime-select" value={protocol} onChange={(event) => setProtocol(event.target.value as typeof protocol)}><option value="openai_compatible">OpenAI Chat Completions</option><option value="openai_responses">OpenAI Responses API</option><option value="anthropic">Anthropic</option></select>
          <input className="runtime-input runtime-input-wide" value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} placeholder="Base URL，例如 https://openrouter.ai/api/v1" />
          <input className="runtime-input" value={credentialRef} onChange={(event) => setCredentialRef(event.target.value.toUpperCase())} placeholder="API Key 环境变量名" />
          <button className="ghost-button" type="button" disabled={busy || !serviceName.trim() || !serviceSlug.trim()} onClick={() => void addService()}>添加服务</button>
        </div>
        <p className="settings-card-footnote">这里只保存环境变量名，不保存 API Key。先在 .env 配置对应变量，再在这里引用。</p>
      </details>

      <details className="model-service-editor">
        <summary>添加模型档案</summary>
        <div className="model-service-form">
          <select className="runtime-select" value={profileServiceId} onChange={(event) => setProfileServiceId(event.target.value ? Number(event.target.value) : "")}><option value="">选择模型服务</option>{(catalog?.services ?? []).map((service) => <option value={service.id} key={service.id}>{service.name}</option>)}</select>
          <input className="runtime-input" value={modelId} onChange={(event) => setModelId(event.target.value)} placeholder="模型 ID，例如 qwen/qwen3-32b" />
          <input className="runtime-input" value={displayName} onChange={(event) => setDisplayName(event.target.value)} placeholder="显示名称（可选）" />
          <input className="runtime-input" type="number" min={16384} max={2000000} step={1024} value={contextWindow} onChange={(event) => setContextWindow(event.target.value)} placeholder="上下文窗口 tokens（可选）" />
          <button className="ghost-button" type="button" disabled={busy || !profileServiceId || !modelId.trim()} onClick={() => void addProfile()}>添加模型</button>
        </div>
      </details>
    </>}
    {error && <div className="settings-card-callout"><TriangleAlert size={14} /><span>{error}</span></div>}
  </SettingsVisualGroup>;
}

/** 只能改 `.env` 的那些项。
 *
 * 原来只把变量名平铺出来，看不出哪些配了 —— 而「标题生成为什么没走硅基流动」
 * 这类问题，答案十有八九就是某个 key 没配。
 *
 * **密钥只显示配没配，值由后端保证不下发**（`kind: "secret"` 时 `value` 恒为空串）。
 */
function EnvStatusList({ rows, fallback }: { rows: EnvFieldStatus[]; fallback: string[] }) {
  if (!rows.length) {
    // 后端还没升级时退回旧的平铺列表，别让这一块整个消失
    return fallback.length ? <details className="settings-disclosure settings-env-disclosure"><summary><span><strong>环境配置</strong><small>仅用于排查连接问题，修改后需重启后端</small></span><ChevronRight size={13} /></summary><div className="settings-env-only"><div className="settings-env-only-heading"><span className="settings-scope-badge">仅环境变量</span><strong>修改后需要重启后端</strong></div><div className="settings-env-only-list">{fallback.map((key) => <code key={key}>{key}</code>)}</div></div></details> : null;
  }
  const secrets = rows.filter((row) => row.kind === "secret");
  const plain = rows.filter((row) => row.kind !== "secret");
  return <details className="settings-disclosure settings-env-disclosure">
    <summary><span><strong>环境配置</strong><small>密钥只显示是否配置；地址和日志等共 {rows.length} 项</small></span><ChevronRight size={13} /></summary>
    <div className="settings-env-only">
      <div className="settings-env-only-heading"><span className="settings-scope-badge">仅环境变量</span><strong>改这些要重启后端</strong></div>
      <div className="env-status-grid">
        {secrets.map((row) => <div className={`env-status-row ${row.configured ? "" : row.note ? "env-status-warn" : ""}`} key={row.key}>
          <span className="env-status-label"><strong>{row.label}</strong><code>{row.env}</code></span>
          <span className={`env-status-pill ${row.configured ? "ok" : ""}`}>{row.configured ? "已配置" : "未配置"}</span>
          {row.note && <span className="env-status-note">{row.note}</span>}
        </div>)}
      </div>
      <details className="settings-disclosure env-status-plain">
        <summary><span><strong>其余环境配置</strong><small>地址、日志和上限，共 {plain.length} 项</small></span><ChevronRight size={13} /></summary>
        <div className="env-status-grid">
          {plain.map((row) => <div className="env-status-row" key={row.key}>
            <span className="env-status-label"><strong>{row.label}</strong><code>{row.env}</code></span>
            <span className="env-status-value">{row.value || "—"}</span>
          </div>)}
        </div>
      </details>
    </div>
  </details>;
}

export function SettingsPage() {
  const { t } = useI18n();
  const toast = useToast();
  const [activeSection, setActiveSection] = useState<SettingsSectionKey>("general");
  const [mobileDetailOpen, setMobileDetailOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [highlightedField, setHighlightedField] = useState("");
  const [runtime, setRuntime] = useState<RuntimeSettings | null>(null);
  const [draftValues, setDraftValues] = useState<Record<string, unknown>>({});
  const [pendingResets, setPendingResets] = useState<Set<string>>(() => new Set());
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [preferences, setPreferences] = useState<UserPreferences>(defaultPreferences);
  const [profileAvatarLoading, setProfileAvatarLoading] = useState(false);
  const [avatarCropSource, setAvatarCropSource] = useState<AvatarCropSource | null>(null);
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
  const [clearAllDataPending, setClearAllDataPending] = useState(false);
  const [clearingAllData, setClearingAllData] = useState(false);
  const [clearAllDataMessage, setClearAllDataMessage] = useState("");

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
      // 状态读不到不该挡住整个设置页；设置页仍可编辑本地配置。
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
    const handlePreferenceChange = (event: Event) => {
      const detail = (event as CustomEvent<UserPreferences>).detail;
      if (detail) setPreferences(detail);
    };
    window.addEventListener(preferencesChangeEvent(), handlePreferenceChange);
    return () => window.removeEventListener(preferencesChangeEvent(), handlePreferenceChange);
  }, [loadRuntime]);

  useEffect(() => {
    if (activeSection !== "voice") return;
    if (!ttsStatus && !ttsStatusError && !ttsStatusLoading) void refreshTtsStatus();
    if (!asrStatus && !asrStatusError && !asrStatusLoading) void refreshAsrStatus();
  }, [activeSection, asrStatus, asrStatusError, asrStatusLoading, refreshAsrStatus, refreshTtsStatus, ttsStatus, ttsStatusError, ttsStatusLoading]);

  useEffect(() => () => {
    previewAudioRef.current?.pause();
    if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
  }, []);

  useEffect(() => () => {
    if (avatarCropSource) URL.revokeObjectURL(avatarCropSource.url);
  }, [avatarCropSource]);

  const updatePreference = <K extends keyof UserPreferences>(key: K, value: UserPreferences[K]) => {
    const next = { ...preferences, [key]: value };
    setPreferences(next);
    writePreferences(next);
  };

  const closeAvatarCrop = useCallback(() => {
    setAvatarCropSource((current) => {
      if (current) URL.revokeObjectURL(current.url);
      return null;
    });
  }, []);

  const updateProfileAvatar = async (file: File | undefined) => {
    if (!file) return;
    setProfileAvatarLoading(true);
    try {
      setAvatarCropSource(await loadProfileAvatar(file));
    } catch (cause) {
      toast.push({ tone: "danger", message: errorMessage(cause, "头像上传失败") });
    } finally {
      setProfileAvatarLoading(false);
    }
  };

  const confirmProfileAvatar = (dataUrl: string) => {
    updatePreference("profileAvatar", dataUrl);
    closeAvatarCrop();
  };

  const activeProvider = typeof draftValues.provider === "string" ? draftValues.provider : runtime?.provider;
  const activeFields = useMemo(() => runtime?.fields?.filter((field) => !field.provider || field.provider === activeProvider) ?? [], [activeProvider, runtime]);
  const ungroupedFields = useMemo(() => activeFields.filter((field) => (field.group ?? "") === ""), [activeFields]);
  const modelFields = useMemo(() => ungroupedFields.filter((field) => !reviewFieldKeys.has(field.key) && !systemFieldKeys.has(field.key) && !chatThinkingFieldKeys.has(field.key) && !legacyModelRoutingFieldKeys.has(field.key)), [ungroupedFields]);
  const modelAdvancedFields = useMemo(() => modelFields.filter((field) => isAdvanced(field) && field.key !== "title_model"), [modelFields]);
  const reviewFields = useMemo(() => ungroupedFields.filter((field) => reviewFieldKeys.has(field.key)), [ungroupedFields]);
  const reviewPrimaryFields = useMemo(() => reviewFields.filter(isPrimary), [reviewFields]);
  const reviewAdvancedFields = useMemo(() => reviewFields.filter(isAdvanced), [reviewFields]);
  const promptFields = useMemo(() => activeFields.filter((field) => field.group === "prompt"), [activeFields]);
  const ttsFields = useMemo(() => activeFields.filter((field) => field.group === "tts"), [activeFields]);
  const asrFields = useMemo(() => activeFields.filter((field) => field.group === "asr"), [activeFields]);
  const systemFields = useMemo(() => ungroupedFields.filter((field) => systemFieldKeys.has(field.key)), [ungroupedFields]);
  const ttsModeFields = useMemo(() => ttsFields.filter((field) => field.key === "tts_mode"), [ttsFields]);
  const ttsPrimaryFields = useMemo(() => ttsFields.filter((field) => field.key !== "tts_mode" && isPrimary(field)), [ttsFields]);
  const ttsAdvancedFields = useMemo(() => ttsFields.filter(isAdvanced), [ttsFields]);
  const asrPrimaryFields = useMemo(() => asrFields.filter(isPrimary), [asrFields]);
  const asrAdvancedFields = useMemo(() => asrFields.filter(isAdvanced), [asrFields]);
  const notifyFields = useMemo(() => activeFields.filter((field) => field.group === "notify"), [activeFields]);
  const notifyEnabledFields = useMemo(() => notifyFields.filter((field) => field.key === "notify_enabled"), [notifyFields]);
  const notifyDeliveryFields = useMemo(() => notifyFields.filter((field) => field.key === "bark_key" || field.key === "notify_public_base_url"), [notifyFields]);
  const timelineFields = useMemo(() => notifyFields.filter((field) => notifyTimingFieldKeys.has(field.key)), [notifyFields]);
  const timelinePrimaryFields = useMemo(() => timelineFields.filter(isPrimary), [timelineFields]);
  const timelineAdvancedFields = useMemo(() => timelineFields.filter(isAdvanced), [timelineFields]);
  const skillFields = useMemo(() => activeFields.filter((field) => field.group === "skills"), [activeFields]);
  const toolFields = useMemo(() => activeFields.filter((field) => field.group === "tools"), [activeFields]);
  const debugFields = useMemo(() => activeFields.filter((field) => field.group === "debug"), [activeFields]);

  // ---- 搜索 ----
  // 索引直接建在后端下发的 fields[] 上：label 和分区归属都是现成的，
  // 不需要额外契约，也不存在「加了配置项忘记加进搜索」这种漏。
  const searchIndex = useMemo(() => {
    const buckets: Array<[SettingsSectionKey, RuntimeSettingField[]]> = [
      ["assistant", promptFields],
      ["model", modelFields],
      ["tools", toolFields],
      ["skills", skillFields],
      ["review", reviewFields],
      ["reminders", notifyFields],
      ["voice", [...ttsFields, ...asrFields]],
      ["system", systemFields],
      ["advanced", debugFields],
    ];
    return [
      { section: "general" as const, key: "", label: t("settings.general.appearance"), help: t("theme.select") },
      { section: "general" as const, key: "", label: t("settings.general.language"), help: t("language.select") },
      ...buckets.flatMap(([section, fields]) => fields.map((field) => ({
      section,
      key: field.key,
      label: field.label,
      help: fieldHelp[field.key] ?? "",
      }))),
    ];
  }, [asrFields, debugFields, modelFields, notifyFields, promptFields, reviewFields, skillFields, systemFields, t, toolFields, ttsFields]);

  const searchResults = useMemo(() => {
    const needle = searchQuery.trim().toLowerCase();
    if (!needle) return [];
    const hits = searchIndex.filter((entry) =>
      entry.label.toLowerCase().includes(needle)
      || entry.key.toLowerCase().includes(needle)
      || entry.help.toLowerCase().includes(needle));
    // 分区名本身也能搜（搜「备份」要能命中「数据与备份」这一栏）
    const sectionHits = settingsSections
      .filter((section) => t(`settings.section.${section.key}.label`).toLowerCase().includes(needle))
      .map((section) => ({ section: section.key, key: "", label: t(`settings.section.${section.key}.label`), help: "" }));
    return [...sectionHits, ...hits].slice(0, 12);
  }, [searchIndex, searchQuery, t]);

  const openSearchHit = (section: SettingsSectionKey, key: string) => {
    setActiveSection(section);
    setMobileDetailOpen(true);
    // 高亮而不是滚动到顶：搜到的那一行往往在折叠块里，光跳分区还是要人再找一遍。
    setHighlightedField(key);
    setSearchQuery("");
  };

  const openSection = (section: SettingsSectionKey) => {
    setActiveSection(section);
    setHighlightedField("");
    setMobileDetailOpen(true);
  };

  const moveSectionSelection = (event: ReactKeyboardEvent<HTMLButtonElement>, section: SettingsSectionKey) => {
    const keys = settingsSections.map((item) => item.key);
    const current = keys.indexOf(section);
    let next = current;
    if (event.key === "ArrowDown") next = Math.min(keys.length - 1, current + 1);
    else if (event.key === "ArrowUp") next = Math.max(0, current - 1);
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = keys.length - 1;
    else return;
    event.preventDefault();
    const nextKey = keys[next];
    openSection(nextKey);
    window.requestAnimationFrame(() => document.querySelector<HTMLButtonElement>(`[data-settings-section="${nextKey}"]`)?.focus());
  };

  // 高亮只留几秒 —— 它是「你要找的在这」的指示，不是一个持久状态。
  useEffect(() => {
    if (!highlightedField) return;
    const timer = window.setTimeout(() => setHighlightedField(""), 2600);
    return () => window.clearTimeout(timer);
  }, [highlightedField]);
  const changedKeys = useMemo(() => Array.from(new Set([
    ...activeFields.filter((field) => !Object.is(draftValues[field.key], runtime?.values?.[field.key])).map((field) => field.key),
    ...pendingResets,
  ])), [activeFields, draftValues, pendingResets, runtime]);
  useNavigationGuard(changedKeys.length > 0, "设置页有尚未保存的修改，确定放弃并离开吗？");
  const apiBase = apiBaseLabel();
  const connectionLabel = health?.status === "ok" ? "已连接" : health ? health.status : "未知";
  const ttsMode = draftValues.tts_mode === "manual" || draftValues.tts_mode === "auto" ? draftValues.tts_mode : "off";
  const notifyEnabled = typeof draftValues.notify_enabled === "boolean" ? draftValues.notify_enabled : notifyStatus?.enabled ?? false;
  const ttsPresentation = ttsStatusPresentation(ttsStatus, ttsStatusLoading);
  const profileToneOptions = [
    { value: "blue" as const, label: t("settings.profile.toneBlue") },
    { value: "violet" as const, label: t("settings.profile.toneViolet") },
    { value: "teal" as const, label: t("settings.profile.toneTeal") },
    { value: "orange" as const, label: t("settings.profile.toneOrange") },
  ];

  // 左栏的状态点：不点进去就知道哪些能力还没配好。
  // **只给真的拿到了状态的分区**——凭空给每一项都点一个圆点，那是装饰不是信息。
  const sectionStatus: Partial<Record<SettingsSectionKey, { tone: string; hint: string }>> = {
    voice: ttsMode === "off"
      ? { tone: "off", hint: "语音未启用" }
      : ttsStatus && !ttsStatus.reachable
        ? { tone: "warn", hint: "语音服务离线" }
        : { tone: "on", hint: "语音已启用" },
    reminders: !notifyEnabled
      ? { tone: "off", hint: "主动通知未开启" }
      : notifyStatus?.ready
        ? { tone: "on", hint: "通知已就绪" }
        : { tone: "warn", hint: "已开启，但推送通道还没配置" },
  };
  if (typeof draftValues.skills_enabled === "boolean") {
    sectionStatus.skills = draftValues.skills_enabled
      ? { tone: "on", hint: "技能已启用" }
      : { tone: "off", hint: "技能已关闭" };
  }
  const disabledToolkits = typeof draftValues.toolkits_disabled === "string" ? draftValues.toolkits_disabled : "";
  const toggleToolkit = (toolkit: string, enabled: boolean) => {
    const field = toolFields.find((item) => item.key === "toolkits_disabled");
    if (!field) return;
    const disabled = new Set(disabledToolkits.split(",").map((value) => value.trim()).filter(Boolean));
    if (enabled) disabled.delete(toolkit);
    else disabled.add(toolkit);
    changeRuntimeField(field, Array.from(disabled).sort().join(","));
  };
  if (toolFields.length > 0) {
    sectionStatus.tools = disabledToolkits
      ? { tone: "warn", hint: "部分工具已关闭" }
      : { tone: "on", hint: "工具已启用" };
  }
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
    if (activeSection === "reminders") void refreshNotifyStatus();
  }, [activeSection, refreshNotifyStatus]);

  const saveRuntime = async () => {
    if (!runtime || !changedKeys.length || saving) return;
    setSaving(true);
    setError("");
    try {
      const changes = Object.fromEntries(changedKeys.map((key) => [key, pendingResets.has(key) ? null : draftValues[key]]));
      const updated = await updateRuntimeSettings(changes);
      setRuntime(updated);
      setDraftValues(updated.values ?? {});
      setPendingResets(new Set());
      if (changedKeys.some((key) => key.startsWith("tts_"))) void refreshTtsStatus();
      if (changedKeys.some((key) => key.startsWith("asr_"))) void refreshAsrStatus();
      if (debugFields.length > 0) void refreshDebugRequests();
      toast.push({ message: "设置已保存", description: "已立即生效，无需重启", tone: "success" });
    } catch (cause) {
      toast.push({ message: errorMessage(cause, "无法保存后端设置"), tone: "danger" });
    } finally {
      setSaving(false);
    }
  };

  const changeRuntimeField = (field: RuntimeSettingField, value: unknown) => {
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

  const clearAllDataAction = async () => {
    if (clearingAllData) return;
    setClearingAllData(true);
    setClearAllDataMessage("");
    try {
      const result = await clearAllData();
      setClearAllDataPending(false);
      setClearAllDataMessage(`已清空 ${result.deleted_conversations} 段对话、${result.deleted_memories} 条记忆、${result.deleted_timeline_items} 条时间事项、${result.deleted_daily_digests} 条每日回顾`);
      notifyWorkspaceConversationsChanged({ type: "cleared" });
    } catch (cause) {
      setError(errorMessage(cause, "清空所有数据失败"));
    } finally {
      setClearingAllData(false);
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
      highlighted={highlightedField === field.key}
      onChange={(value) => changeTtsField(field, value)}
      onRestore={() => toggleRuntimeReset(field)}
    />)}
  </div>;

  return <div className={`settings-shell ${mobileDetailOpen ? "mobile-detail-open" : ""}`}>
    <main className="settings-content settings-content-refined">
      <header className="settings-app-toolbar">
        <Link className="settings-app-back" href="/" onClick={(event) => { if (!confirmAppNavigation()) event.preventDefault(); }}><ChevronLeft size={18} />朝花夕拾</Link>
        <button className="settings-detail-back" type="button" onClick={() => setMobileDetailOpen(false)}><ChevronLeft size={18} />设置</button>
        <strong>设置</strong>
        <div className={`settings-service-status ${health?.status === "ok" ? "is-online" : ""}`} role="status">
          <span className="settings-service-dot" aria-hidden="true" />
          <span>{health?.status === "ok" ? t("settings.service.ok") : health ? connectionLabel : t("settings.service.unknown")}</span>
        </div>
      </header>
      {error && <div className="settings-error"><X size={15} /><span>{error}</span><button className="ghost-button" onClick={() => void loadRuntime()} disabled={loading}><RefreshCw size={12} />{t("settings.retry")}</button></div>}

      <div className="settings-layout settings-layout-aligned">
        <aside className="settings-section-nav settings-nav-rail" aria-label={t("settings.navLabel")}>
          <div className="settings-page-title">
            <span className="settings-page-eyebrow">{t("settings.eyebrow")}</span>
            <h1>{t("settings.title")}</h1>
            <p>{t("settings.description")}</p>
          </div>
          <div className="settings-nav-search" role="search">
            <Search size={13} aria-hidden="true" />
            <input value={searchQuery} aria-label="搜索设置" placeholder="搜索设置" onChange={(event) => setSearchQuery(event.target.value)} onKeyDown={(event) => { if (event.key === "Escape") setSearchQuery(""); }} />
            {searchQuery && <button className="icon-button" type="button" aria-label="清空搜索" onClick={() => setSearchQuery("")}><X size={12} /></button>}
          </div>

          {searchQuery.trim() ? <div className="settings-nav-results">
            {searchResults.length ? searchResults.map((hit) => <button className="settings-nav-result" type="button" key={`${hit.section}-${hit.key}`} onClick={() => openSearchHit(hit.section, hit.key)}>
              <span className="settings-nav-result-label">{hit.label}</span>
              <span className="settings-nav-result-path">{t(`settings.section.${hit.section}.label`)}</span>
            </button>) : <p className="settings-nav-empty">没有匹配的设置项</p>}
          </div> : settingsSectionGroups.map((group, index) => <div className="settings-nav-group" key={index}>
            {group.map((key) => {
              const section = settingsSections.find((item) => item.key === key);
              if (!section) return null;
              const Icon = section.icon;
              const status = sectionStatus[key];
              return <button className={activeSection === key ? "active" : ""} type="button" data-settings-section={key} aria-current={activeSection === key ? "page" : undefined} key={key} onClick={() => openSection(key)} onKeyDown={(event) => moveSectionSelection(event, key)} title={status?.hint}>
                <span className={`settings-nav-icon tone-${section.tone}`} aria-hidden="true"><Icon size={13} /></span>
                <span className="settings-nav-label">{t(`settings.section.${key}.label`)}</span>
                {status && <span className={`settings-nav-dot ${status.tone}`} role="img" aria-label={status.hint} />}
              </button>;
            })}
          </div>)}
        </aside>

        <div className="settings-section-content settings-detail-column">
          {activeSection === "profile" && <section className="settings-card settings-panel-card settings-profile-panel">
            <div className="settings-card-heading"><div><span className="card-kicker">PROFILE</span><h2>{t("settings.profile.title")}</h2><p>{t("settings.profile.description")}</p></div><UserRound size={17} /></div>
            <div className="settings-profile-preview">
              <div className={`settings-profile-avatar tone-${preferences.profileTone}`} aria-hidden="true">{isProfileAvatarImage(preferences.profileAvatar) ? <>
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={preferences.profileAvatar} alt="" />
              </> : profileInitials(preferences.profileName || defaultPreferences.profileName, preferences.profileAvatar)}</div>
              <div className="settings-profile-preview-copy"><span>{t("settings.profile.preview")}</span><strong>{preferences.profileName || defaultPreferences.profileName}</strong><small>{t("workspace.localMemory")}</small></div>
              <div className="settings-profile-preview-tools">
                <label className="settings-profile-avatar-upload"><ImagePlus size={14} aria-hidden="true" /><span>{profileAvatarLoading ? t("settings.profile.uploading") : t("settings.profile.upload")}</span><input type="file" accept="image/png,image/jpeg,image/webp" onChange={(event) => { void updateProfileAvatar(event.target.files?.[0]); event.currentTarget.value = ""; }} disabled={profileAvatarLoading || Boolean(avatarCropSource)} /></label>
                {isProfileAvatarImage(preferences.profileAvatar) && <button className="settings-profile-avatar-remove" type="button" onClick={() => updatePreference("profileAvatar", "")} disabled={profileAvatarLoading}><Trash2 size={13} aria-hidden="true" />{t("settings.profile.remove")}</button>}
                <small aria-live="polite">{profileAvatarLoading ? t("settings.profile.uploading") : t("settings.profile.imageHint")}</small>
              </div>
            </div>
            <div className="settings-profile-form">
              <label className="settings-profile-field"><span><strong>{t("settings.profile.name")}</strong><small>{t("settings.profile.nameHint")}</small></span><input className="runtime-input" type="text" autoComplete="name" maxLength={32} value={preferences.profileName} onChange={(event) => updatePreference("profileName", event.target.value.slice(0, 32))} /></label>
              <label className="settings-profile-field"><span><strong>{t("settings.profile.avatar")}</strong><small>{isProfileAvatarImage(preferences.profileAvatar) ? t("settings.profile.avatarImageActive") : t("settings.profile.avatarHint")}</small></span><input className="runtime-input settings-profile-avatar-input" type="text" inputMode="text" maxLength={2} placeholder={isProfileAvatarImage(preferences.profileAvatar) ? t("settings.profile.avatarImagePlaceholder") : undefined} value={isProfileAvatarImage(preferences.profileAvatar) ? "" : preferences.profileAvatar} disabled={isProfileAvatarImage(preferences.profileAvatar)} onChange={(event) => updatePreference("profileAvatar", event.target.value.replace(/\s/g, "").slice(0, 2))} /></label>
            </div>
            <fieldset className="settings-profile-tone-fieldset">
              <legend>{t("settings.profile.tone")}</legend>
              <div className="settings-profile-tones">
                {profileToneOptions.map((tone) => <button className={`settings-profile-tone tone-${tone.value} ${preferences.profileTone === tone.value ? "is-selected" : ""}`} type="button" key={tone.value} aria-label={tone.label} aria-pressed={preferences.profileTone === tone.value} onClick={() => updatePreference("profileTone", tone.value)}><span aria-hidden="true" /></button>)}
              </div>
            </fieldset>
            <div className="settings-card-actions"><span>{t("settings.profile.saved")}</span></div>
          </section>}
          {activeSection === "general" && <section className="settings-card settings-panel-card">
            <div className="settings-card-heading"><div><span className="card-kicker">CHAT & DISPLAY</span><h2>聊天与显示</h2><p>消息发送、回答跟随和过程信息都在这里。</p></div><SlidersHorizontal size={17} /></div>
            <section className="settings-preference-surface" aria-label={t("settings.general.appearanceLanguage")}>
              <PreferenceSelectRow
                label={t("settings.general.appearance")}
                ariaLabel={t("theme.select")}
                value={preferences.theme}
                options={[
                  { value: "system", label: t("theme.system") },
                  { value: "light", label: t("theme.light") },
                  { value: "dark", label: t("theme.dark") },
                ]}
                onChange={(value) => updatePreference("theme", value as UserPreferences["theme"])}
              />
              <PreferenceSelectRow
                label={t("settings.general.language")}
                ariaLabel={t("language.select")}
                value={preferences.locale}
                options={[
                  { value: "zh-CN", label: t("language.zh-CN") },
                  { value: "en-US", label: t("language.en-US") },
                ]}
                onChange={(value) => updatePreference("locale", value as UserPreferences["locale"])}
              />
            </section>
            <SettingsVisualGroup icon={SlidersHorizontal} title="对话行为" description="发送方式与回答跟随" tone="accent">
              <div className="settings-toggle-list">
                <Toggle label={t("settings.general.enter")} description={t("settings.general.enterDescription")} checked={preferences.enterToSend} onChange={(value) => updatePreference("enterToSend", value)} />
                <Toggle label={t("settings.general.scroll")} description={t("settings.general.scrollDescription")} checked={preferences.autoScroll} onChange={(value) => updatePreference("autoScroll", value)} />
              </div>
            </SettingsVisualGroup>
            <SettingsVisualGroup icon={Eye} title="界面信息" description="控制工具活动和 token 用量的呈现" tone="neutral">
              <div className="settings-toggle-list">
                <Toggle label={t("settings.general.tools")} description={t("settings.general.toolsDescription")} checked={preferences.showToolActivity} onChange={(value) => updatePreference("showToolActivity", value)} />
                <Toggle label={t("settings.general.usage")} description={t("settings.general.usageDescription")} checked={preferences.showUsage} onChange={(value) => updatePreference("showUsage", value)} />
              </div>
            </SettingsVisualGroup>
            <div className="settings-card-actions"><span>{t("settings.general.saved")}</span><button className="ghost-button" onClick={() => { setPreferences(defaultPreferences); writePreferences(defaultPreferences); }}>{t("settings.general.reset")}</button></div>
          </section>}

          {activeSection === "assistant" && <section className="settings-card settings-panel-card">
            <div className="settings-card-heading"><div><span className="card-kicker">ASSISTANT</span><h2>助手规则</h2><p>设置称呼，以及助手每次对话都应遵循的工作方式。</p></div><UserRound size={17} /></div>
            <SettingsVisualGroup icon={UserRound} title="称呼与固定指令" description="长期有效的回答规则，不用于保存事实和计划" tone="violet">
              {loading ? <div className="settings-loading"><RefreshCw size={15} className="spin" />读取设置…</div> : promptFields.length ? renderRuntimeFields(promptFields) : <div className="settings-empty">当前后端没有提供人格配置。</div>}
              <div className="prompt-boundary-note">事实、偏好和计划请交给长期记忆。</div>
            </SettingsVisualGroup>
          </section>}

          {/* 模型目录和「Provider 与回答」原来是两个分区，名字几乎一样、内容重叠 ——
              而且只有目录是真的在决定用哪个模型。合成一页，目录放最上面。 */}
          {activeSection === "model" && <section className="settings-card settings-panel-card">
            <div className="settings-card-heading"><div><span className="card-kicker">MODELS</span><h2>模型</h2><p>添加模型服务、选择默认模型，以及回答和工具调用参数。</p></div><ServerCog size={17} /></div>
            <ModelServicesPanel />
            {loading ? <div className="settings-loading"><RefreshCw size={15} className="spin" />读取模型设置…</div> : modelFields.length ? <>
              <details className="settings-disclosure settings-field-disclosure">
                <summary><span><strong><Gauge size={14} />回答与工具限制</strong><small>输出上限、标题模型和最大工具次数</small></span><ChevronRight size={14} /></summary>
                {modelAdvancedFields.length ? renderRuntimeFields(modelAdvancedFields) : <div className="settings-empty">当前没有回答限制字段。</div>}
              </details>
            </> : <div className="settings-empty">当前后端没有提供模型配置。</div>}
          </section>}

          {activeSection === "tools" && <section className="settings-card settings-panel-card tools-settings-card">
            <div className="settings-card-heading"><div><span className="card-kicker">TOOLS</span><h2>{t("settings.tools.title")}</h2><p>{t("settings.tools.description")}</p></div><Wrench size={17} /></div>
            <SettingsVisualGroup icon={Wrench} title={t("settings.tools.controlTitle")} description={t("settings.tools.controlDescription")} tone="accent">
              {loading ? <div className="settings-loading"><RefreshCw size={15} className="spin" />{t("settings.tools.loading")}</div> : toolFields.length ? <>
                <div className="settings-card-callout"><Wrench size={14} /><span>{t("settings.tools.saveHint")}</span></div>
                <ToolCatalog disabledToolkits={disabledToolkits} onToggleToolkit={toggleToolkit} />
              </> : <div className="settings-empty">{t("settings.tools.unavailable")}</div>}
            </SettingsVisualGroup>
          </section>}

          {activeSection === "skills" && <section className="settings-card settings-panel-card skills-settings-card">
            <div className="settings-card-heading"><div><h2>技能</h2><p>按需为助手添加专门能力。相关任务出现时，助手会自动读取对应说明。</p></div></div>
            <div className="skills-master-group">
              <h3 className="settings-group-label">通用</h3>
              <section className="skills-master-section" aria-label="技能总开关">
                {loading ? <div className="settings-loading"><RefreshCw size={15} className="spin" />读取技能设置…</div> : skillFields.length ? renderRuntimeFields(skillFields) : <div className="settings-empty">当前后端没有提供技能配置。</div>}
              </section>
            </div>
            <SkillsPanel />
          </section>}

          {activeSection === "review" && <section className="settings-card settings-panel-card">
            <div className="settings-card-heading"><div><span className="card-kicker">MEMORY REVIEW</span><h2>记忆整理</h2><p>管理每日整理的时间和使用模型。</p></div><Link className="ghost-button" href="/review" onClick={(event) => { if (!confirmAppNavigation()) event.preventDefault(); }}>每日回顾<ChevronRight size={13} /></Link></div>
            {loading ? <div className="settings-loading"><RefreshCw size={15} className="spin" />读取整理设置…</div> : reviewFields.length ? <>
              <SettingsVisualGroup icon={CalendarClock} title="自动整理" description="选择是否自动运行及每天的执行时间" tone="accent">{renderRuntimeFields(reviewPrimaryFields)}</SettingsVisualGroup>
              <details className="settings-disclosure settings-field-disclosure">
                <summary><span><strong><BrainCircuit size={14} />整理模型</strong><small>留空时沿用日常聊天模型</small></span><ChevronRight size={14} /></summary>
                {reviewAdvancedFields.length ? renderRuntimeFields(reviewAdvancedFields) : <div className="settings-empty">当前没有独立的整理模型配置。</div>}
              </details>
            </> : <div className="settings-empty">当前后端没有提供整理配置。</div>}
            <div className="settings-card-callout"><Clock3 size={14} /><span>关闭自动整理后，仍可在每日回顾页按需整理。</span></div>
          </section>}

          {/* 「时间线与提醒」和「手机提醒」原来是两个分区，但前者的字段**全部**是 notify_*，
              通知一关就整页失效。合成一页：先决定发不发，再决定什么时候发。 */}
          {activeSection === "reminders" && <section className="settings-card settings-panel-card notify-settings-panel">
            <div className="settings-card-heading"><div><span className="card-kicker">REMINDERS</span><h2>提醒</h2><p>决定要不要主动提醒你、推到哪台设备，以及提前多久。</p></div><div className={`tts-status-badge ${notifyStatus?.ready ? "success" : "neutral"}`}><span className="tts-status-dot" />{notifyStatus?.ready ? t("settings.notify.channelReady") : t("settings.notify.channelMissing")}</div></div>
            {loading ? <div className="settings-loading"><RefreshCw size={15} className="spin" />读取提醒设置…</div> : notifyFields.length ? <>
              <SettingsVisualGroup icon={BellRing} title="通知总览" description="控制是否发送主动提醒，并检查当前通道是否可以工作" tone="accent" className="notify-overview-group">
                <div className="notify-overview-grid">
                  <div className={`notify-toggle-card ${notifyEnabled ? "is-enabled" : ""}`}>
                    <div className="notify-toggle-card-heading"><span className="notify-overview-icon"><BellRing size={16} /></span><div><strong>{t("settings.notify.enabledLabel")}</strong><small>{notifyEnabled ? t("settings.notify.enabledHint") : t("settings.notify.disabledHint")}</small></div></div>
                    {notifyEnabledFields.length ? renderRuntimeFields(notifyEnabledFields) : <div className="notify-inline-empty">当前后端没有提供总开关。</div>}
                  </div>
                  <div className="notify-quick-card">
                    <div className="notify-quick-card-heading"><span className="notify-overview-icon"><Smartphone size={16} /></span><div><strong>{t("settings.notify.deliveryLabel")}</strong><small>{notifyStatus?.ready ? t("settings.notify.deliveryReady") : t("settings.notify.deliveryMissing")}</small></div></div>
                    <div className="notify-channel-summary">{(notifyStatus?.channels ?? []).map((channel) => <span className={channel.configured ? "ready" : ""} key={channel.name}><i />{channel.name}</span>)}</div>
                    <p className="notify-test-hint">{t("settings.notify.testHint")}</p>
                    <button className="ghost-button notify-test-button" type="button" onClick={() => void runNotifyTest()} disabled={notifyTesting || !notifyStatus?.ready}><Send size={13} />{notifyTesting ? t("settings.notify.testing") : t("settings.notify.test")}</button>
                  </div>
                </div>
                {notifyMessage && <div className="notify-test-message"><BellRing size={14} /><span>{notifyMessage}</span></div>}
              </SettingsVisualGroup>

              {/* 通道没配好之前，下面的提前量调了也送不出去 —— 顺序就是这个道理。 */}
              <SettingsVisualGroup icon={Smartphone} title="Bark 设备" description="只需粘贴一次设备 key；已保存的 key 不会再次回显" tone="success">
                <div className="notify-channel-list">{(notifyStatus?.channels ?? []).map((channel) => <div className={`notify-channel-row ${channel.configured ? "ready" : ""}`} key={channel.name}><span className="notify-channel-dot" /><div><strong>{channel.name}</strong><small>{channel.configured ? t("settings.notify.channelReady") : channel.reason || t("settings.notify.channelMissing")}</small></div><span className="notify-channel-flag">{channel.enabled ? "已启用" : "未启用"}</span></div>)}</div>
                {notifyDeliveryFields.length ? renderRuntimeFields(notifyDeliveryFields) : <div className="settings-empty">当前后端没有提供通道配置。</div>}
                <div className="settings-card-callout"><Smartphone size={14} /><span>在 Bark App 中复制设备 key 粘贴到这里。保存后只显示“已配置”，需要更换时直接输入新的 key。</span></div>
              </SettingsVisualGroup>

              {/* 提前量仍然会算进条目的 remind_at 并显示在时间线上，所以不隐藏 ——
                  但「算得出时刻」和「到点会提醒你」是两回事，别让人以为配了就有用。 */}
              <SettingsVisualGroup icon={CalendarClock} title="提醒规则" description="新建事项默认使用的提前量和全天事项提醒时间" tone="violet">
                {!notifyEnabled && <div className="settings-card-callout"><BellRing size={14} /><span>主动通知未开启，下面的时刻只会显示在时间线上，到点不会推送给你。</span></div>}
                {timelinePrimaryFields.length ? renderRuntimeFields(timelinePrimaryFields) : <div className="settings-empty">当前后端没有提供时间线规则配置。</div>}
                <div className="settings-card-callout"><Clock3 size={14} /><span>会议默认提前 15 分钟，出行和生日提前一天，截止日期提前三天；单条事项可以在时间线编辑页单独调整。</span></div>
              </SettingsVisualGroup>

              {/* 简报和补发都是「推送」行为，通知关着的时候它们一条也不会发出去。
                  摆在这里只会让人调完一圈发现什么都没收到。 */}
              {notifyEnabled && <details className="settings-disclosure settings-field-disclosure">
                <summary><span><strong><Gauge size={14} />每日简报与补发</strong><small>简报时间、错过提醒的补发窗口和文案生成</small></span><ChevronRight size={14} /></summary>
                {timelineAdvancedFields.length ? renderRuntimeFields(timelineAdvancedFields) : <div className="settings-empty">当前没有额外的提醒设置。</div>}
              </details>}
            </> : <div className="settings-empty">当前后端没有提供提醒配置。</div>}
          </section>}

          {activeSection === "voice" && <section className="settings-card settings-panel-card">
            <div className="settings-card-heading"><div><span className="card-kicker">VOICE</span><h2>语音</h2><p>回答朗读和录音输入，两者共用本机的 mlx-audio 服务。</p></div><div className="tts-section-tools"><div className={`tts-status-badge ${ttsPresentation.tone}`} title={ttsStatus?.detail || undefined}><span className="tts-status-dot" />{ttsPresentation.label}</div>{ttsMode !== "off" && <button className="ghost-button tts-preview-button" type="button" onClick={() => void runTtsPreview()} disabled={ttsPreviewLoading || ttsStatusLoading || !ttsStatus || ttsStatus.mode === "off" || !ttsStatus.enabled}><Headphones size={13} />{ttsPreviewLoading ? "试听中…" : "试听"}</button>}</div></div>
            {loading ? <div className="settings-loading"><RefreshCw size={15} className="spin" />读取语音设置…</div> : ttsFields.length ? <>
              <SettingsVisualGroup icon={Volume2} title="播放方式" description="关闭、手动播放或回答后自动朗读" tone="accent">
                {renderRuntimeFields(ttsModeFields)}
              </SettingsVisualGroup>
              {/* 语音关着的时候不摆出十几个输入框：朗读依赖宿主机的 mlx-audio 和几 GB 权重，
                  没做这两件事之前，那些字段只会让人以为它们是必配项。 */}
              {ttsMode === "off" ? <div className="settings-card-callout"><Headphones size={14} /><span>语音未启用，当前不会朗读回答。启用前需要在宿主机跑 <code>mlx_audio.server --port 8001</code>；首次合成会自动下载约 1.7GB 权重。选择「手动播放」或「自动朗读」后，这里会出现模型、音色和语速设置。</span></div> : <>
                <SettingsVisualGroup icon={AudioLines} title="声音与表达" description="选择模型、音色、语气和语速" tone="violet">
                  <div className="tts-model-overview"><div><span>本地缓存</span><strong>{ttsStatus?.cached_models?.length ?? 0} 个模型</strong></div><div><span>当前选择</span><strong title={selectedTtsModel}>{selectedTtsModel.split("/").at(-1) || "—"}</strong></div><div><span>状态</span><strong className={selectedModelLoaded ? "online" : selectedCachedModel ? "cached" : ""}>{selectedModelLoaded ? "已加载" : selectedCachedModel ? `已缓存 · ${formatModelSize(selectedCachedModel.size_bytes)}` : "未检测到缓存"}</strong></div></div>
                  {renderRuntimeFields(ttsPrimaryFields)}
                </SettingsVisualGroup>
                <details className="settings-disclosure settings-field-disclosure">
                  <summary><span><strong><Gauge size={14} />高级合成设置</strong><small>语种、格式、流式传输及性能限制</small></span><ChevronRight size={14} /></summary>
                  {ttsAdvancedFields.length ? renderRuntimeFields(ttsAdvancedFields) : <div className="settings-empty">当前没有高级合成配置。</div>}
                </details>
              </>}
            </> : <div className="settings-empty">当前后端没有提供语音配置。</div>}
            {(ttsStatusError || ttsStatus?.detail || ttsPreviewMessage) && <div className="tts-status-detail">{ttsStatusError || ttsStatus?.detail || ttsPreviewMessage}<button className="icon-button" type="button" aria-label="刷新语音服务状态" title="刷新状态" onClick={() => void refreshTtsStatus()} disabled={ttsStatusLoading}><RefreshCw size={12} className={ttsStatusLoading ? "spin" : ""} /></button></div>}
            <audio ref={previewAudioRef} className="tts-audio" onEnded={() => setTtsPreviewMessage("")} />

            {/* 语音输入原来是独立一页，但它和朗读共用同一个 mlx-audio 服务、同一份
                模型缓存、同一个「服务没起来」的状态。分成两页只会让人配两遍。 */}
              <SettingsVisualGroup icon={Mic2} title="识别设置" description={asrStatusLoading ? "正在检查识别服务" : !asrStatus?.reachable ? "识别服务离线" : asrStatus.loaded ? "识别模型已加载" : "录音时按需加载模型"} tone={asrStatus?.reachable ? "success" : "neutral"} className="settings-voice-input-group">
                {loading ? <div className="settings-loading"><RefreshCw size={15} className="spin" />读取识别设置…</div> : asrFields.length ? <>
                  {/* 和朗读同理：识别服务没起来时，模型和语言选项都还不能生效，
                      先说清楚缺什么，别让人对着一排下拉框猜。 */}
                  {asrStatus && !asrStatus.reachable ? <div className="settings-card-callout"><Mic2 size={14} /><span>识别服务未运行，语音输入按钮不可用。在宿主机跑 <code>mlx_audio.server --port 8001</code> 后刷新；首次识别会自动下载权重。</span></div> : <>
                    <div className="tts-model-overview"><div><span>本地缓存</span><strong>{asrStatus?.cached_models.length ?? 0} 个模型</strong></div><div><span>当前选择</span><strong title={selectedAsrModel}>{selectedAsrModel.split("/").at(-1) || "—"}</strong></div><div><span>状态</span><strong className={selectedAsrModelLoaded ? "online" : selectedCachedAsrModel ? "cached" : ""}>{selectedAsrModelLoaded ? "已加载" : selectedCachedAsrModel ? `已缓存 · ${formatModelSize(selectedCachedAsrModel.size_bytes)}` : "未检测到缓存"}</strong></div></div>
                    {renderRuntimeFields(asrPrimaryFields)}
                    {asrAdvancedFields.length > 0 && <details className="settings-disclosure settings-field-disclosure">
                      <summary><span><strong><Gauge size={14} />识别高级设置</strong><small>识别长度和请求超时</small></span><ChevronRight size={14} /></summary>
                      {renderRuntimeFields(asrAdvancedFields)}
                    </details>}
                  </>}
                </> : <div className="settings-empty">当前后端没有提供语音识别配置。</div>}
                {(asrStatusError || asrStatus?.detail) && <div className="tts-status-detail">{asrStatusError || asrStatus?.detail}<button className="icon-button" type="button" aria-label="刷新语音识别状态" onClick={() => void refreshAsrStatus()} disabled={asrStatusLoading}><RefreshCw size={12} className={asrStatusLoading ? "spin" : ""} /></button></div>}
              </SettingsVisualGroup>
          </section>}

          {activeSection === "system" && <section className="settings-card settings-panel-card">
            <div className="settings-card-heading"><div><span className="card-kicker">DATA & BACKUP</span><h2>数据与备份</h2><p>查看连接状态、创建快照并确认运行环境。</p></div><HardDriveDownload size={17} /></div>
            <SettingsVisualGroup icon={HardDriveDownload} title="连接与备份" description="查看当前服务状态并创建数据快照" tone="success">
              <div className="settings-summary-strip"><div><span>连接</span><strong className={health?.status === "ok" ? "value-success" : ""}>{connectionLabel}</strong></div><div><span>当前模型</span><strong>{runtime?.model ?? "—"}</strong></div><div><span>知识库</span><strong>{runtime ? runtime.kb_enabled ? "已挂载" : "未启用" : "—"}</strong></div></div>
              <div className="settings-system-actions"><div><strong>数据备份</strong><span>创建数据库和长期记忆的当前快照。</span></div><button className="ghost-button" onClick={() => void runBackup()} disabled={backupLoading}><HardDriveDownload size={13} />{backupLoading ? "备份中…" : "创建备份"}</button></div>
              {backup && <div className="settings-backup-result"><Download size={14} /><span>备份完成：{backup.dump_file} · {backup.memory_files} 个记忆文件 · 新增 {backup.attachment_files} 个附件 · {Math.round(backup.dump_bytes / 1024)} KB</span></div>}
              {systemFields.length > 0 && <details className="settings-disclosure settings-field-disclosure settings-backup-disclosure">
                <summary><span><strong><HardDriveDownload size={14} />自动备份</strong><small>备份频率和保留数量</small></span><ChevronRight size={14} /></summary>
                {renderRuntimeFields(systemFields)}
              </details>}
              <div className="settings-system-actions settings-danger-action"><div><strong>清空所有数据</strong><span>永久删除全部会话、记忆、每日回顾、时间线、关注事项、附件和使用记录。应用设置、模型配置和已安装技能会保留。</span></div><button className="danger-button" type="button" onClick={() => setClearAllDataPending(true)} disabled={clearingAllData}><Trash2 size={13} />清空所有数据</button></div>
              {clearAllDataMessage && <div className="settings-backup-result"><Check size={14} /><span>{clearAllDataMessage}</span></div>}
            </SettingsVisualGroup>
            <SettingsVisualGroup icon={ServerCog} title="运行与环境" description="服务地址、Provider 和需要重启的环境变量" tone="neutral" className="settings-runtime-group">
              <div className="settings-values">{loading ? <div className="settings-loading"><RefreshCw size={15} className="spin" />读取运行状态…</div> : <><SettingValue label="服务地址" value={apiBase} /><SettingValue label="Provider" value={runtime?.provider ?? "—"} /></>}</div>
              <EnvStatusList rows={runtime?.env_status ?? []} fallback={runtime?.env_only ?? []} />
            </SettingsVisualGroup>
          </section>}

          {activeSection === "advanced" && <section className="settings-card settings-panel-card">
            <div className="settings-card-heading"><div><span className="card-kicker">DEVELOPER</span><h2>开发者</h2><p>只在排查模型工具和请求问题时使用。</p></div><Bug size={17} /></div>
            <SettingsVisualGroup icon={Bug} title="开发者选项" description="工具定义与可能包含对话原文的请求调试" tone="warm" className="settings-developer-group" action={<button className="ghost-button" type="button" onClick={() => void openDebugPrompt()} disabled={debugPromptLoading}><Eye size={13} />{debugPromptLoading ? "读取中…" : "查看 System Prompt"}</button>}>
              <div className="settings-developer-stack">
                <details className="tts-advanced-settings settings-disclosure"><summary><span><strong>{t("settings.obs.title")}</strong><small>Phoenix 链路追踪：状态、入口和启用方式</small></span><ChevronRight size={14} /></summary><ObservabilityCard /></details>
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
    {avatarCropSource && <AvatarCropDialog source={avatarCropSource} onCancel={closeAvatarCrop} onConfirm={confirmProfileAvatar} />}
    {debugDialog && <DebugDialog kind={debugDialog} prompt={debugPrompt} request={debugDetail} loading={debugDialog === "prompt" ? debugPromptLoading : debugDetailLoading} error={debugError} copied={debugCopied} onClose={() => setDebugDialog(null)} onCopy={(text, target) => void copyDebugText(text, target)} />}
    <ConfirmDialog open={clearDebugPending} title="清空调试请求？" description="最近的模型请求快照会从后端内存中全部移除，服务重启后本来也会自动清空。" confirmLabel="清空请求" busy={clearingDebug} onCancel={() => setClearDebugPending(false)} onConfirm={() => void clearDebug()} />
    <ConfirmDialog open={clearAllDataPending} title="清空所有数据？" description="全部会话、消息、记忆、每日回顾、时间线、关注事项、附件和历史记录都会被永久删除，不能恢复。" warning="应用设置、模型配置和已安装技能不会删除；如果只想删除某一段会话，请使用侧栏里的单独删除。" confirmLabel="永久清空所有数据" busy={clearingAllData} onCancel={() => setClearAllDataPending(false)} onConfirm={() => void clearAllDataAction()} />
  </div>;
}
