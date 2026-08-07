"use client";

import { useState } from "react";
import { Check, CircleDashed, Plus, Undo2, X } from "lucide-react";
import type { OpenLoop } from "@/lib/types";

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
  const [draft, setDraft] = useState("");
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
    });
  };

  const open = loops.filter((loop) => loop.status === "open");
  const fresh = open.filter((loop) => loop.opened_on === day);
  const lingering = open.filter((loop) => loop.opened_on < day);
  const settled = loops.filter((loop) => loop.status !== "open" && loop.closed_on === day);

  const row = (loop: OpenLoop, age?: number) => <li key={loop.id} className={age !== undefined && age >= 7 ? "loop-row loop-stale" : "loop-row"}>
    <button className="loop-check" onClick={() => void run(loop.id, () => actions.onClose(loop))} disabled={busy !== null} aria-label="标记为已完成"><Check size={13} /></button>
    <span className="loop-text">{loop.text}</span>
    {age !== undefined && <span className="loop-age">挂了 {age} 天</span>}
    <button className="loop-drop" onClick={() => void run(loop.id, () => actions.onDrop(loop))} disabled={busy !== null} aria-label="不做了"><X size={13} /></button>
  </li>;

  return <section className="review-card review-open-loops">
    <div className="card-heading">
      <div>
        <span className="card-kicker">OPEN LOOPS</span>
        <h2>悬而未决</h2>
        <p className="card-description">说了要做但没做完的事。整理时会自动判断哪些已经闭环，也可以自己勾掉。</p>
      </div>
      <span className="count-pill">{error ? "—" : open.length}</span>
    </div>

    {error ? <div className="card-state card-state-error">{error}</div> : <>
      {settled.length > 0 && <div className="loop-group loop-group-settled">
        <h3>今天闭环</h3>
        <ul>{settled.map((loop) => <li key={loop.id} className="loop-row loop-done">
          <span className="loop-check loop-check-done" aria-hidden="true"><Check size={13} /></span>
          <span className="loop-text"><s>{loop.text}</s>{loop.closed_note && <em>{loop.closed_note}</em>}</span>
          <button className="loop-undo" onClick={() => void run(loop.id, () => actions.onReopen(loop))} disabled={busy !== null} aria-label="撤销闭环"><Undo2 size={13} /></button>
        </li>)}</ul>
      </div>}

      {fresh.length > 0 && <div className="loop-group">
        <h3>今天新增</h3>
        <ul>{fresh.map((loop) => row(loop))}</ul>
      </div>}

      {lingering.length > 0 && <div className="loop-group">
        <h3>还挂着</h3>
        <ul>{lingering.map((loop) => row(loop, daysBetween(loop.opened_on, day)))}</ul>
      </div>}

      {open.length === 0 && settled.length === 0 && <div className="card-empty compact-empty">
        <CircleDashed size={16} />
        <strong>没有悬着的事</strong>
        <span>整理这一天之后，没做完的事会出现在这里。</span>
      </div>}

      <form className="loop-compose" onSubmit={(event) => { event.preventDefault(); void submitDraft(); }}>
        <Plus size={14} />
        <input value={draft} onChange={(event) => setDraft(event.target.value)} placeholder="自己加一条…" disabled={busy !== null} />
        <button className="ghost-button" type="submit" disabled={busy !== null || !draft.trim()}>加上</button>
      </form>
    </>}
  </section>;
}
