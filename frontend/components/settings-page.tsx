"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Activity, BookOpen, CalendarDays, Check, ChevronRight, Clock3, MessageSquare, RefreshCw, Settings2, SlidersHorizontal, Sparkles, X } from "lucide-react";
import { errorMessage, getHealth, getRuntimeSettings } from "@/lib/api";
import { defaultPreferences, preferencesChangeEvent, readPreferences, writePreferences, type UserPreferences } from "@/lib/preferences";
import type { HealthStatus, RuntimeSettings } from "@/lib/types";
import { ThemeControl } from "@/components/theme-control";

function Toggle({ checked, onChange, label, description }: { checked: boolean; onChange: (checked: boolean) => void; label: string; description: string }) {
  return <label className="settings-toggle-row"><span className="settings-toggle-copy"><strong>{label}</strong><span>{description}</span></span><input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} /><span className="toggle-track" aria-hidden="true"><span /></span></label>;
}

function SettingValue({ label, value, tone = "" }: { label: string; value: string; tone?: string }) {
  return <div className="settings-value"><span>{label}</span><strong className={tone}>{value}</strong></div>;
}

export function SettingsPage() {
  const [runtime, setRuntime] = useState<RuntimeSettings | null>(null);
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [preferences, setPreferences] = useState<UserPreferences>(defaultPreferences);

  const loadRuntime = useCallback(async () => {
    setLoading(true);
    setError("");
    const [runtimeResult, healthResult] = await Promise.allSettled([getRuntimeSettings(), getHealth()]);
    if (runtimeResult.status === "fulfilled") setRuntime(runtimeResult.value);
    if (healthResult.status === "fulfilled") setHealth(healthResult.value);
    if (runtimeResult.status === "rejected" && healthResult.status === "rejected") setError(errorMessage(runtimeResult.reason, "无法读取后端设置"));
    setLoading(false);
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

  const updatePreference = <K extends keyof UserPreferences>(key: K, value: UserPreferences[K]) => {
    const next = { ...preferences, [key]: value };
    setPreferences(next);
    writePreferences(next);
  };

  const resetPreferences = () => {
    setPreferences(defaultPreferences);
    writePreferences(defaultPreferences);
  };

  const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
  const thinkingDefault = runtime ? runtime.thinking_default ? "开启" : "关闭" : "—";
  const connectionLabel = health?.status === "ok" ? "已连接" : health ? health.status : "未知";
  const hasConsolidationConfig = typeof runtime?.consolidate_auto === "boolean";
  const consolidationMode = hasConsolidationConfig ? runtime?.consolidate_auto ? "自动整理" : "手动触发" : "后端配置";
  const consolidationSchedule = hasConsolidationConfig ? runtime?.consolidate_auto ? `${String(runtime.consolidate_hour ?? 4).padStart(2, "0")}:00${runtime.timezone ? ` · ${runtime.timezone}` : ""}` : "按需触发" : "后端配置";
  const consolidationModel = runtime?.consolidate_model || runtime?.model || "—";

  return <div className="settings-shell">
    <header className="settings-topbar"><Link className="brand brand-home" href="/" aria-label="返回主页"><div className="brand-mark">✦</div><div><div className="brand-title">个人 AI 助手</div><div className="brand-subtitle">Settings</div></div></Link><div className="settings-topbar-tools"><nav className="settings-nav"><Link href="/"><MessageSquare size={14} /><span>聊天</span></Link><Link href="/memories"><BookOpen size={14} /><span>记忆管理</span></Link><Link href="/review"><CalendarDays size={14} /><span>每日回顾</span></Link><span className="active"><Settings2 size={14} /><span>设置</span></span></nav><ThemeControl /></div></header>
    <main className="settings-content">
      <div className="settings-heading"><div><div className="eyebrow">Workspace settings</div><h1>把助手调成你习惯的样子。</h1><p>聊天体验保存在当前浏览器，模型与整理任务由后端统一管理。主题可在顶部快捷切换。</p></div><Link className="ghost-button settings-back" href="/"><ChevronRight size={13} style={{ transform: "rotate(180deg)" }} />返回聊天</Link></div>
      {error && <div className="settings-error"><X size={15} /><span>{error}</span><button className="ghost-button" onClick={() => void loadRuntime()} disabled={loading}><RefreshCw size={12} />重试</button></div>}
      <div className="settings-grid">
        <section className="settings-card settings-chat-card"><div className="settings-card-heading"><div><span className="card-kicker">CHAT</span><h2>聊天体验</h2><p>这些选项只影响当前浏览器。</p></div><SlidersHorizontal size={17} /></div><div className="settings-toggle-list"><Toggle label="Enter 发送" description="按 Enter 发送消息，Shift + Enter 换行。" checked={preferences.enterToSend} onChange={(value) => updatePreference("enterToSend", value)} /><Toggle label="自动跟随新回答" description="流式回答时自动滚动到底部；手动上滑仍会暂停跟随。" checked={preferences.autoScroll} onChange={(value) => updatePreference("autoScroll", value)} /><Toggle label="显示思考过程" description="保留思考内容，但默认仍以折叠方式呈现。" checked={preferences.showThinking} onChange={(value) => updatePreference("showThinking", value)} /><Toggle label="显示记忆操作" description="显示助手读取、更新和删除记忆的状态条。" checked={preferences.showToolActivity} onChange={(value) => updatePreference("showToolActivity", value)} /><Toggle label="显示 token 用量" description="在每条已完成的回答下显示输出 token 数。" checked={preferences.showUsage} onChange={(value) => updatePreference("showUsage", value)} /></div><div className="settings-card-actions"><button className="ghost-button" onClick={resetPreferences}>恢复浏览器默认</button><span>浏览器配置</span></div></section>
        <section className="settings-card settings-review-card"><div className="settings-card-heading"><div><span className="card-kicker">MEMORY & REVIEW</span><h2>记忆与每日回顾</h2><p>当前显示后端状态；自动整理配置将在后端接口开放后可编辑。</p></div><Sparkles size={17} /></div><div className="settings-readonly-list"><div><span>整理方式</span><strong>{consolidationMode}</strong><em className="settings-scope-badge backend">后端</em></div><div><span>整理时间</span><strong>{consolidationSchedule}</strong><em className="settings-scope-badge backend">后端</em></div><div><span>整理模型</span><strong>{consolidationModel}</strong><em className="settings-scope-badge backend">后端</em></div></div><div className="settings-card-callout"><Clock3 size={14} /><span>每日回顾会保留摘要、记忆变更和用量记录；整理任务仍建议在回顾页按需触发。</span></div><Link className="settings-inline-link" href="/review">前往每日回顾<ChevronRight size={14} /></Link></section>
        <section className="settings-card settings-runtime-card"><div className="settings-card-heading"><div><span className="card-kicker">MODEL & CONNECTION</span><h2>模型与连接</h2><p>运行时信息来自后端，不在浏览器中保存 API Key。</p></div><Activity size={17} /></div><div className="settings-values">{loading ? <div className="settings-loading"><RefreshCw size={15} className="spin" />读取运行配置…</div> : <><SettingValue label="连接状态" value={connectionLabel} tone={health?.status === "ok" ? "value-success" : ""} /><SettingValue label="服务地址" value={apiBase} /><SettingValue label="Provider" value={runtime?.provider ?? "—"} /><SettingValue label="模型" value={runtime?.model ?? "—"} /><SettingValue label="默认思考" value={thinkingDefault} /><SettingValue label="会话级开关" value={runtime ? runtime.thinking_toggle ? "可用" : "不可用" : "—"} /></>}</div>{health?.status === "ok" && <div className="settings-runtime-footer"><span className="status-dot online" /><strong>后端服务正常</strong>{health.provider && health.model && <span>{health.provider} · {health.model}</span>}<Check size={14} /></div>}</section>
      </div>
      <p className="settings-note">Provider、模型、自动整理策略和时区需要在后端环境或后端设置接口中修改；外观和聊天体验可以立即在本地生效。</p>
    </main>
  </div>;
}
