import { CalendarClock, CornerDownRight, Eye, Play, Repeat2, Sparkles } from "lucide-react";
import type { DailyDigest, Echo } from "@/lib/types";
import { useI18n } from "@/components/i18n-provider";

const ECHO_ICON = {
  recurring: Repeat2,
  followup: CornerDownRight,
  anniversary: CalendarClock,
} as const;

function EchoRow({ echo }: { echo: Echo }) {
  const Icon = ECHO_ICON[echo.kind] ?? Repeat2;
  return <li><Icon size={13} aria-hidden="true" />{echo.text}</li>;
}

type ReviewDigestProps = {
  digest: DailyDigest | null;
  error?: string;
  running: boolean;
  onRun: () => void;
  showAction?: boolean;
};

export function ReviewDigest({ digest, error, running, onRun, showAction = true }: ReviewDigestProps) {
  const { t } = useI18n();
  if (error) return <section className="review-digest review-digest-empty"><div className="card-state card-state-error">{error}</div></section>;

  if (!digest) return <section className="review-digest review-digest-empty">
    <Sparkles size={20} />
    <strong>{t("review.digest.emptyTitle")}</strong>
    <span>{t("review.digest.emptyDescription")}</span>
    {showAction && <button className="primary-button" onClick={onRun} disabled={running}><Play size={14} />{running ? t("review.running") : t("review.run")}</button>}
  </section>;

  return <section className="review-digest" aria-label={t("review.digest.label")}>
    {digest.title && <p className="digest-title">{digest.title}</p>}
    <p className="digest-headline">{digest.headline}</p>

    {digest.highlights.length > 0 && <ul className="digest-highlights">
      {digest.highlights.map((highlight, index) => <li key={index}><span className="digest-bullet" aria-hidden="true" />{highlight}</li>)}
    </ul>}

    {digest.observation && <p className="digest-observation"><Eye size={14} aria-hidden="true" /><span>{digest.observation}</span></p>}

    {digest.quote && <blockquote className="digest-quote">{digest.quote}<cite>{t("review.digest.quoteBy")}</cite></blockquote>}

    {digest.echoes.length > 0 && <ul className="digest-echoes">
      {digest.echoes.map((echo, index) => <EchoRow echo={echo} key={index} />)}
    </ul>}
  </section>;
}
