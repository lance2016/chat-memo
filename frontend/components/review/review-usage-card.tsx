import { ChevronDown, CircleHelp } from "lucide-react";
import type { DailyUsage } from "@/lib/types";
import { useI18n } from "@/components/i18n-provider";

function cacheRate(item: DailyUsage) {
  if (item.input_tokens <= 0) return "—";
  return `${Math.round(item.cached_tokens / item.input_tokens * 100)}%`;
}

export function ReviewUsageCard({ usage, selectedDay, error }: { usage: DailyUsage[]; selectedDay: string; error?: string }) {
  const { locale, t } = useI18n();
  const selected = usage.find((item) => item.day === selectedDay);
  const maxInput = Math.max(...usage.map((item) => item.input_tokens), 1);
  return <details className="review-card review-collapsible-card review-usage-card" open={error ? true : undefined}>
    <summary className="card-heading review-collapsible-heading">
      <div><span className="card-kicker">{t("review.usage.kicker")}</span><h2>{t("review.usage.title")}</h2></div>
      <span className="review-collapsible-meta"><span className="count-pill">{t("review.usage.range")}</span><ChevronDown size={15} /></span>
    </summary>
    {error ? <div className="card-state card-state-error">{error}</div> : <div className="review-usage-body">
      <div className="usage-selected">{selected ? <><div><span>{t("review.usage.input")}</span><strong>{selected.input_tokens.toLocaleString(locale)}</strong></div><div><span>{t("review.usage.output")}</span><strong>{selected.output_tokens.toLocaleString(locale)}</strong></div><div><span>{t("review.usage.cached")}</span><strong>{selected.cached_tokens.toLocaleString(locale)}</strong></div><div><span>{t("review.usage.rate")}</span><strong>{cacheRate(selected)}</strong></div></> : <div className="usage-missing"><CircleHelp size={14} />{t("review.usage.missing")}</div>}</div>
      <div className="usage-history">{usage.slice(0, 7).map((item) => <div className={item.day === selectedDay ? "selected" : ""} key={item.day}><span>{item.day.slice(5)}</span><i><b style={{ width: `${item.input_tokens ? Math.max(6, item.input_tokens / maxInput * 100) : 0}%` }} /></i><b>{item.input_tokens.toLocaleString(locale)}</b><em>{cacheRate(item)}</em></div>)}</div>
    </div>}
  </details>;
}
