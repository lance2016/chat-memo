"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { CheckCircle2, ChevronLeft, ChevronRight, LoaderCircle, Play, RefreshCw, TriangleAlert } from "lucide-react";
import { closeOpenLoop, consolidate, createOpenLoop, dropOpenLoop, errorMessage, getDailyUsage, getDigest, listAllMemoryVersions, listConversations, listOpenLoops, listReviewDays, listSummaries, reopenOpenLoop, restoreMemoryVersion } from "@/lib/api";
import type { Conversation, ConversationSummary, ConsolidateResult, DailyDigest, DailyUsage, MemoryVersion, OpenLoop } from "@/lib/types";
import { ReviewConversationList } from "@/components/review/review-conversation-list";
import { ReviewDigest } from "@/components/review/review-digest";
import { ReviewMemoryChanges } from "@/components/review/review-memory-changes";
import { ReviewOpenLoops } from "@/components/review/review-open-loops";
import { ReviewSummaryList } from "@/components/review/review-summary-list";
import { ReviewUsageCard } from "@/components/review/review-usage-card";
import { LatestRequest } from "@/lib/latest-request";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { useI18n } from "@/components/i18n-provider";

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

function formatDayTitle(value: string, locale: string) {
  return new Intl.DateTimeFormat(locale, { year: "numeric", month: "long", day: "numeric", weekday: "long" }).format(new Date(`${value}T12:00:00`));
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
  const { locale, t } = useI18n();
  const router = useRouter();
  const searchParams = useSearchParams();
  const requestedDay = searchParams.get("day");
  const [availableDays, setAvailableDays] = useState<string[] | null>(null);
  const day = validDay(requestedDay) && (availableDays === null || availableDays.includes(requestedDay))
    ? requestedDay
    : availableDays?.[0] ?? today();
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
    let active = true;
    void listReviewDays()
      .then((days) => { if (active) setAvailableDays(days); })
      .catch((cause) => {
        if (!active) return;
        setAvailableDays([]);
        setError(errorMessage(cause, t("review.days.error")));
      });
    return () => { active = false; };
  }, [t]);

  useEffect(() => {
    if (availableDays === null || availableDays.length === 0) return;
    if (requestedDay !== day) router.replace(`/review?day=${day}`, { scroll: false });
  }, [availableDays, day, requestedDay, router]);

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
    if (!isFulfilled(activeResult) && !isFulfilled(archivedResult)) nextErrors.conversations = errorMessage(activeResult.reason, t("review.conversationsError"));
    else if (!isFulfilled(activeResult) || !isFulfilled(archivedResult)) nextErrors.conversations = t("review.partialConversationsError");
    if (!isFulfilled(changesResult)) nextErrors.changes = errorMessage(changesResult.reason, t("review.changesError"));
    if (!isFulfilled(summariesResult)) nextErrors.summaries = errorMessage(summariesResult.reason, t("review.summariesError"));
    if (!isFulfilled(usageResult)) nextErrors.usage = errorMessage(usageResult.reason, t("review.usageError"));
    if (!isFulfilled(digestResult)) nextErrors.digest = errorMessage(digestResult.reason, t("review.digestError"));
    if (!isFulfilled(loopsResult)) nextErrors.loops = errorMessage(loopsResult.reason, t("review.followUps.loadError"));

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
    if (Object.keys(nextErrors).length) setError(t("review.partialError"));
    setLoading(false);
  }, [t]);

  useEffect(() => {
    if (availableDays?.includes(day)) void loadReview(day);
  }, [availableDays, day, loadReview]);

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
      setError(errorMessage(cause, t("review.runError")));
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
      setError(errorMessage(cause, t("review.followUps.updateError")));
    }
  }, [t]);

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
      setError(errorMessage(cause, t("review.restoreError")));
    } finally {
      setRestoring(false);
    }
  };

  const selectDay = (nextDay: string) => {
    if (!validDay(nextDay) || !availableDays?.includes(nextDay) || nextDay === day) return;
    selectedDayRef.current = nextDay;
    router.replace(`/review?day=${nextDay}`, { scroll: false });
  };

  // 「已整理」看的是 digest 而不是摘要：摘要可能因为当天没东西可记而为空，
  // digest 在就说明这一天真的被回顾过了。
  const dayDigest = loadedDay === day ? digest : null;
  const isToday = day === today();
  const dayIndex = availableDays?.indexOf(day) ?? -1;
  const olderDay = dayIndex >= 0 ? availableDays?.[dayIndex + 1] : undefined;
  const newerDay = dayIndex > 0 ? availableDays?.[dayIndex - 1] : undefined;
  const todayIsAvailable = availableDays?.includes(today()) ?? false;

  if (availableDays === null) return <div className="review-shell"><main className="review-content"><div className="review-loading review-page-state"><LoaderCircle size={18} className="spin" />{t("review.days.loading")}</div></main></div>;

  if (availableDays.length === 0) return <div className="review-shell"><main className="review-content"><div className="review-page-header review-page-header-empty"><div className="review-title-block"><div className="eyebrow">{t("review.eyebrow")}</div><h1>{t("review.days.emptyTitle")}</h1><p>{t("review.days.emptyDescription")}</p></div></div>{error && <div className="review-error-banner"><TriangleAlert size={15} /><span>{error}</span></div>}</main></div>;

  return <div className="review-shell">
    <main className="review-content">
      <header className="review-page-header">
        <div className="review-title-block">
          <div className="review-title-meta">
            <span className="eyebrow">{t("review.eyebrow")}</span>
            {dayDigest && <span className="review-status-chip"><CheckCircle2 size={12} />{t("review.status.done", { time: new Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit" }).format(new Date(dayDigest.updated_at)) })}</span>}
          </div>
          <h1>{isToday ? t("review.title.today") : t("review.title.day")}</h1>
          <p>{t("review.subtitle", { date: formatDayTitle(day, locale) })}</p>
        </div>

        <div className="review-toolbar" role="toolbar" aria-label={t("review.date.select")}>
          <div className="date-control review-date-control">
            <button className="icon-button date-step" type="button" aria-label={t("review.date.previous")} disabled={!olderDay} onClick={() => olderDay && selectDay(olderDay)}><ChevronLeft size={16} /></button>
            <label className="review-date-label" htmlFor="review-day">{t("review.date.select")}</label>
            <select id="review-day" value={day} onChange={(event) => selectDay(event.target.value)}>{availableDays.map((value) => <option value={value} key={value}>{value.replaceAll("-", "/")}</option>)}</select>
            <button className="icon-button date-step" type="button" aria-label={t("review.date.next")} disabled={!newerDay} onClick={() => newerDay && selectDay(newerDay)}><ChevronRight size={16} /></button>
          </div>
          {!isToday && todayIsAvailable && <button className="icon-button review-toolbar-icon" type="button" aria-label={t("review.today")} title={t("review.today")} onClick={() => selectDay(today())}><RefreshCw size={15} /></button>}
          <button className="primary-button review-run-button" type="button" onClick={() => void runConsolidation()} disabled={running}>{running ? <><LoaderCircle size={14} className="spin" />{t("review.running")}</> : <><Play size={14} />{dayDigest ? t("review.rerun") : t("review.run")}</>}</button>
        </div>
      </header>
      {error && <div className="review-error-banner"><TriangleAlert size={15} /><span>{error}</span><button className="ghost-button" onClick={() => void loadReview(day)} disabled={loading}><RefreshCw size={12} />{t("review.retry")}</button></div>}
      {result && <div className={`review-result ${result.failed_summaries > 0 || result.digest_failed ? "review-warning" : ""}`}><CheckCircle2 size={17} /><div><strong>{result.skipped ? t("review.result.empty") : [result.title, result.headline].filter(Boolean).join(" · ") || t("review.result.done")}</strong><span>{result.digest_failed ? t("review.result.digestFailed") : result.detail || t("review.result.detail", { writes: result.memory_writes, newCount: result.new_loops, closedCount: result.closed_loops })}</span></div></div>}
      {loading ? <div className="review-loading review-page-state"><LoaderCircle size={18} className="spin" />{t("review.loading")}</div> : <div className="review-reading-flow">
        <ReviewDigest digest={digest} error={sectionErrors.digest} running={running} onRun={() => void runConsolidation()} showAction={false} />
        <ReviewOpenLoops
          loops={loops}
          day={day}
          error={sectionErrors.loops}
          onClose={(loop) => mutateLoops(() => closeOpenLoop(loop.id))}
          onReopen={(loop) => mutateLoops(() => reopenOpenLoop(loop.id))}
          onDrop={(loop) => mutateLoops(() => dropOpenLoop(loop.id))}
          onCreate={(text) => mutateLoops(() => createOpenLoop(text, day))}
        />
        <div className="review-conversation-layer">
          <ReviewSummaryList summaries={summaries} error={sectionErrors.summaries} />
          <ReviewConversationList conversations={conversations} error={sectionErrors.conversations} />
        </div>
        <ReviewMemoryChanges changes={changes} error={sectionErrors.changes} onRestore={requestRestoreDeletedMemory} />
        <ReviewUsageCard usage={usage} selectedDay={day} error={sectionErrors.usage} />
      </div>}
      <details className="review-note">
        <summary><TriangleAlert size={13} />{t("review.details")}</summary>
        <p>{t("review.note")}</p>
      </details>
      <ConfirmDialog
        open={restoreTarget !== null}
        title={t("review.restore.title")}
        description={t("review.restore.description")}
        subject={restoreTarget?.path}
        confirmLabel={t("review.restore.confirm")}
        busy={restoring}
        onCancel={() => setRestoreTarget(null)}
        onConfirm={() => void confirmRestoreDeletedMemory()}
      />
    </main>
  </div>;
}
