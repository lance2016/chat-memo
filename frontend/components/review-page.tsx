"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { CheckCircle2, ChevronLeft, ChevronRight, LoaderCircle, Play, RefreshCw, Settings2, TriangleAlert } from "lucide-react";
import { consolidate, errorMessage, getDailyUsage, listAllMemoryVersions, listConversations, listSummaries, restoreMemoryVersion } from "@/lib/api";
import type { Conversation, ConversationSummary, ConsolidateResult, DailyUsage, MemoryVersion } from "@/lib/types";
import { ReviewConversationList } from "@/components/review/review-conversation-list";
import { ReviewMemoryChanges } from "@/components/review/review-memory-changes";
import { ReviewOverview } from "@/components/review/review-overview";
import { ReviewSummaryList } from "@/components/review/review-summary-list";
import { ReviewUsageCard } from "@/components/review/review-usage-card";
import { WorkspaceTopbar } from "@/components/workspace-topbar";

type SectionKey = "conversations" | "changes" | "summaries" | "usage";
type SectionErrors = Partial<Record<SectionKey, string>>;

function today() {
  const now = new Date();
  const parts = new Intl.DateTimeFormat("en-CA", { year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(now);
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}`;
}

function localDay(value: string) {
  const parts = new Intl.DateTimeFormat("en-CA", { year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(new Date(value));
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}`;
}

function formatDayTitle(value: string) {
  return new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "long", day: "numeric", weekday: "long" }).format(new Date(`${value}T12:00:00`));
}

function moveDay(value: string, amount: number) {
  const date = new Date(`${value}T12:00:00`);
  date.setDate(date.getDate() + amount);
  const parts = new Intl.DateTimeFormat("en-CA", { year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(date);
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}`;
}

function isFulfilled<T>(result: PromiseSettledResult<T>): result is PromiseFulfilledResult<T> {
  return result.status === "fulfilled";
}

export function ReviewPage() {
  const [day, setDay] = useState(today);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [changes, setChanges] = useState<MemoryVersion[]>([]);
  const [summaries, setSummaries] = useState<ConversationSummary[]>([]);
  const [usage, setUsage] = useState<DailyUsage[]>([]);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<ConsolidateResult | null>(null);
  const [error, setError] = useState("");
  const [sectionErrors, setSectionErrors] = useState<SectionErrors>({});

  const loadReview = useCallback(async (selectedDay: string) => {
    setLoading(true);
    setError("");
    setResult(null);
    const [activeResult, archivedResult, changesResult, summariesResult, usageResult] = await Promise.allSettled([
      listConversations(200),
      listConversations(200, true),
      listAllMemoryVersions({ day: selectedDay, limit: 100 }),
      listSummaries({ day: selectedDay, limit: 100 }),
      getDailyUsage(7),
    ]);

    const nextErrors: SectionErrors = {};
    if (!isFulfilled(activeResult) && !isFulfilled(archivedResult)) nextErrors.conversations = errorMessage(activeResult.reason, "无法加载当天会话");
    else if (!isFulfilled(activeResult) || !isFulfilled(archivedResult)) nextErrors.conversations = "部分会话列表暂时无法加载";
    if (!isFulfilled(changesResult)) nextErrors.changes = errorMessage(changesResult.reason, "无法加载记忆变更");
    if (!isFulfilled(summariesResult)) nextErrors.summaries = errorMessage(summariesResult.reason, "无法加载会话摘要");
    if (!isFulfilled(usageResult)) nextErrors.usage = errorMessage(usageResult.reason, "无法加载用量统计");

    const allConversations = [
      ...(isFulfilled(activeResult) ? activeResult.value : []),
      ...(isFulfilled(archivedResult) ? archivedResult.value : []),
    ];
    const uniqueConversations = Array.from(new Map(allConversations.map((conversation) => [conversation.id, conversation])).values());
    setConversations(uniqueConversations.filter((conversation) => localDay(conversation.updated_at) === selectedDay));
    if (isFulfilled(changesResult)) setChanges(changesResult.value);
    if (isFulfilled(summariesResult)) setSummaries(summariesResult.value);
    if (isFulfilled(usageResult)) setUsage(usageResult.value);
    setSectionErrors(nextErrors);
    if (Object.keys(nextErrors).length) setError("部分数据加载失败，可以点击重试；可用区块仍然保留。");
    setLoading(false);
  }, []);

  useEffect(() => { void loadReview(day); }, [day, loadReview]);

  const runConsolidation = async () => {
    if (running) return;
    setRunning(true);
    setError("");
    try {
      const summary = await consolidate(day);
      await loadReview(day);
      setResult(summary);
    } catch (cause) {
      setError(errorMessage(cause, "整理失败"));
    } finally {
      setRunning(false);
    }
  };

  const restoreDeletedMemory = async (change: MemoryVersion) => {
    if (change.operation !== "deleted") return;
    if (!window.confirm(`恢复已删除的 ${change.path} 吗？这会按该版本内容重新创建文件。`)) return;
    try {
      await restoreMemoryVersion(change.id);
      await loadReview(day);
    } catch (cause) {
      setError(errorMessage(cause, "恢复记忆失败"));
    }
  };

  const latestSummary = summaries[0];
  const isToday = day === today();

  return <div className="review-shell">
    <WorkspaceTopbar active="review" subtitle="Daily review" />
    <main className="review-content">
      <div className="review-heading"><div><div className="eyebrow">Daily review</div><h1>{isToday ? "回看今天。" : "回看这一天。"}</h1><p>{formatDayTitle(day)} · 把对话、记忆和使用情况放在同一条脉络里。</p></div><div className="review-controls"><div className="date-control"><button className="icon-button date-step" aria-label="前一天" onClick={() => setDay((value) => moveDay(value, -1))}><ChevronLeft size={16} /></button><label htmlFor="review-day">选择日期</label><input id="review-day" type="date" value={day} onChange={(event) => setDay(event.target.value)} /><button className="icon-button date-step" aria-label="后一天" onClick={() => setDay((value) => moveDay(value, 1))}><ChevronRight size={16} /></button></div>{latestSummary && <span className="review-status-chip"><CheckCircle2 size={12} />已整理 · {new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit" }).format(new Date(latestSummary.created_at))}</span>}{!isToday && <button className="ghost-button today-button" onClick={() => setDay(today())}><RefreshCw size={13} />今天</button>}<Link className="ghost-button review-settings-link" href="/settings"><Settings2 size={13} />整理设置</Link><button className="primary-button" onClick={() => void runConsolidation()} disabled={running}>{running ? <><LoaderCircle size={14} className="spin" />整理中…</> : <><Play size={14} />{latestSummary ? "重新整理这一天" : "整理这一天"}</>}</button></div></div>
      {error && <div className="review-error-banner"><TriangleAlert size={15} /><span>{error}</span><button className="ghost-button" onClick={() => void loadReview(day)} disabled={loading}><RefreshCw size={12} />重试</button></div>}
      {result && <div className={`review-result ${result.failed_summaries > 0 ? "review-warning" : ""}`}><CheckCircle2 size={17} /><div><strong>{result.skipped ? "这一天没有需要整理的对话" : "整理完成"}</strong><span>{result.detail || `摘要 ${result.summarized_conversations} 个 · 工具调用 ${result.tool_calls} 次 · 记忆写入 ${result.memory_writes} 次`}</span></div></div>}
      {loading ? <div className="review-loading"><LoaderCircle size={18} className="spin" />正在读取这一天的记录…</div> : <>
        <ReviewOverview conversations={conversations} summaries={summaries} changes={changes} />
        <div className="review-layout"><div className="review-main-column"><ReviewSummaryList summaries={summaries} error={sectionErrors.summaries} /><ReviewMemoryChanges changes={changes} error={sectionErrors.changes} onRestore={(change) => void restoreDeletedMemory(change)} /></div><aside className="review-side-column"><ReviewConversationList conversations={conversations} error={sectionErrors.conversations} /><ReviewUsageCard usage={usage} selectedDay={day} error={sectionErrors.usage} /></aside></div>
      </>}
      <div className="review-note"><TriangleAlert size={13} />摘要只在运行每日整理后生成；记忆时间线包含已删除文件的历史快照。</div>
    </main>
  </div>;
}
