import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  getEvalStatus: vi.fn(),
  getEvalDataset: vi.fn(),
  listEvalRuns: vi.fn(),
  startEvalRun: vi.fn(),
  cancelEvalRun: vi.fn(),
  acknowledgeEvalRun: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  ...api,
  errorMessage: (cause: unknown, fallback: string) => (cause instanceof Error ? cause.message : fallback),
}));

import { EvalPanel } from "./eval-panel";
import type { EvalDataset, EvalRunState, EvalSummary } from "@/lib/types";

afterEach(cleanup);

function summary(overrides: Partial<EvalSummary> = {}): EvalSummary {
  return {
    total: 2, usable: 2, crashed: 0, judge_failed: 0,
    recall: 0.75, correction_rate: null, errors_total: 0, no_op_respected: 1,
    index_issues_total: 0, index_clean_rate: 1,
    writes_total: 3, tool_calls_total: 5, seconds_total: 90,
    ...overrides,
  };
}

function dataset(overrides: Partial<EvalDataset> = {}): EvalDataset {
  return {
    directory: "evals/cases", total: 1, no_op_cases: 1, valid: true,
    cases: [{ id: "01-a", date: "2026-08-06", note: "测什么", conversations: 2, memory_files: 1, facts: 3, corrections: 0, forbidden: 0, no_op: false, problems: [] }],
    ...overrides,
  };
}

function state(overrides: Partial<EvalRunState> = {}): EvalRunState {
  return {
    run_id: "abc", status: "done", total: 2, completed: 2, current_case: "",
    started_at: "2026-08-08T12:00:00", finished_at: "2026-08-08T12:05:00",
    detail: "", saved_path: "eval-runs/x.json", meta: { model: "deepseek-v4-flash" },
    summary: summary(), scores: [],
    ...overrides,
  };
}

beforeEach(() => {
  api.getEvalStatus.mockResolvedValue(null);
  api.getEvalDataset.mockResolvedValue(dataset());
  api.listEvalRuns.mockResolvedValue([]);
});

it("「不适用」显示成横杠，不是 0%", async () => {
  // null 是「这批样本没有可判定的修正项」。显示成 0% 会像质量崩了，
  // 而人会拿着这个假信号去改提示词。
  api.getEvalStatus.mockResolvedValue(state());
  render(<EvalPanel />);

  await waitFor(() => expect(screen.getByText("修正正确率")).toBeInTheDocument());
  expect(screen.getByText("—")).toBeInTheDocument();
  expect(screen.getByText("75%")).toBeInTheDocument();
});

it("跑着的时候显示正在跑哪条，而不只是进度数字", async () => {
  // 一条样本要跑一分钟，只显示「1/6」的话那一分钟里看着像卡死了。
  api.getEvalStatus.mockResolvedValue(state({ status: "running", completed: 1, current_case: "02-conflict" }));
  render(<EvalPanel />);

  await waitFor(() => expect(screen.getByText(/正在跑 02-conflict/)).toBeInTheDocument());
  expect(screen.getByRole("button", { name: /跑着呢/ })).toBeDisabled();
});

it("标注不合法时禁止开跑，并说清楚为什么", async () => {
  api.getEvalDataset.mockResolvedValue(dataset({
    valid: false,
    cases: [{ ...dataset().cases[0], problems: ["no_op 样本不该同时期望写入事实"] }],
  }));
  render(<EvalPanel />);

  await waitFor(() => expect(screen.getByRole("button", { name: /开始评测/ })).toBeDisabled());
  expect(screen.getByText(/标注不合法/)).toBeInTheDocument();
  expect(screen.getByText(/no_op 样本不该同时期望写入事实/)).toBeInTheDocument();
});

it("空数据集也拦住——跑零条样本会「全部通过」", async () => {
  api.getEvalDataset.mockResolvedValue(dataset({ total: 0, cases: [], no_op_cases: 0 }));
  render(<EvalPanel />);

  await waitFor(() => expect(screen.getByText(/数据集是空的/)).toBeInTheDocument());
  expect(screen.getByRole("button", { name: /开始评测/ })).toBeDisabled();
});

it("失败的那轮要说出原因，不能停在「没结果」", async () => {
  api.getEvalStatus.mockResolvedValue(state({ status: "failed", summary: null, detail: "RuntimeError: provider 挂了" }));
  render(<EvalPanel />);

  await waitFor(() => expect(screen.getByText(/provider 挂了/)).toBeInTheDocument());
});

it("没跑过时不显示假的空结果", async () => {
  render(<EvalPanel />);

  await waitFor(() => expect(screen.getByText("还没有跑过评测。")).toBeInTheDocument());
  expect(screen.queryByText("事实召回")).not.toBeInTheDocument();
});

it("被打断的一轮要说出来，不能假装从没跑过", async () => {
  // 后端热重载会带走内存状态。跑了几分钟、烧掉的 token 就这么无声消失，
  // 是这个项目最不能接受的那种静默失败。
  api.getEvalStatus.mockResolvedValue(state({
    status: "interrupted", completed: 3, total: 6, summary: null,
    detail: "这轮评测没跑完就中断了（多半是后端进程重启）。结果没有保存，需要重跑。",
  }));
  render(<EvalPanel />);

  await waitFor(() => expect(screen.getByText(/上一轮（3\/6）/)).toBeInTheDocument());
  expect(screen.getByRole("button", { name: "知道了" })).toBeInTheDocument();
  // 中断的一轮不该显示成一份结果
  expect(screen.queryByText("事实召回")).not.toBeInTheDocument();
});
