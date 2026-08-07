"use client";

import { useState } from "react";
import { Check, CircleDashed, Plus, Undo2, X } from "lucide-react";
import type { OpenLoop } from "@/lib/types";
import { useI18n } from "@/components/i18n-provider";

function daysBetween(from: string, to: string) {
  const start = new Date(`${from}T12:00:00`).getTime();
  const end = new Date(`${to}T12:00:00`).getTime();
  return Math.round((end - start) / 86_400_000);
}

type Actions = {
  onClose: (loop: OpenLoop) => Promise<void>;
  onReopen: (loop: OpenLoop) => Promise<void>;
  onDrop: (loop: OpenLoop) => Promise<void>;
  onCreate: (text: string) => Promise<void>;
};

export function ReviewOpenLoops({ loops, day, error, ...actions }: { loops: OpenLoop[]; day: string; error?: string } & Actions) {
  const { t } = useI18n();
  const [draft, setDraft] = useState("");
  const [composing, setComposing] = useState(false);
  const [busy, setBusy] = useState<number | "new" | null>(null);

  const run = async (key: number | "new", task: () => Promise<void>) => {
    if (busy !== null) return;
    setBusy(key);
    try {
      await task();
    } finally {
      setBusy(null);
    }
  };

  const submitDraft = async () => {
    const text = draft.trim();
    if (!text) return;
    await run("new", async () => {
      await actions.onCreate(text);
      setDraft("");
      setComposing(false);
    });
  };

  const open = loops.filter((loop) => loop.status === "open");
  const fresh = open.filter((loop) => loop.opened_on === day);
  const lingering = open.filter((loop) => loop.opened_on < day);
  const settled = loops.filter((loop) => loop.status !== "open" && loop.closed_on === day);
  const empty = open.length === 0 && settled.length === 0;

  const row = (loop: OpenLoop, age?: number) => <li key={loop.id} className={age !== undefined && age >= 7 ? "loop-row loop-stale" : "loop-row"}>
    <button className="loop-check" onClick={() => void run(loop.id, () => actions.onClose(loop))} disabled={busy !== null} aria-label={t("review.followUps.markHandled")}><Check size={13} /></button>
    <span className="loop-text">{loop.text}</span>
    {age !== undefined && <span className="loop-age">{t("review.followUps.watchedDays", { days: age })}</span>}
    <button className="loop-drop" onClick={() => void run(loop.id, () => actions.onDrop(loop))} disabled={busy !== null} aria-label={t("review.followUps.stopWatching")}><X size={13} /></button>
  </li>;

  const composer = <form className="loop-compose" onSubmit={(event) => { event.preventDefault(); void submitDraft(); }}>
    <Plus size={14} />
    <input value={draft} onChange={(event) => setDraft(event.target.value)} placeholder={t("review.followUps.placeholder")} disabled={busy !== null} autoFocus={empty && composing} />
    <button className="ghost-button" type="submit" disabled={busy !== null || !draft.trim()}>{t("review.followUps.add")}</button>
  </form>;

  return <section className={`review-card review-open-loops ${empty ? "is-empty" : ""}`}>
    <div className="card-heading">
      <div>
        <span className="card-kicker">{t("review.followUps.kicker")}</span>
        <h2>{t("review.followUps.title")}</h2>
        <p className="card-description">{t("review.followUps.description")}</p>
      </div>
      <span className="count-pill">{error ? "—" : open.length}</span>
    </div>

    {error ? <div className="card-state card-state-error">{error}</div> : <>
      {settled.length > 0 && <div className="loop-group loop-group-settled">
        <h3>{t("review.followUps.settledToday")}</h3>
        <ul>{settled.map((loop) => <li key={loop.id} className="loop-row loop-done">
          <span className="loop-check loop-check-done" aria-hidden="true"><Check size={13} /></span>
          <span className="loop-text"><s>{loop.text}</s>{loop.closed_note && <em>{loop.closed_note}</em>}</span>
          <button className="loop-undo" onClick={() => void run(loop.id, () => actions.onReopen(loop))} disabled={busy !== null} aria-label={t("review.followUps.restore")}><Undo2 size={13} /></button>
        </li>)}</ul>
      </div>}

      {fresh.length > 0 && <div className="loop-group">
        <h3>{t("review.followUps.foundToday")}</h3>
        <ul>{fresh.map((loop) => row(loop))}</ul>
      </div>}

      {lingering.length > 0 && <div className="loop-group">
        <h3>{t("review.followUps.stillWatching")}</h3>
        <ul>{lingering.map((loop) => row(loop, daysBetween(loop.opened_on, day)))}</ul>
      </div>}

      {empty && <div className="loop-empty-inline">
        <span className="loop-empty-icon"><CircleDashed size={16} /></span>
        <span className="loop-empty-copy"><strong>{t("review.followUps.emptyTitle")}</strong><small>{t("review.followUps.emptyDescription")}</small></span>
        <button className="ghost-button loop-compose-toggle" type="button" onClick={() => setComposing((value) => !value)}>{composing ? <X size={12} /> : <Plus size={12} />}{composing ? t("review.followUps.collapse") : t("review.followUps.addOne")}</button>
      </div>}

      {(!empty || composing) && composer}
    </>}
  </section>;
}
