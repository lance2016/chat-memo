import { CircleHelp } from "lucide-react";
import type { DailyUsage } from "@/lib/types";

function cacheRate(item: DailyUsage) {
  if (item.input_tokens <= 0) return "—";
  return `${Math.round(item.cached_tokens / item.input_tokens * 100)}%`;
}

export function ReviewUsageCard({ usage, selectedDay, error }: { usage: DailyUsage[]; selectedDay: string; error?: string }) {
  const selected = usage.find((item) => item.day === selectedDay);
  const maxInput = Math.max(...usage.map((item) => item.input_tokens), 1);
  return <section className="review-card"><div className="card-heading"><div><span className="card-kicker">USAGE SIGNALS</span><h2>用量与缓存</h2></div><span className="count-pill">近 7 天</span></div>{error ? <div className="card-state card-state-error">{error}</div> : <><div className="usage-selected">{selected ? <><div><span>输入 tokens</span><strong>{selected.input_tokens.toLocaleString()}</strong></div><div><span>输出 tokens</span><strong>{selected.output_tokens.toLocaleString()}</strong></div><div><span>缓存命中</span><strong>{selected.cached_tokens.toLocaleString()}</strong></div><div><span>命中率</span><strong>{cacheRate(selected)}</strong></div></> : <div className="usage-missing"><CircleHelp size={14} />所选日期不在近 7 天统计范围内</div>}</div><div className="usage-history">{usage.slice(0, 7).map((item) => <div className={item.day === selectedDay ? "selected" : ""} key={item.day}><span>{item.day.slice(5)}</span><i><b style={{ width: `${item.input_tokens ? Math.max(6, item.input_tokens / maxInput * 100) : 0}%` }} /></i><b>{item.input_tokens.toLocaleString()}</b><em>{cacheRate(item)}</em></div>)}</div></>}</section>;
}
