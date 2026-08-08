import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  getEvalCase: vi.fn(),
  saveEvalExpect: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  ...api,
  errorMessage: (cause: unknown, fallback: string) => (cause instanceof Error ? cause.message : fallback),
}));

import { EvalAnnotator } from "./eval-annotator";
import type { EvalCaseDetail } from "@/lib/types";

afterEach(cleanup);

function detail(overrides: Partial<EvalCaseDetail> = {}): EvalCaseDetail {
  return {
    id: "2026-08-06",
    date: "2026-08-06",
    note: "从真实数据导出",
    memory_before: { "/memories/MEMORY.md": "# 记忆索引" },
    conversations: [{ title: "会话", messages: [{ role: "user", text: "我对花生过敏" }] }],
    expect: { facts: [], corrections: [], forbidden: [], no_op: false },
    problems: [],
    ...overrides,
  };
}

beforeEach(() => {
  api.getEvalCase.mockResolvedValue(detail());
  api.saveEvalExpect.mockImplementation(async (_id: string, expect_: unknown) => detail({ expect: expect_ as never }));
});

it("对话和记忆快照是只读的输入，不提供编辑", async () => {
  // 改了就不再是同一条样本，之前跑出来的结果也就没法比了
  render(<EvalAnnotator caseId="2026-08-06" onClose={vi.fn()} onSaved={vi.fn()} />);

  await waitFor(() => expect(screen.getByText(/这天的对话/)).toBeInTheDocument());
  expect(screen.getByText(/冻结的输入/)).toBeInTheDocument();
  expect(screen.getByText("我对花生过敏").closest("input")).toBeNull();
});

it("no_op 是显眼的开关，并且会隐藏正例字段", async () => {
  // 反例不能少：只测正例的数据集会奖励一个疯狂写记忆的模型
  render(<EvalAnnotator caseId="2026-08-06" onClose={vi.fn()} onSaved={vi.fn()} />);

  await waitFor(() => expect(screen.getByText("该记住的事实")).toBeInTheDocument());
  fireEvent.click(screen.getByRole("checkbox"));

  expect(screen.queryByText("该记住的事实")).not.toBeInTheDocument();
  expect(screen.getByText(/至少留两三条/)).toBeInTheDocument();
});

it("保存后把标注回传给后端", async () => {
  render(<EvalAnnotator caseId="2026-08-06" onClose={vi.fn()} onSaved={vi.fn()} />);

  await waitFor(() => expect(screen.getByText("该记住的事实")).toBeInTheDocument());
  fireEvent.click(screen.getAllByRole("button", { name: /添加/ })[0]);
  fireEvent.change(screen.getByPlaceholderText(/花生过敏/), { target: { value: "用户对花生过敏" } });
  fireEvent.click(screen.getByRole("button", { name: /保存标注/ }));

  await waitFor(() => expect(api.saveEvalExpect).toHaveBeenCalledWith(
    "2026-08-06",
    expect.objectContaining({ facts: ["用户对花生过敏"] }),
  ));
});

it("标注问题直接显示出来，不用等到开跑才发现", async () => {
  api.getEvalCase.mockResolvedValue(detail({ problems: ["既不是 no_op，又没标任何期望"] }));
  render(<EvalAnnotator caseId="2026-08-06" onClose={vi.fn()} onSaved={vi.fn()} />);

  await waitFor(() => expect(screen.getByText(/又没标任何期望/)).toBeInTheDocument());
});

it("读不到样本时说清楚，而不是显示一个空表单", async () => {
  api.getEvalCase.mockRejectedValue(new Error("没有这条样本"));
  render(<EvalAnnotator caseId="nope" onClose={vi.fn()} onSaved={vi.fn()} />);

  await waitFor(() => expect(screen.getByText("没有这条样本")).toBeInTheDocument());
  expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
});
