"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { CheckCircle2, ChevronLeft, ChevronRight, LoaderCircle, Play, RefreshCw, Settings2, TriangleAlert } from "lucide-react";
import { closeOpenLoop, consolidate, createOpenLoop, dropOpenLoop, errorMessage, getDailyUsage, getDigest, listAllMemoryVersions, listConversations, listOpenLoops, listSummaries, reopenOpenLoop, restoreMemoryVersion } from "@/lib/api";
import type { Conversation, ConversationSummary, ConsolidateResult, DailyDigest, DailyUsage, MemoryVersion, OpenLoop } from "@/lib/types";
import { ReviewConversationList } from "@/components/review/review-conversation-list";
import { ReviewDigest } from "@/components/review/review-digest";
import { ReviewMemoryChanges } from "@/components/review/review-memory-changes";
import { ReviewOpenLoops } from "@/components/review/review-open-loops";
import { ReviewSummaryList } from "@/components/review/review-summary-list";
import { ReviewUsageCard } from "@/components/review/review-usage-card";
import { LatestRequest } from "@/lib/latest-request";
import { ConfirmDialog } from "@/components/confirm-dialog";

type SectionKey = "conversations" | "changes" | "summaries" | "usage" | "digest" | "loops";
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

function validDay(value: string | null): value is string {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const date = new Date(`${value}T12:00:00`);
  return !Number.isNaN(date.getTime()) && moveDay(value, 0) === value;
}

function isFulfilled<T>(result: PromiseSettledResult<T>): result is PromiseFulfilledResult<T> {
  return result.status === "fulfilled";
}

export function ReviewPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const requestedDay = searchParams.get("day");
  const day = validDay(requestedDay) ? requestedDay : today();
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [changes, setChanges] = useState<MemoryVersion[]>([]);
  const [summaries, setSummaries] = useState<ConversationSummary[]>([]);
  const [usage, setUsage] = useState<DailyUsage[]>([]);
  const [digest, setDigest] = useState<DailyDigest | null>(null);
  const [loops, setLoops] = useState<OpenLoop[]>([]);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [restoreTarget, setRestoreTarget] = useState<MemoryVersion | null>(null);
  const [restoring, setRestoring] = useState(false);
  const [result, setResult] = useState<ConsolidateResult | null>(null);
  const [error, setError] = useState("");
  const [sectionErrors, setSectionErrors] = useState<SectionErrors>({});
  const [loadedDay, setLoadedDay] = useState("");
  const reviewRequestsRef = useRef(new LatestRequest());
  const selectedDayRef = useRef(day);

  useEffect(() => {
    selectedDayRef.current = day;
  }, [day]);

  useEffect(() => {
    if (!validDay(requestedDay)) router.replace(`/review?day=${day}`, { scroll: false });
  }, [day, requestedDay, router]);

  useEffect(() => () => reviewRequestsRef.current.invalidate(), []);

  const loadReview = useCallback(async (selectedDay: string) => {
    const request = reviewRequestsRef.current.begin();
    setLoading(true);
    setError("");
    setResult(null);
    const [activeResult, archivedResult, changesResult, summariesResult, usageResult, digestResult, loopsResult] = await Promise.allSettled([
      listConversations(200),
      listConversations(200, true),
      listAllMemoryVersions({ day: selectedDay, limit: 100 }),
      listSummaries({ day: selectedDay, limit: 100 }),
      getDailyUsage(7),
      getDigest(selectedDay),
      listOpenLoops(selectedDay),
    ]);
    if (!reviewRequestsRef.current.isCurrent(request)) return;

    const nextErrors: SectionErrors = {};
    if (!isFulfilled(activeResult) && !isFulfilled(archivedResult)) nextErrors.conversations = errorMessage(activeResult.reason, "无法加载当天会话");
    else if (!isFulfilled(activeResult) || !isFulfilled(archivedResult)) nextErrors.conversations = "部分会话列表暂时无法加载";
    if (!isFulfilled(changesResult)) nextErrors.changes = errorMessage(changesResult.reason, "无法加载记忆变更");
    if (!isFulfilled(summariesResult)) nextErrors.summaries = errorMessage(summariesResult.reason, "无法加载会话摘要");
    if (!isFulfilled(usageResult)) nextErrors.usage = errorMessage(usageResult.reason, "无法加载用量统计");
    if (!isFulfilled(digestResult)) nextErrors.digest = errorMessage(digestResult.reason, "无法加载今日回顾");
    if (!isFulfilled(loopsResult)) nextErrors.loops = errorMessage(loopsResult.reason, "无法加载悬而未决");

    const allConversations = [
      ...(isFulfilled(activeResult) ? activeResult.value : []),
      ...(isFulfilled(archivedResult) ? archivedResult.value : []),
    ];
    const uniqueConversations = Array.from(new Map(allConversations.map((conversation) => [conversation.id, conversation])).values());
    setConversations(uniqueConversations.filter((conversation) => localDay(conversation.updated_at) === selectedDay));
    if (isFulfilled(changesResult)) setChanges(changesResult.value);
    if (isFulfilled(summariesResult)) setSummaries(summariesResult.value);
    if (isFulfilled(usageResult)) setUsage(usageResult.value);
    if (isFulfilled(digestResult)) setDigest(digestResult.value);
    if (isFulfilled(loopsResult)) setLoops(loopsResult.value);
    setSectionErrors(nextErrors);
    setLoadedDay(selectedDay);
    if (Object.keys(nextErrors).length) setError("部分数据加载失败，可以点击重试；可用区块仍然保留。");
    setLoading(false);
  }, []);

  useEffect(() => { void loadReview(day); }, [day, loadReview]);

  const runConsolidation = async () => {
    if (running) return;
    const targetDay = day;
    setRunning(true);
    setError("");
    try {
      const summary = await consolidate(targetDay);
      if (selectedDayRef.current !== targetDay) return;
      await loadReview(targetDay);
      if (selectedDayRef.current === targetDay) setResult(summary);
    } catch (cause) {
      setError(errorMessage(cause, "整理失败"));
    } finally {
      setRunning(false);
    }
  };

  // 只刷这一块，不走 loadReview —— 勾掉一条待办不该让整页闪一次加载态。
  const mutateLoops = useCallback(async (task: () => Promise<unknown>) => {
    const targetDay = selectedDayRef.current;
    try {
      await task();
      const next = await listOpenLoops(targetDay);
      if (selectedDayRef.current === targetDay) setLoops(next);
    } catch (cause) {
      setError(errorMessage(cause, "更新待办失败"));
    }
  }, []);

  const requestRestoreDeletedMemory = (change: MemoryVersion) => {
    if (change.operation !== "deleted") return;
    setRestoreTarget(change);
  };

  const confirmRestoreDeletedMemory = async () => {
    const change = restoreTarget;
    if (!change || restoring) return;
    setRestoring(true);
    try {
      await restoreMemoryVersion(change.id);
      await loadReview(day);
      setRestoreTarget(null);
    } catch (cause) {
      setError(errorMessage(cause, "恢复记忆失败"));
    } finally {
      setRestoring(false);
    }
  };

  const selectDay = (nextDay: string) => {
    if (!validDay(nextDay) || nextDay === day) return;
    selectedDayRef.current = nextDay;
    router.replace(`/review?day=${nextDay}`, { scroll: false });
  };

  // 「已整理」看的是 digest 而不是摘要：摘要可能因为当天没东西可记而为空，
  // digest 在就说明这一天真的被回顾过了。
  const dayDigest = loadedDay === day ? digest : null;
  const isToday = day === today();

  return <div className="review-shell">
    <main className="review-content">
      <div className="review-heading"><div><div className="eyebrow">Daily review</div><h1>{isToday ? "回看今天。" : "回看这一天。"}</h1><p>{formatDayTitle(day)} · 把对话、记忆和使用情况放在同一条脉络里。</p></div><div className="review-controls"><div className="date-control"><button className="icon-button date-step" aria-label="前一天" onClick={() => selectDay(moveDay(day, -1))}><ChevronLeft size={16} /></button><label htmlFor="review-day">选择日期</label><input id="review-day" type="date" value={day} onChange={(event) => selectDay(event.target.value)} /><button className="icon-button date-step" aria-label="后一天" onClick={() => selectDay(moveDay(day, 1))}><ChevronRight size={16} /></button></div>{dayDigest && <span className="review-status-chip"><CheckCircle2 size={12} />已整理 · {new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit" }).format(new Date(dayDigest.updated_at))}</span>}{!isToday && <button className="ghost-button today-button" onClick={() => selectDay(today())}><RefreshCw size={13} />今天</button>}<Link className="ghost-button review-settings-link" href="/settings"><Settings2 size={13} />整理设置</Link><button className="primary-button" onClick={() => void runConsolidation()} disabled={running}>{running ? <><LoaderCircle size={14} className="spin" />整理中…</> : <><Play size={14} />{dayDigest ? "重新整理这一天" : "整理这一天"}</>}</button></div></div>
      {error && <div className="review-error-banner"><TriangleAlert size={15} /><span>{error}</span><button className="ghost-button" onClick={() => void loadReview(day)} disabled={loading}><RefreshCw size={12} />重试</button></div>}
      {result && <div className={`review-result ${result.failed_summaries > 0 || result.digest_failed ? "review-warning" : ""}`}><CheckCircle2 size={17} /><div><strong>{result.skipped ? "这一天没有需要整理的对话" : result.headline || "整理完成"}</strong><span>{result.digest_failed ? "回顾生成失败，记忆已照常整理 —— 可以再跑一次" : result.detail || `记忆写入 ${result.memory_writes} 次 · 新增待办 ${result.new_loops} 条 · 闭环 ${result.closed_loops} 条`}</span></div></div>}
      {loading ? <div className="review-loading"><LoaderCircle size={18} className="spin" />正在读取这一天的记录…</div> : <>
        <ReviewDigest digest={digest} error={sectionErrors.digest} running={running} onRun={() => void runConsolidation()} />
        <ReviewOpenLoops
          loops={loops}
          day={day}
          error={sectionErrors.loops}
          onClose={(loop) => mutateLoops(() => closeOpenLoop(loop.id))}
          onReopen={(loop) => mutateLoops(() => reopenOpenLoop(loop.id))}
          onDrop={(loop) => mutateLoops(() => dropOpenLoop(loop.id))}
          onCreate={(text) => mutateLoops(() => createOpenLoop(text, day))}
        />
        <details className="review-details">
          <summary>细节：会话摘要、记忆变更、用量</summary>
          <div className="review-layout"><div className="review-main-column"><ReviewSummaryList summaries={summaries} error={sectionErrors.summaries} /><ReviewMemoryChanges changes={changes} error={sectionErrors.changes} onRestore={requestRestoreDeletedMemory} /></div><aside className="review-side-column"><ReviewConversationList conversations={conversations} error={sectionErrors.conversations} /><ReviewUsageCard usage={usage} selectedDay={day} error={sectionErrors.usage} /></aside></div>
        </details>
      </>}
      <div className="review-note"><TriangleAlert size={13} />回顾和摘要只在运行每日整理后生成；记忆时间线包含已删除文件的历史快照。</div>
      <ConfirmDialog
        open={restoreTarget !== null}
        title="恢复已删除的记忆？"
        description="这会按历史快照重新创建记忆文件，并保留完整的恢复记录。"
        subject={restoreTarget?.path}
        confirmLabel="恢复记忆"
        busy={restoring}
        onCancel={() => setRestoreTarget(null)}
        onConfirm={() => void confirmRestoreDeletedMemory()}
      />
    </main>
  </div>;
}
