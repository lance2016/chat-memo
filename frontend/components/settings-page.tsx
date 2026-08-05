"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Activity, BookOpen, CalendarDays, Check, ChevronRight, MessageSquare, RefreshCw, Settings2, SlidersHorizontal, Sparkles, Wifi, X } from "lucide-react";
import { errorMessage, getHealth, getRuntimeSettings } from "@/lib/api";
import { defaultPreferences, preferencesChangeEvent, readPreferences, writePreferences, type UserPreferences } from "@/lib/preferences";
import type { HealthStatus, RuntimeSettings } from "@/lib/types";

function Toggle({ checked, onChange, label, description }: { checked: boolean; onChange: (checked: boolean) => void; label: string; description: string }) {
  return <label className="settings-toggle-row"><span className="settings-toggle-copy"><strong>{label}</strong><span>{description}</span></span><input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} /><span className="toggle-track" aria-hidden="true"><span /></span></label>;
}

function SettingValue({ label, value }: { label: string; value: string }) {
  return <div className="settings-value"><span>{label}</span><strong>{value}</strong></div>;
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

  const updatePreference = (key: keyof UserPreferences, value: boolean) => {
    const next = { ...preferences, [key]: value };
    setPreferences(next);
    writePreferences(next);
  };

  const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
  const thinkingDefault = runtime ? runtime.thinking_default ? "开启" : "关闭" : "—";
  const connectionLabel = health?.status === "ok" ? "已连接" : health ? health.status : "未知";

  return <div className="settings-shell">
    <header className="settings-topbar"><Link className="brand brand-home" href="/" aria-label="返回主页"><div className="brand-mark">✦</div><div><div className="brand-title">个人 AI 助手</div><div className="brand-subtitle">Settings</div></div></Link><nav className="settings-nav"><Link href="/"><MessageSquare size={14} />聊天</Link><Link href="/memories"><BookOpen size={14} />记忆管理</Link><Link href="/review"><CalendarDays size={14} />每日回顾</Link><span className="active"><Settings2 size={14} />设置</span></nav></header>
    <main className="settings-content">
      <div className="settings-heading"><div><div className="eyebrow">Workspace settings</div><h1>把助手调成你习惯的样子。</h1><p>运行时模型由后端管理，聊天体验偏好保存在当前浏览器。</p></div><Link className="ghost-button settings-back" href="/"><ChevronRight size={13} style={{ transform: "rotate(180deg)" }} />返回聊天</Link></div>
      {error && <div className="settings-error"><X size={15} /><span>{error}</span><button className="ghost-button" onClick={() => void loadRuntime()} disabled={loading}><RefreshCw size={12} />重试</button></div>}
      <div className="settings-grid">
        <section className="settings-card settings-runtime-card"><div className="settings-card-heading"><div><span className="card-kicker">RUNTIME</span><h2>运行环境</h2><p>这些值来自后端，不在浏览器中保存 API Key。</p></div><Activity size={17} /></div><div className="settings-values">{loading ? <div className="settings-loading"><RefreshCw size={15} className="spin" />读取运行配置…</div> : <><SettingValue label="连接状态" value={connectionLabel} /><SettingValue label="服务地址" value={apiBase} /><SettingValue label="Provider" value={runtime?.provider ?? "—"} /><SettingValue label="模型" value={runtime?.model ?? "—"} /><SettingValue label="默认思考" value={thinkingDefault} /><SettingValue label="会话级开关" value={runtime ? runtime.thinking_toggle ? "可用" : "不可用" : "—"} /></>}</div></section>
        <section className="settings-card"><div className="settings-card-heading"><div><span className="card-kicker">PREFERENCES</span><h2>聊天偏好</h2><p>只影响当前浏览器，不会改动后端配置。</p></div><SlidersHorizontal size={17} /></div><div className="settings-toggle-list"><Toggle label="Enter 发送" description="按 Enter 发送消息，Shift + Enter 换行。" checked={preferences.enterToSend} onChange={(value) => updatePreference("enterToSend", value)} /><Toggle label="自动跟随新回答" description="流式回答时自动滚动到底部；手动上滑仍会暂停跟随。" checked={preferences.autoScroll} onChange={(value) => updatePreference("autoScroll", value)} /></div></section>
        <section className="settings-card settings-review-card"><div className="settings-card-heading"><div><span className="card-kicker">DAILY REVIEW</span><h2>每日回顾</h2><p>整理任务默认手动触发，避免进程重启或设备休眠导致任务漏跑。</p></div><Sparkles size={17} /></div><div className="settings-info-row"><div className="settings-info-icon"><CalendarDays size={15} /></div><div><strong>整理方式</strong><span>手动整理 · 在每日回顾页面选择日期后触发</span></div></div><Link className="settings-inline-link" href="/review">前往每日回顾<ChevronRight size={14} /></Link></section>
        <section className="settings-card settings-help-card"><div className="settings-card-heading"><div><span className="card-kicker">STATUS</span><h2>当前状态</h2><p>快速确认服务是否正常工作。</p></div><Wifi size={17} /></div><div className="settings-status"><span className={`status-dot ${health?.status === "ok" ? "online" : ""}`} /><strong>{connectionLabel}</strong><span>{health?.provider && health.model ? `${health.provider} · ${health.model}` : "等待后端返回状态"}</span>{health?.status === "ok" && <Check size={14} />}</div></section>
      </div>
      <p className="settings-note">模型、Provider 和自动整理策略需要在后端环境配置中修改；当前前端只展示运行时设置，并提供浏览器侧偏好。</p>
    </main>
  </div>;
}
