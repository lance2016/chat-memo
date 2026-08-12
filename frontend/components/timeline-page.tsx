"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { AlarmClockOff, BellOff, BellRing, CalendarCheck2, CalendarDays, Check, CheckCircle2, CircleDashed, Clock3, MapPin, Pencil, Plus, RefreshCw, Trash2, TriangleAlert, X } from "lucide-react";
import { createTimelineItem, deleteTimelineItem, errorMessage, listTimeline, updateTimelineItem } from "@/lib/api";
import type { TimelineItem, TimelineKind, TimelineStatus } from "@/lib/types";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { useI18n } from "@/components/i18n-provider";
import type { TranslationKey, TranslationValues } from "@/lib/i18n";
import { useDismissDetailsOnOutside } from "@/lib/use-dismiss-on-outside";

type View = "today" | "upcoming" | "month";

const timelineKindGroups = [
  { label: "timeline.kindGroup.tasks" as const, values: ["todo", "deadline", "reminder"] as TimelineKind[] },
  { label: "timeline.kindGroup.events" as const, values: ["event", "birthday", "travel"] as TimelineKind[] },
  { label: "timeline.kindGroup.records" as const, values: ["note"] as TimelineKind[] },
];

function startOfDay(value = new Date()) {
  return new Date(value.getFullYear(), value.getMonth(), value.getDate());
}

function addDays(value: Date, days: number) {
  const next = new Date(value);
  next.setDate(next.getDate() + days);
  return next;
}

function dateKey(value: Date | string) {
  const date = typeof value === "string" ? new Date(value) : value;
  const year = date.getFullYear();
  return `${year}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

function inputDateTime(value: Date) {
  return `${dateKey(value)}T${String(value.getHours()).padStart(2, "0")}:${String(value.getMinutes()).padStart(2, "0")}`;
}

// 每年重复的事项会被后端展开成多个「occurrence」，它们共用同一个 id。
// 单个查询区间内一个 id 最多出现一次，但列表里要区分，所以 key 带上时刻。
function itemKey(item: TimelineItem) {
  return `${item.id}-${item.starts_at}`;
}

function rangeFor(view: View, cursor: Date) {
  if (view === "today") {
    const start = startOfDay();
    return { from: start.toISOString(), to: addDays(start, 1).toISOString() };
  }
  if (view === "upcoming") {
    const start = startOfDay();
    return { from: start.toISOString(), to: addDays(start, 31).toISOString() };
  }
  const start = new Date(cursor.getFullYear(), cursor.getMonth(), 1);
  const end = new Date(cursor.getFullYear(), cursor.getMonth() + 1, 1);
  return { from: start.toISOString(), to: end.toISOString() };
}

function isOverdue(item: TimelineItem) {
  return new Date(item.starts_at) < startOfDay() && item.status !== "completed" && item.status !== "cancelled";
}

function dayTitle(key: string, locale: string, todayLabel: string, tomorrowLabel: string) {
  const date = new Date(`${key}T12:00:00`);
  const today = dateKey(new Date());
  if (key === today) return todayLabel;
  if (key === dateKey(addDays(startOfDay(), 1))) return tomorrowLabel;
  return new Intl.DateTimeFormat(locale, { month: "long", day: "numeric", weekday: "short" }).format(date);
}

function timeLabel(item: TimelineItem, locale: string, allDayLabel: string) {
  if (item.all_day) return allDayLabel;
  const start = new Date(item.starts_at);
  const end = item.ends_at ? new Date(item.ends_at) : null;
  const format = new Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit", hour12: false });
  return end ? `${format.format(start)}–${format.format(end)}` : format.format(start);
}

/** 提前量说人话。后端存的是绝对时刻，这里换算回「提前多久」更好读。 */
function leadLabel(item: TimelineItem, t: (key: TranslationKey, values?: TranslationValues) => string) {
  if (!item.remind_at) return "";
  const minutes = Math.round((new Date(item.starts_at).getTime() - new Date(item.remind_at).getTime()) / 60000);
  if (minutes <= 0) return t("timeline.remindOnTime");
  if (minutes < 60) return t("timeline.remindBefore", { value: t("timeline.minutes", { count: minutes }) });
  if (minutes < 24 * 60) return t("timeline.remindBefore", { value: t("timeline.hours", { count: Math.round(minutes / 60) }) });
  return t("timeline.remindBefore", { value: t("timeline.days", { count: Math.round(minutes / (24 * 60)) }) });
}

function TimelineCard({ item, busy, overdue, onStatus, onToggleNotify, onEdit, onDelete }: {
  item: TimelineItem;
  busy: boolean;
  overdue: boolean;
  onStatus: (status: TimelineStatus) => void;
  onToggleNotify: () => void;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const { locale, t } = useI18n();
  const done = item.status === "completed" || item.status === "cancelled";
  return <article className={`timeline-item kind-${item.kind} status-${item.status} ${overdue ? "is-overdue" : ""}`}>
    <div className="timeline-item-rail"><span /><i /></div>
    <div className="timeline-item-body">
      <div className="timeline-item-heading">
        <div><span className="timeline-kind">{t(`timeline.kind.${item.kind}`)}</span>{item.status === "pending" && <span className="timeline-pending"><CircleDashed size={11} />{t("timeline.pending")}</span>}{overdue && <span className="timeline-overdue-tag"><TriangleAlert size={11} />{t("timeline.overdue")}</span>}</div>
        <strong>{item.title}</strong>
      </div>
      {item.details && <p>{item.details}</p>}
      <div className="timeline-item-meta"><span><Clock3 size={12} />{timeLabel(item, locale, t("timeline.allDay"))}{item.said && <em className="timeline-said" title={t("timeline.saidHint")}>{t("timeline.said", { said: item.said })}</em>}</span>{item.location && <span><MapPin size={12} />{item.location}</span>}{item.recurrence === "yearly" && <span>{t("timeline.yearly")}</span>}{!done && (item.remind_at ? <span className="timeline-remind"><BellRing size={12} />{leadLabel(item, t)}</span> : <span className="timeline-remind muted"><BellOff size={12} />{t("timeline.noRemind")}</span>)}{item.source_conversation_id && <Link href={`/?conversation=${item.source_conversation_id}`}>{t("timeline.fromConversation")}</Link>}</div>
    </div>
    <div className="timeline-item-actions">
      {item.status === "pending" && <button type="button" onClick={() => onStatus("confirmed")} disabled={busy} title={t("timeline.confirm")} aria-label={t("timeline.confirm")}><Check size={14} /></button>}
      {!done && <button type="button" onClick={() => onStatus("completed")} disabled={busy} title={t("timeline.complete")} aria-label={t("timeline.complete")}><CheckCircle2 size={14} /></button>}
      {!done && <button type="button" onClick={onToggleNotify} disabled={busy} title={item.remind_at ? t("timeline.muteRemind") : t("timeline.unmuteRemind")} aria-label={item.remind_at ? t("timeline.muteRemind") : t("timeline.unmuteRemind")}>{item.remind_at ? <AlarmClockOff size={13} /> : <BellRing size={13} />}</button>}
      <button type="button" onClick={onEdit} disabled={busy} title={t("timeline.edit")} aria-label={t("timeline.edit")}><Pencil size={13} /></button>
      {item.status === "completed" && <button type="button" onClick={() => onStatus("confirmed")} disabled={busy} title={t("timeline.reopen")} aria-label={t("timeline.reopen")}><RefreshCw size={13} /></button>}
      <button type="button" onClick={onDelete} disabled={busy} title={t("timeline.delete")} aria-label={t("timeline.delete")}><Trash2 size={13} /></button>
    </div>
  </article>;
}

export function TimelinePage() {
  const { locale, t } = useI18n();
  const [view, setView] = useState<View>("today");
  const [cursor, setCursor] = useState(new Date());
  const [items, setItems] = useState<TimelineItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [composerOpen, setComposerOpen] = useState(false);
  const [editing, setEditing] = useState<TimelineItem | null>(null);
  const [saving, setSaving] = useState(false);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<TimelineItem | null>(null);
  const completedRef = useRef<HTMLDetailsElement>(null);
  useDismissDetailsOnOutside(completedRef);
  const nextHour = useMemo(() => { const value = new Date(); value.setHours(value.getHours() + 1, 0, 0, 0); return value; }, []);
  const emptyDraft = useMemo(() => ({ title: "", details: "", kind: "todo" as TimelineKind, startsAt: inputDateTime(nextHour), allDay: false, location: "", leadMinutes: "" }), [nextHour]);
  const [draft, setDraft] = useState(emptyDraft);

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setError("");
    try {
      setItems(await listTimeline({
        ...rangeFor(view, cursor),
        statuses: ["pending", "confirmed", "completed"],
        // 月历是日历，逾期项不该跨月挤进来；今天和最近必须带上，
        // 否则昨天没勾掉的事会静默沉底。
        includeOverdue: view !== "month",
      }, signal));
    } catch (cause) {
      if (!(cause instanceof DOMException && cause.name === "AbortError")) setError(errorMessage(cause, t("timeline.loadError")));
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, [cursor, t, view]);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  const [overdueItems, grouped, completedGroups] = useMemo(() => {
    const overdue: TimelineItem[] = [];
    const active = new Map<string, TimelineItem[]>();
    const completed = new Map<string, TimelineItem[]>();
    for (const item of items) {
      if (isOverdue(item)) { overdue.push(item); continue; }
      const key = dateKey(item.starts_at);
      const target = item.status === "completed" ? completed : active;
      target.set(key, [...(target.get(key) ?? []), item]);
    }
    const statusOrder: Record<TimelineStatus, number> = { pending: 0, confirmed: 1, completed: 2, cancelled: 3 };
    for (const dayItems of active.values()) dayItems.sort((left, right) => statusOrder[left.status] - statusOrder[right.status]);
    return [overdue, [...active.entries()], [...completed.entries()]];
  }, [items]);

  const openComposer = (item: TimelineItem | null) => {
    setEditing(item);
    setDraft(item ? {
      title: item.title, details: item.details, kind: item.kind,
      startsAt: inputDateTime(new Date(item.starts_at)), allDay: item.all_day,
      location: item.location, leadMinutes: item.lead_minutes === null ? "" : String(item.lead_minutes),
    } : emptyDraft);
    setComposerOpen(true);
  };

  const closeComposer = () => { setComposerOpen(false); setEditing(null); };

  const save = async () => {
    if (!draft.title.trim() || !draft.startsAt || saving) return;
    setSaving(true);
    try {
      const payload = {
        title: draft.title.trim(), details: draft.details.trim(), kind: draft.kind,
        starts_at: new Date(draft.startsAt).toISOString(), all_day: draft.allDay,
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Shanghai", location: draft.location.trim(),
        lead_minutes: draft.leadMinutes.trim() === "" ? null : Number(draft.leadMinutes),
      };
      if (editing) {
        // 改期走 PATCH 而不是删了重建 —— 重建会丢掉来源会话和 said 那条依据。
        const updated = await updateTimelineItem(editing.id, payload);
        setItems((current) => current.map((entry) => entry.id === editing.id ? updated : entry));
      } else {
        await createTimelineItem(payload);
        await load();
      }
      setDraft(emptyDraft);
      closeComposer();
    } catch (cause) {
      setError(errorMessage(cause, editing ? t("timeline.updateError") : t("timeline.createError")));
    } finally {
      setSaving(false);
    }
  };

  const applyChange = async (item: TimelineItem, run: () => Promise<TimelineItem>, fallback: string) => {
    setBusyId(item.id);
    try {
      const updated = await run();
      setItems((current) => current.map((entry) => entry.id === item.id ? updated : entry));
    } catch (cause) {
      setError(errorMessage(cause, fallback));
    } finally {
      setBusyId(null);
    }
  };

  const remove = async () => {
    if (!deleteTarget) return;
    setBusyId(deleteTarget.id);
    try {
      await deleteTimelineItem(deleteTarget.id);
      setItems((current) => current.filter((item) => item.id !== deleteTarget.id));
      setDeleteTarget(null);
    } catch (cause) {
      setError(errorMessage(cause, t("timeline.deleteError")));
    } finally {
      setBusyId(null);
    }
  };

  const monthDays = useMemo(() => {
    const first = new Date(cursor.getFullYear(), cursor.getMonth(), 1);
    const start = addDays(first, -(first.getDay() || 7) + 1);
    return Array.from({ length: 42 }, (_, index) => addDays(start, index));
  }, [cursor]);

  const cardProps = (item: TimelineItem, overdue = false) => ({
    item, overdue, busy: busyId === item.id,
    onStatus: (status: TimelineStatus) => void applyChange(item, () => updateTimelineItem(item.id, { status }), t("timeline.updateError")),
    onToggleNotify: () => void applyChange(item, () => updateTimelineItem(item.id, { notify: !item.remind_at }), t("timeline.updateError")),
    onEdit: () => openComposer(item),
    onDelete: () => setDeleteTarget(item),
  });

  return <div className="timeline-shell"><main className="timeline-content">
    <header className="timeline-page-header">
      <div><h1>{t("nav.timeline")}</h1><p>{t("timeline.description")}</p></div>
      <button type="button" className="primary-button timeline-add-button" onClick={() => openComposer(null)}><Plus size={16} />{t("timeline.add")}</button>
    </header>
    <div className="timeline-toolbar">
      <div className="timeline-tabs" role="tablist">{(["today", "upcoming", "month"] as View[]).map((key) => <button type="button" role="tab" aria-selected={view === key} className={view === key ? "active" : ""} onClick={() => setView(key)} key={key}>{t(`timeline.view.${key}`)}</button>)}</div>
      <div className="timeline-toolbar-actions">
        {view === "month" && <div className="timeline-month-nav"><button type="button" aria-label={t("timeline.previousMonth")} onClick={() => setCursor(new Date(cursor.getFullYear(), cursor.getMonth() - 1, 1))}>‹</button><strong>{new Intl.DateTimeFormat(locale, { year: "numeric", month: "long" }).format(cursor)}</strong><button type="button" aria-label={t("timeline.nextMonth")} onClick={() => setCursor(new Date(cursor.getFullYear(), cursor.getMonth() + 1, 1))}>›</button></div>}
      </div>
    </div>

    {composerOpen && <section className="timeline-composer"><div className="timeline-composer-title"><div><span>{editing ? t("timeline.editItem") : t("timeline.newItem")}</span><h2>{editing ? t("timeline.editTitle") : t("timeline.addTitle")}</h2></div><button type="button" className="icon-button" onClick={closeComposer} aria-label={t("common.close")}><X size={16} /></button></div><div className="timeline-form-grid"><label><span>{t("timeline.field.title")}</span><input autoFocus value={draft.title} onChange={(event) => setDraft({ ...draft, title: event.target.value })} placeholder={t("timeline.field.titlePlaceholder")} /></label><label><span>{t("timeline.field.kind")}</span><select value={draft.kind} onChange={(event) => setDraft({ ...draft, kind: event.target.value as TimelineKind })}>{timelineKindGroups.map((group) => <optgroup label={t(group.label)} key={group.label}>{group.values.map((value) => <option value={value} key={value}>{t(`timeline.kind.${value}`)}</option>)}</optgroup>)}</select></label><label><span>{t("timeline.field.time")}</span><input type="datetime-local" value={draft.startsAt} onChange={(event) => setDraft({ ...draft, startsAt: event.target.value })} /></label><label><span>{t("timeline.field.lead")}</span><input type="number" min={0} value={draft.leadMinutes} onChange={(event) => setDraft({ ...draft, leadMinutes: event.target.value })} placeholder={t("timeline.field.leadPlaceholder")} /></label><label><span>{t("timeline.field.location")}</span><input value={draft.location} onChange={(event) => setDraft({ ...draft, location: event.target.value })} placeholder={t("timeline.optional")} /></label><label className="timeline-details-field"><span>{t("timeline.field.details")}</span><textarea value={draft.details} onChange={(event) => setDraft({ ...draft, details: event.target.value })} placeholder={t("timeline.detailsPlaceholder")} /></label><label className="timeline-check"><input type="checkbox" checked={draft.allDay} onChange={(event) => setDraft({ ...draft, allDay: event.target.checked })} />{t("timeline.allDayItem")}</label></div><div className="timeline-composer-actions"><button type="button" className="ghost-button" onClick={closeComposer}>{t("common.cancel")}</button><button type="button" className="primary-button" onClick={() => void save()} disabled={!draft.title.trim() || !draft.startsAt || saving}>{saving ? t("timeline.saving") : editing ? t("timeline.saveEdit") : t("timeline.save")}</button></div></section>}

    {error && items.length > 0 && <div className="timeline-error" role="alert"><span>{error}</span><button type="button" onClick={() => void load()}><RefreshCw size={13} />{t("timeline.retry")}</button></div>}
    {loading ? <div className="timeline-empty"><RefreshCw className="spin" size={20} /><strong>{t("timeline.loading")}</strong></div> : error && items.length === 0 ? <div className="timeline-empty timeline-error-state" role="alert"><TriangleAlert size={24} /><strong>{t("timeline.loadError")}</strong><span>{error}</span><button type="button" className="ghost-button" onClick={() => void load()}><RefreshCw size={13} />{t("timeline.retry")}</button></div> : view === "month" ? <section className="timeline-calendar"><div className="timeline-weekdays">{Array.from({ length: 7 }, (_, index) => new Intl.DateTimeFormat(locale, { weekday: "short" }).format(addDays(new Date(2024, 0, 1), index))).map((day) => <span key={day}>{day}</span>)}</div><div className="timeline-calendar-grid">{monthDays.map((day) => { const key = dateKey(day); const dayItems = items.filter((item) => dateKey(item.starts_at) === key); const currentMonth = day.getMonth() === cursor.getMonth(); return <div className={`timeline-day ${currentMonth ? "" : "outside"} ${key === dateKey(new Date()) ? "today" : ""}`} key={key}><time>{day.getDate()}</time><div>{dayItems.slice(0, 3).map((item) => <span className={`kind-${item.kind}`} title={item.title} key={itemKey(item)}>{item.title}</span>)}</div>{dayItems.length > 3 && <small>{t("timeline.more", { count: dayItems.length - 3 })}</small>}</div>; })}</div></section> : <>
      {overdueItems.length > 0 && <section className="timeline-list timeline-overdue"><div className="timeline-day-group"><div className="timeline-date"><TriangleAlert size={15} /><div><strong>{t("timeline.overdueTitle")}</strong><span>{t("timeline.overdueHint", { count: overdueItems.length })}</span></div></div><div className="timeline-day-items">{overdueItems.map((item) => <TimelineCard {...cardProps(item, true)} key={itemKey(item)} />)}</div></div></section>}
      {grouped.length ? <section className="timeline-list">{grouped.map(([key, dayItems]) => <div className="timeline-day-group" key={key}><div className="timeline-date"><CalendarDays size={15} /><div><strong>{dayTitle(key, locale, t("timeline.today"), t("timeline.tomorrow"))}</strong><span>{key}</span></div></div><div className="timeline-day-items">{dayItems.map((item) => <TimelineCard {...cardProps(item)} key={itemKey(item)} />)}</div></div>)}</section> : overdueItems.length === 0 && <div className="timeline-empty"><CalendarCheck2 size={25} /><strong>{view === "today" ? t("timeline.emptyToday") : t("timeline.emptyUpcoming")}</strong><span>{t("timeline.emptyDescription")}</span></div>}
      {completedGroups.length > 0 && <details ref={completedRef} className="timeline-completed"><summary><CheckCircle2 size={14} /><span>{t("timeline.completed.title")}</span><b>{completedGroups.reduce((count, [, dayItems]) => count + dayItems.length, 0)}</b></summary><section className="timeline-list">{completedGroups.map(([key, dayItems]) => <div className="timeline-day-group" key={key}><div className="timeline-date"><CalendarDays size={15} /><div><strong>{dayTitle(key, locale, t("timeline.today"), t("timeline.tomorrow"))}</strong><span>{key}</span></div></div><div className="timeline-day-items">{dayItems.map((item) => <TimelineCard {...cardProps(item)} key={itemKey(item)} />)}</div></div>)}</section></details>}
    </>}
  </main><ConfirmDialog open={deleteTarget !== null} title={t("timeline.deleteTitle")} description={t("timeline.deleteDescription")} subject={deleteTarget?.title} confirmLabel={t("timeline.deleteConfirm")} busy={busyId === deleteTarget?.id} onCancel={() => setDeleteTarget(null)} onConfirm={() => void remove()} /></div>;
}
