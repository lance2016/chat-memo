"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { CircleAlert, FlaskConical, Play, RefreshCw, Square, TriangleAlert } from "lucide-react";
import { acknowledgeEvalRun, cancelEvalRun, errorMessage, getEvalDataset, getEvalStatus, listEvalRuns, startEvalRun } from "@/lib/api";
import type { EvalCaseScore, EvalDataset, EvalHistoryEntry, EvalRunState, EvalSummary } from "@/lib/types";

/** 跑一轮要几分钟，轮询频率按「人能忍受多久看不到变化」定，不用更快。 */
const POLL_MS = 2000;

function percent(value: number | null) {
  // null 是「这批样本不适用这个指标」，和 0% 不是一回事 —— 显示成 0% 会像质量崩了。
  return value === null ? "—" : `${Math.round(value * 100)}%`;
}

function formatTime(value: string) {
  if (!value) return "";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(parsed);
}

function SummaryCards({ summary }: { summary: EvalSummary }) {
  // 顺序即确定性：机械指标在前（代码算的，无争议），带噪声的裁判指标在后。
  const cards: { label: string; value: string; warn?: boolean }[] = [
    { label: "可用样本", value: `${summary.usable}/${summary.total}`, warn: summary.usable < summary.total },
    { label: "索引干净率", value: percent(summary.index_clean_rate) },
    { label: "事实召回", value: percent(summary.recall) },
    { label: "修正正确率", value: percent(summary.correction_rate) },
    { label: "引入错误", value: `${summary.errors_total}`, warn: summary.errors_total > 0 },
    { label: "no_op 遵守", value: percent(summary.no_op_respected) },
  ];
  return <div className="eval-summary-cards">
    {cards.map((card) => <div className={card.warn ? "stat-warning" : ""} key={card.label}>
      <span>{card.label}</span><strong>{card.value}</strong>
    </div>)}
  </div>;
}

function ScoreRow({ score }: { score: EvalCaseScore }) {
  const reasons: string[] = [];
  if (score.crashed) reasons.push(score.detail || "执行崩溃");
  if (score.judge_failed) reasons.push("裁判失败，本条指标作废");
  if (score.no_op_respected === false) reasons.push("该沉默的一天写了记忆");
  if (score.index_issues) reasons.push(`${score.index_issues} 个索引问题`);

  return <div className={`eval-score-row ${score.usable ? "" : "eval-score-unusable"}`}>
    <span className="eval-score-name" title={score.case_id}>{score.case_id}</span>
    <span className="eval-score-metrics">
      <em>{percent(score.recall)}</em>
      <em>{percent(score.correction_rate)}</em>
      <em className={score.error_count ? "stat-warning" : ""}>{score.error_count}</em>
      <em>{score.memory_writes}</em>
      <em>{score.seconds.toFixed(0)}s</em>
    </span>
    {reasons.length > 0 && <span className="eval-score-reasons">{reasons.join("；")}</span>}
  </div>;
}

export function EvalPanel() {
  const [dataset, setDataset] = useState<EvalDataset | null>(null);
  const [state, setState] = useState<EvalRunState | null>(null);
  const [history, setHistory] = useState<EvalHistoryEntry[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [judge, setJudge] = useState(true);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const refresh = useCallback(async (withDataset = false) => {
    try {
      const [status, runs, data] = await Promise.all([
        getEvalStatus(),
        listEvalRuns(10),
        withDataset ? getEvalDataset() : Promise.resolve(null),
      ]);
      setState(status);
      setHistory(runs);
      if (data) setDataset(data);
      setError("");
    } catch (cause) {
      setError(errorMessage(cause, "无法读取评测状态"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void refresh(true); }, [refresh]);

  // 只在跑着的时候轮询。跑完就停 —— 一个安静的页面不该一直发请求。
  useEffect(() => {
    if (state?.status !== "running") return;
    timer.current = setTimeout(() => { void refresh(); }, POLL_MS);
    return () => { if (timer.current) clearTimeout(timer.current); };
  }, [state, refresh]);

  const start = async () => {
    setError("");
    try {
      setState(await startEvalRun({ judge }));
    } catch (cause) {
      setError(errorMessage(cause, "无法启动评测"));
    }
  };

  const dismiss = async () => {
    try {
      setState(await acknowledgeEvalRun());
    } catch (cause) {
      setError(errorMessage(cause, "无法清除提示"));
    }
  };

  const stop = async () => {
    try {
      setState(await cancelEvalRun());
      void refresh();
    } catch (cause) {
      setError(errorMessage(cause, "无法停止评测"));
    }
  };

  if (loading) return <div className="centered-empty stats-empty">读取评测状态…</div>;

  const running = state?.status === "running";
  const blocked = dataset ? !dataset.valid || dataset.total === 0 : false;

  return <div className="memory-stats-panel eval-panel">
    <div className="stats-context-bar">
      <span>对固定样本重放真正的每日整理，看它记对了没有</span>
      <strong>{dataset ? `${dataset.total} 条样本` : ""}</strong>
    </div>

    {error && <div className="eval-alert"><TriangleAlert size={14} />{error}</div>}

    <section className="memory-stat-card eval-run-card">
      <div className="stats-card-heading">
        <div><span className="card-kicker">RUN</span><h2>跑一轮评测</h2></div>
        <FlaskConical size={16} />
      </div>
      <div className="eval-run-body">
        <div className="eval-run-controls">
          <button className="primary-button" disabled={running || blocked} onClick={() => void start()}>
            <Play size={13} />{running ? "跑着呢…" : "开始评测"}
          </button>
          {running && <button className="ghost-button" onClick={() => void stop()}><Square size={12} />停止</button>}
          <label className="eval-toggle">
            <input type="checkbox" checked={judge} disabled={running} onChange={(event) => setJudge(event.target.checked)} />
            用模型裁判判分
          </label>
        </div>
        <p className="eval-run-hint">
          {judge
            ? "会对每条样本调两次模型（整理 + 判分），一条约一分钟，期间不要重复触发。"
            : "只跑索引校验和过程指标，不花裁判的 token —— 适合先确认链路通不通。"}
        </p>

        {blocked && <div className="eval-alert eval-alert-block"><CircleAlert size={14} />
          {dataset?.total === 0 ? "数据集是空的，先导出并标注样本" : "有样本的标注不合法，修好才能跑（下面列出了问题）"}
        </div>}

        {running && state && <div className="eval-progress">
          <div className="eval-progress-bar"><i style={{ width: `${state.total ? (state.completed / state.total) * 100 : 0}%` }} /></div>
          <span>{state.completed}/{state.total} · {state.current_case ? `正在跑 ${state.current_case}` : "判分中…"}</span>
        </div>}

        {state?.status === "failed" && <div className="eval-alert"><TriangleAlert size={14} />这轮没跑完：{state.detail}</div>}

        {/* 后端重启会带走内存里的运行状态。不说出来的话，跑了几分钟、烧掉的 token
            就这么无声消失了，界面看起来像从没跑过。 */}
        {state?.status === "interrupted" && <div className="eval-alert">
          <TriangleAlert size={14} />
          <span>上一轮（{state.completed}/{state.total}）{state.detail}</span>
          <button className="ghost-button" onClick={() => void dismiss()}>知道了</button>
        </div>}
      </div>
    </section>

    {state?.summary && state.status !== "running" && <section className="memory-stat-card eval-result-card">
      <div className="stats-card-heading">
        <div><span className="card-kicker">LAST RESULT</span><h2>最近一轮</h2></div>
        <span className="eval-meta">{formatTime(state.finished_at)} · {state.meta.model || "未知模型"}</span>
      </div>
      <SummaryCards summary={state.summary} />
      <div className="eval-score-table">
        <div className="eval-score-head"><span>样本</span><span className="eval-score-metrics"><em>召回</em><em>修正</em><em>错误</em><em>写入</em><em>耗时</em></span></div>
        {state.scores.map((score) => <ScoreRow key={score.case_id} score={score} />)}
      </div>
      <p className="eval-hint">
        分数只用来做<strong>改动前后的对比</strong>，绝对值没有意义。差异小于噪声就当没变化 ——
        噪声要用 <code>python -m app.eval noise</code> 量。
      </p>
    </section>}

    <div className="memory-stats-grid">
      {dataset && <section className="memory-stat-card">
        <div className="stats-card-heading">
          <div><span className="card-kicker">DATASET</span><h2>样本</h2></div>
          <span className="count-pill">{dataset.no_op_cases} 条反例</span>
        </div>
        <div className="eval-case-list">
          {dataset.cases.map((item) => <div className={`eval-case ${item.problems.length ? "eval-case-bad" : ""}`} key={item.id}>
            <div className="eval-case-head">
              <strong>{item.id}</strong>
              {item.no_op ? <span className="eval-tag eval-tag-noop">不该写记忆</span> : <span className="eval-tag">{item.facts} 事实 · {item.corrections} 修正</span>}
            </div>
            {item.note && <p title={item.note}>{item.note}</p>}
            {item.problems.map((problem) => <span className="eval-case-problem" key={problem}><CircleAlert size={11} />{problem}</span>)}
          </div>)}
        </div>
        <p className="eval-hint">
          反例（这天什么都不该写）不能少 —— 只测正例的数据集会奖励一个疯狂写记忆的模型。
        </p>
      </section>}

      <section className="memory-stat-card">
        <div className="stats-card-heading">
          <div><span className="card-kicker">HISTORY</span><h2>历史结果</h2></div>
          <button className="icon-button" title="刷新" aria-label="刷新历史" onClick={() => void refresh(true)}><RefreshCw size={13} /></button>
        </div>
        {history.length ? <div className="eval-history">
          {history.map((entry) => <div className="eval-history-row" key={entry.name}>
            <span className="eval-history-when">{formatTime(entry.created_at) || entry.name}</span>
            <span className="eval-history-model">{entry.model || "—"}{entry.judged ? "" : " · 未判分"}</span>
            <span className="eval-history-score">{entry.summary ? `召回 ${percent(entry.summary.recall)} · 错误 ${entry.summary.errors_total}` : "—"}</span>
          </div>)}
        </div> : <div className="stats-card-empty">还没有跑过评测。</div>}
        <p className="eval-hint">完整结果（含裁判逐条证据）存在 <code>eval-runs/</code>，可以直接 diff。</p>
      </section>
    </div>
  </div>;
}
