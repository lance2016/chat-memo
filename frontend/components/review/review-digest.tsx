import { Play, Sparkles } from "lucide-react";
import type { DailyDigest } from "@/lib/types";

export function ReviewDigest({ digest, error, running, onRun }: { digest: DailyDigest | null; error?: string; running: boolean; onRun: () => void }) {
  if (error) return <section className="review-digest review-digest-empty"><div className="card-state card-state-error">{error}</div></section>;

  if (!digest) return <section className="review-digest review-digest-empty">
    <Sparkles size={20} />
    <strong>这一天还没有回顾</strong>
    <span>整理之后，这里会是一句话概括，加上几条真正推进了的事。</span>
    <button className="primary-button" onClick={onRun} disabled={running}><Play size={14} />{running ? "整理中…" : "整理这一天"}</button>
  </section>;

  return <section className="review-digest" aria-label="今日回顾">
    <p className="digest-headline">{digest.headline}</p>
    {digest.highlights.length > 0 && <ul className="digest-highlights">
      {digest.highlights.map((highlight, index) => <li key={index}><span className="digest-bullet" aria-hidden="true" />{highlight}</li>)}
    </ul>}
  </section>;
}
