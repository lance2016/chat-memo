import { CalendarClock, CornerDownRight, Eye, Play, Repeat2, Sparkles } from "lucide-react";
import type { DailyDigest, Echo } from "@/lib/types";

const ECHO_ICON = {
  recurring: Repeat2,
  followup: CornerDownRight,
  anniversary: CalendarClock,
} as const;

function EchoRow({ echo }: { echo: Echo }) {
  const Icon = ECHO_ICON[echo.kind] ?? Repeat2;
  return <li><Icon size={13} aria-hidden="true" />{echo.text}</li>;
}

export function ReviewDigest({ digest, error, running, onRun }: { digest: DailyDigest | null; error?: string; running: boolean; onRun: () => void }) {
  if (error) return <section className="review-digest review-digest-empty"><div className="card-state card-state-error">{error}</div></section>;

  if (!digest) return <section className="review-digest review-digest-empty">
    <Sparkles size={20} />
    <strong>这一天还没有回顾</strong>
    <span>整理之后，这里会是这一天的名字、发生了什么，以及一句只有看过全天对话才写得出的观察。</span>
    <button className="primary-button" onClick={onRun} disabled={running}><Play size={14} />{running ? "整理中…" : "整理这一天"}</button>
  </section>;

  return <section className="review-digest" aria-label="今日回顾">
    {digest.title && <p className="digest-title">{digest.title}</p>}
    <p className="digest-headline">{digest.headline}</p>

    {digest.highlights.length > 0 && <ul className="digest-highlights">
      {digest.highlights.map((highlight, index) => <li key={index}><span className="digest-bullet" aria-hidden="true" />{highlight}</li>)}
    </ul>}

    {digest.observation && <p className="digest-observation"><Eye size={14} aria-hidden="true" /><span>{digest.observation}</span></p>}

    {digest.quote && <blockquote className="digest-quote">{digest.quote}<cite>你，这天说的</cite></blockquote>}

    {digest.echoes.length > 0 && <ul className="digest-echoes">
      {digest.echoes.map((echo, index) => <EchoRow echo={echo} key={index} />)}
    </ul>}
  </section>;
}
