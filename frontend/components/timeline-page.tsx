"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { CalendarCheck2, CalendarDays, Check, CheckCircle2, CircleDashed, Clock3, MapPin, Plus, RefreshCw, Trash2, X } from "lucide-react";
import { createTimelineItem, deleteTimelineItem, errorMessage, listTimeline, updateTimelineItem } from "@/lib/api";
import type { TimelineItem, TimelineKind, TimelineStatus } from "@/lib/types";
import { ConfirmDialog } from "@/components/confirm-dialog";

type View = "today" | "upcoming" | "month";

const kindLabels: Record<TimelineKind, string> = {
  todo: "待办", event: "日程", reminder: "提醒", birthday: "生日", travel: "旅行", deadline: "截止", note: "记录",
};

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

function dayTitle(key: string) {
  const date = new Date(`${key}T12:00:00`);
  const today = dateKey(new Date());
  if (key === today) return "今天";
  if (key === dateKey(addDays(startOfDay(), 1))) return "明天";
  return new Intl.DateTimeFormat("zh-CN", { month: "long", day: "numeric", weekday: "short" }).format(date);
}

function timeLabel(item: TimelineItem) {
  if (item.all_day) return "全天";
  const start = new Date(item.starts_at);
  const end = item.ends_at ? new Date(item.ends_at) : null;
  const format = new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });
  return end ? `${format.format(start)}–${format.format(end)}` : format.format(start);
}

function TimelineCard({ item, busy, onStatus, onDelete }: {
  item: TimelineItem;
  busy: boolean;
  onStatus: (status: TimelineStatus) => void;
  onDelete: () => void;
}) {
  return <article className={`timeline-item kind-${item.kind} status-${item.status}`}>
    <div className="timeline-item-rail"><span /><i /></div>
    <div className="timeline-item-body">
      <div className="timeline-item-heading">
        <div><span className="timeline-kind">{kindLabels[item.kind]}</span>{item.status === "pending" && <span className="timeline-pending"><CircleDashed size={11} />待确认</span>}</div>
        <strong>{item.title}</strong>
      </div>
      {item.details && <p>{item.details}</p>}
      <div className="timeline-item-meta"><span><Clock3 size={12} />{timeLabel(item)}</span>{item.location && <span><MapPin size={12} />{item.location}</span>}{item.recurrence === "yearly" && <span>每年重复</span>}{item.source_conversation_id && <Link href={`/?conversation=${item.source_conversation_id}`}>来自对话</Link>}</div>
    </div>
    <div className="timeline-item-actions">
      {item.status === "pending" && <button onClick={() => onStatus("confirmed")} disabled={busy} title="确认"><Check size={14} /></button>}
      {item.status !== "completed" && item.status !== "cancelled" && <button onClick={() => onStatus("completed")} disabled={busy} title="完成"><CheckCircle2 size={14} /></button>}
      {item.status === "completed" && <button onClick={() => onStatus("confirmed")} disabled={busy} title="重新打开"><RefreshCw size={13} /></button>}
      <button onClick={onDelete} disabled={busy} title="删除"><Trash2 size={13} /></button>
    </div>
  </article>;
}

export function TimelinePage() {
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
      if (!(cause instanceof DOMException && cause.name === "AbortError")) setError(errorMessage(cause, "无法加载时间线"));
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, [cursor, view]);

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
      setError(errorMessage(cause, "无法创建时间事项"));
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
      setError(errorMessage(cause, "无法更新时间事项"));
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
      setError(errorMessage(cause, "无法删除时间事项"));
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
    <header className="timeline-heading"><div><span className="eyebrow">Personal timeline</span><h1>把未来放在眼前。</h1><p>对话中提到的会议、旅行、生日和待办，会整理成可以确认与完成的时间事项。</p></div><button className="primary-button" onClick={() => setComposerOpen(true)}><Plus size={15} />手动添加</button></header>

    <div className="timeline-toolbar">
      <div className="timeline-tabs" role="tablist">{(["today", "upcoming", "month"] as View[]).map((key) => <button role="tab" aria-selected={view === key} className={view === key ? "active" : ""} onClick={() => setView(key)} key={key}>{key === "today" ? "今天" : key === "upcoming" ? "最近 30 天" : "月视图"}</button>)}</div>
      {view === "month" && <div className="timeline-month-nav"><button onClick={() => setCursor(new Date(cursor.getFullYear(), cursor.getMonth() - 1, 1))}>‹</button><strong>{cursor.getFullYear()} 年 {cursor.getMonth() + 1} 月</strong><button onClick={() => setCursor(new Date(cursor.getFullYear(), cursor.getMonth() + 1, 1))}>›</button></div>}
    </div>

    {composerOpen && <section className="timeline-composer"><div className="timeline-composer-title"><div><span>NEW ITEM</span><h2>添加时间事项</h2></div><button className="icon-button" onClick={() => setComposerOpen(false)} aria-label="关闭"><X size={16} /></button></div><div className="timeline-form-grid"><label><span>标题</span><input autoFocus value={draft.title} onChange={(event) => setDraft({ ...draft, title: event.target.value })} placeholder="例如：和产品团队开会" /></label><label><span>类型</span><select value={draft.kind} onChange={(event) => setDraft({ ...draft, kind: event.target.value as TimelineKind })}>{Object.entries(kindLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label><label><span>时间</span><input type="datetime-local" value={draft.startsAt} onChange={(event) => setDraft({ ...draft, startsAt: event.target.value })} /></label><label><span>地点</span><input value={draft.location} onChange={(event) => setDraft({ ...draft, location: event.target.value })} placeholder="可选" /></label><label className="timeline-details-field"><span>说明</span><textarea value={draft.details} onChange={(event) => setDraft({ ...draft, details: event.target.value })} placeholder="可选的补充信息" /></label><label className="timeline-check"><input type="checkbox" checked={draft.allDay} onChange={(event) => setDraft({ ...draft, allDay: event.target.checked })} />全天事项</label></div><div className="timeline-composer-actions"><button className="ghost-button" onClick={() => setComposerOpen(false)}>取消</button><button className="primary-button" onClick={() => void save()} disabled={!draft.title.trim() || !draft.startsAt || saving}>{saving ? "保存中…" : "保存事项"}</button></div></section>}

    {error && <div className="timeline-error"><span>{error}</span><button onClick={() => void load()}><RefreshCw size={13} />重试</button></div>}
    {loading ? <div className="timeline-empty"><RefreshCw className="spin" size={20} /><strong>正在整理时间线…</strong></div> : view === "month" ? <section className="timeline-calendar"><div className="timeline-weekdays">{"一二三四五六日".split("").map((day) => <span key={day}>周{day}</span>)}</div><div className="timeline-calendar-grid">{monthDays.map((day) => { const key = dateKey(day); const dayItems = items.filter((item) => dateKey(item.starts_at) === key); const currentMonth = day.getMonth() === cursor.getMonth(); return <div className={`timeline-day ${currentMonth ? "" : "outside"} ${key === dateKey(new Date()) ? "today" : ""}`} key={key}><time>{day.getDate()}</time><div>{dayItems.slice(0, 3).map((item) => <span className={`kind-${item.kind}`} title={item.title} key={item.id}>{item.title}</span>)}</div>{dayItems.length > 3 && <small>还有 {dayItems.length - 3} 项</small>}</div>; })}</div></section> : grouped.length ? <section className="timeline-list">{grouped.map(([key, dayItems]) => <div className="timeline-day-group" key={key}><div className="timeline-date"><CalendarDays size={15} /><div><strong>{dayTitle(key)}</strong><span>{key}</span></div></div><div className="timeline-day-items">{dayItems.map((item) => <TimelineCard item={item} busy={busyId === item.id} onStatus={(status) => void changeStatus(item, status)} onDelete={() => setDeleteTarget(item)} key={item.id} />)}</div></div>)}</section> : <div className="timeline-empty"><CalendarCheck2 size={28} /><strong>{view === "today" ? "今天还没有安排" : "最近没有时间事项"}</strong><span>在对话里告诉我你的计划，或手动添加第一条。</span><button className="ghost-button" onClick={() => setComposerOpen(true)}><Plus size={13} />添加事项</button></div>}
  </main><ConfirmDialog open={deleteTarget !== null} title="删除这条时间事项？" description="删除后不会影响原始对话，但这条结构化记录无法恢复。" subject={deleteTarget?.title} confirmLabel="删除事项" busy={busyId === deleteTarget?.id} onCancel={() => setDeleteTarget(null)} onConfirm={() => void remove()} /></div>;
}
