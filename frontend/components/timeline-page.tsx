"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { CalendarCheck2, CalendarDays, Check, CheckCircle2, CircleDashed, Clock3, MapPin, Plus, RefreshCw, Trash2, X } from "lucide-react";
import { createTimelineItem, deleteTimelineItem, errorMessage, listTimeline, updateTimelineItem } from "@/lib/api";
import type { TimelineItem, TimelineKind, TimelineStatus } from "@/lib/types";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { useI18n } from "@/components/i18n-provider";

type View = "today" | "upcoming" | "month";

const timelineKinds: TimelineKind[] = ["todo", "event", "reminder", "birthday", "travel", "deadline", "note"];

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

function TimelineCard({ item, busy, onStatus, onDelete }: {
  item: TimelineItem;
  busy: boolean;
  onStatus: (status: TimelineStatus) => void;
  onDelete: () => void;
}) {
  const { locale, t } = useI18n();
  return <article className={`timeline-item kind-${item.kind} status-${item.status}`}>
    <div className="timeline-item-rail"><span /><i /></div>
    <div className="timeline-item-body">
      <div className="timeline-item-heading">
        <div><span className="timeline-kind">{t(`timeline.kind.${item.kind}`)}</span>{item.status === "pending" && <span className="timeline-pending"><CircleDashed size={11} />{t("timeline.pending")}</span>}</div>
        <strong>{item.title}</strong>
      </div>
      {item.details && <p>{item.details}</p>}
      <div className="timeline-item-meta"><span><Clock3 size={12} />{timeLabel(item, locale, t("timeline.allDay"))}{item.said && <em className="timeline-said" title={t("timeline.saidHint")}>{t("timeline.said", { said: item.said })}</em>}</span>{item.location && <span><MapPin size={12} />{item.location}</span>}{item.recurrence === "yearly" && <span>{t("timeline.yearly")}</span>}{item.source_conversation_id && <Link href={`/?conversation=${item.source_conversation_id}`}>{t("timeline.fromConversation")}</Link>}</div>
    </div>
    <div className="timeline-item-actions">
      {item.status === "pending" && <button onClick={() => onStatus("confirmed")} disabled={busy} title={t("timeline.confirm")}><Check size={14} /></button>}
      {item.status !== "completed" && item.status !== "cancelled" && <button onClick={() => onStatus("completed")} disabled={busy} title={t("timeline.complete")}><CheckCircle2 size={14} /></button>}
      {item.status === "completed" && <button onClick={() => onStatus("confirmed")} disabled={busy} title={t("timeline.reopen")}><RefreshCw size={13} /></button>}
      <button onClick={onDelete} disabled={busy} title={t("timeline.delete")}><Trash2 size={13} /></button>
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
  const [saving, setSaving] = useState(false);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<TimelineItem | null>(null);
  const nextHour = useMemo(() => { const value = new Date(); value.setHours(value.getHours() + 1, 0, 0, 0); return value; }, []);
  const [draft, setDraft] = useState({ title: "", details: "", kind: "todo" as TimelineKind, startsAt: inputDateTime(nextHour), allDay: false, location: "" });

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setError("");
    try {
      setItems(await listTimeline({ ...rangeFor(view, cursor), statuses: ["pending", "confirmed", "completed"] }, signal));
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

  const grouped = useMemo(() => {
    const groups = new Map<string, TimelineItem[]>();
    for (const item of items) {
      const key = dateKey(item.starts_at);
      groups.set(key, [...(groups.get(key) ?? []), item]);
    }
    return [...groups.entries()];
  }, [items]);

  const save = async () => {
    if (!draft.title.trim() || !draft.startsAt || saving) return;
    setSaving(true);
    try {
      await createTimelineItem({
        title: draft.title.trim(), details: draft.details.trim(), kind: draft.kind,
        starts_at: new Date(draft.startsAt).toISOString(), all_day: draft.allDay,
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Shanghai", location: draft.location.trim(),
      });
      setDraft((current) => ({ ...current, title: "", details: "", location: "" }));
      setComposerOpen(false);
      await load();
    } catch (cause) {
      setError(errorMessage(cause, t("timeline.createError")));
    } finally {
      setSaving(false);
    }
  };

  const changeStatus = async (item: TimelineItem, status: TimelineStatus) => {
    setBusyId(item.id);
    try {
      const updated = await updateTimelineItem(item.id, { status });
      setItems((current) => current.map((entry) => entry.id === item.id ? updated : entry));
    } catch (cause) {
      setError(errorMessage(cause, t("timeline.updateError")));
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

  return <div className="timeline-shell"><main className="timeline-content">
    <header className="timeline-heading"><div><span className="eyebrow">{t("timeline.eyebrow")}</span><h1>{t("timeline.title")}</h1><p>{t("timeline.description")}</p></div><button className="primary-button" onClick={() => setComposerOpen(true)}><Plus size={15} />{t("timeline.add")}</button></header>

    <div className="timeline-toolbar">
      <div className="timeline-tabs" role="tablist">{(["today", "upcoming", "month"] as View[]).map((key) => <button role="tab" aria-selected={view === key} className={view === key ? "active" : ""} onClick={() => setView(key)} key={key}>{t(`timeline.view.${key}`)}</button>)}</div>
      {view === "month" && <div className="timeline-month-nav"><button onClick={() => setCursor(new Date(cursor.getFullYear(), cursor.getMonth() - 1, 1))}>‹</button><strong>{new Intl.DateTimeFormat(locale, { year: "numeric", month: "long" }).format(cursor)}</strong><button onClick={() => setCursor(new Date(cursor.getFullYear(), cursor.getMonth() + 1, 1))}>›</button></div>}
    </div>

    {composerOpen && <section className="timeline-composer"><div className="timeline-composer-title"><div><span>{t("timeline.newItem")}</span><h2>{t("timeline.addTitle")}</h2></div><button className="icon-button" onClick={() => setComposerOpen(false)} aria-label={t("common.close")}><X size={16} /></button></div><div className="timeline-form-grid"><label><span>{t("timeline.field.title")}</span><input autoFocus value={draft.title} onChange={(event) => setDraft({ ...draft, title: event.target.value })} placeholder={t("timeline.field.titlePlaceholder")} /></label><label><span>{t("timeline.field.kind")}</span><select value={draft.kind} onChange={(event) => setDraft({ ...draft, kind: event.target.value as TimelineKind })}>{timelineKinds.map((value) => <option value={value} key={value}>{t(`timeline.kind.${value}`)}</option>)}</select></label><label><span>{t("timeline.field.time")}</span><input type="datetime-local" value={draft.startsAt} onChange={(event) => setDraft({ ...draft, startsAt: event.target.value })} /></label><label><span>{t("timeline.field.location")}</span><input value={draft.location} onChange={(event) => setDraft({ ...draft, location: event.target.value })} placeholder={t("timeline.optional")} /></label><label className="timeline-details-field"><span>{t("timeline.field.details")}</span><textarea value={draft.details} onChange={(event) => setDraft({ ...draft, details: event.target.value })} placeholder={t("timeline.detailsPlaceholder")} /></label><label className="timeline-check"><input type="checkbox" checked={draft.allDay} onChange={(event) => setDraft({ ...draft, allDay: event.target.checked })} />{t("timeline.allDayItem")}</label></div><div className="timeline-composer-actions"><button className="ghost-button" onClick={() => setComposerOpen(false)}>{t("common.cancel")}</button><button className="primary-button" onClick={() => void save()} disabled={!draft.title.trim() || !draft.startsAt || saving}>{saving ? t("timeline.saving") : t("timeline.save")}</button></div></section>}

    {error && <div className="timeline-error"><span>{error}</span><button onClick={() => void load()}><RefreshCw size={13} />{t("timeline.retry")}</button></div>}
    {loading ? <div className="timeline-empty"><RefreshCw className="spin" size={20} /><strong>{t("timeline.loading")}</strong></div> : view === "month" ? <section className="timeline-calendar"><div className="timeline-weekdays">{Array.from({ length: 7 }, (_, index) => new Intl.DateTimeFormat(locale, { weekday: "short" }).format(addDays(new Date(2024, 0, 1), index))).map((day) => <span key={day}>{day}</span>)}</div><div className="timeline-calendar-grid">{monthDays.map((day) => { const key = dateKey(day); const dayItems = items.filter((item) => dateKey(item.starts_at) === key); const currentMonth = day.getMonth() === cursor.getMonth(); return <div className={`timeline-day ${currentMonth ? "" : "outside"} ${key === dateKey(new Date()) ? "today" : ""}`} key={key}><time>{day.getDate()}</time><div>{dayItems.slice(0, 3).map((item) => <span className={`kind-${item.kind}`} title={item.title} key={item.id}>{item.title}</span>)}</div>{dayItems.length > 3 && <small>{t("timeline.more", { count: dayItems.length - 3 })}</small>}</div>; })}</div></section> : grouped.length ? <section className="timeline-list">{grouped.map(([key, dayItems]) => <div className="timeline-day-group" key={key}><div className="timeline-date"><CalendarDays size={15} /><div><strong>{dayTitle(key, locale, t("timeline.today"), t("timeline.tomorrow"))}</strong><span>{key}</span></div></div><div className="timeline-day-items">{dayItems.map((item) => <TimelineCard item={item} busy={busyId === item.id} onStatus={(status) => void changeStatus(item, status)} onDelete={() => setDeleteTarget(item)} key={item.id} />)}</div></div>)}</section> : <div className="timeline-empty"><CalendarCheck2 size={28} /><strong>{view === "today" ? t("timeline.emptyToday") : t("timeline.emptyUpcoming")}</strong><span>{t("timeline.emptyDescription")}</span><button className="ghost-button" onClick={() => setComposerOpen(true)}><Plus size={13} />{t("timeline.addItem")}</button></div>}
  </main><ConfirmDialog open={deleteTarget !== null} title={t("timeline.deleteTitle")} description={t("timeline.deleteDescription")} subject={deleteTarget?.title} confirmLabel={t("timeline.deleteConfirm")} busy={busyId === deleteTarget?.id} onCancel={() => setDeleteTarget(null)} onConfirm={() => void remove()} /></div>;
}
