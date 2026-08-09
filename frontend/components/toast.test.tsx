import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider, useToast, type ToastInput } from "./toast";

vi.mock("@/components/i18n-provider", () => ({
  useI18n: () => ({
    locale: "zh-CN",
    t: (key: string) => ({
      "toast.dismiss": "关闭提示",
      "toast.actionFailed": "操作没能完成，请重试",
    }[key] ?? key),
  }),
}));

/** 把 push 暴露给用例，免得每个测试都写一遍触发按钮。 */
let push: (toast: ToastInput) => number;

function Harness() {
  push = useToast().push;
  return null;
}

function mount() {
  render(<ToastProvider><Harness /></ToastProvider>);
}

describe("ToastProvider", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  afterEach(() => {
    vi.useRealTimers();
    cleanup();
  });

  it("shows a toast and drops it once its time is up", async () => {
    mount();
    act(() => { push({ message: "已保存", duration: 3000 }); });
    expect(screen.getByText("已保存")).toBeInTheDocument();

    act(() => { vi.advanceTimersByTime(3100); });
    await waitFor(() => expect(screen.queryByText("已保存")).not.toBeInTheDocument());
  });

  it("keeps the countdown frozen while the pointer rests on it", async () => {
    mount();
    act(() => { push({ message: "已删除「读书笔记」", duration: 3000 }); });

    fireEvent.pointerEnter(screen.getByText("已删除「读书笔记」").closest(".toast-region")!);
    act(() => { vi.advanceTimersByTime(9000); });
    // 撤销按钮在鼠标底下消失是最糟的体验，所以悬停期间必须一直在。
    expect(screen.getByText("已删除「读书笔记」")).toBeInTheDocument();

    fireEvent.pointerLeave(screen.getByText("已删除「读书笔记」").closest(".toast-region")!);
    act(() => { vi.advanceTimersByTime(3100); });
    await waitFor(() => expect(screen.queryByText("已删除「读书笔记」")).not.toBeInTheDocument());
  });

  it("runs the undo action and then closes itself", async () => {
    const run = vi.fn().mockResolvedValue(undefined);
    mount();
    act(() => { push({ message: "已删除", action: { label: "撤销", run } }); });

    fireEvent.click(screen.getByRole("button", { name: "撤销" }));
    await waitFor(() => expect(run).toHaveBeenCalledOnce());
    await waitFor(() => expect(screen.queryByText("已删除")).not.toBeInTheDocument());
  });

  it("stays on screen and explains itself when undo fails", async () => {
    const run = vi.fn().mockRejectedValue(new Error("boom"));
    mount();
    act(() => { push({ message: "已删除", action: { label: "撤销", run } }); });

    fireEvent.click(screen.getByRole("button", { name: "撤销" }));
    // 默默消失会让用户以为撤销成功了 —— 那是最坏的结果。
    await waitFor(() => expect(screen.getByText("操作没能完成，请重试")).toBeInTheDocument());
    expect(screen.getByText("已删除")).toBeInTheDocument();
  });

  it("interrupts the screen reader for failures but not for successes", () => {
    mount();
    act(() => { push({ message: "保存失败", tone: "danger" }); });
    expect(screen.getByRole("alert")).toHaveTextContent("保存失败");

    act(() => { push({ message: "已保存", tone: "success" }); });
    expect(screen.getByRole("status")).toHaveTextContent("已保存");
  });

  it("caps the stack so notifications never become a wall", () => {
    mount();
    act(() => { for (let index = 0; index < 7; index += 1) push({ message: `第 ${index} 条`, duration: 0 }); });

    expect(screen.queryByText("第 0 条")).not.toBeInTheDocument();
    expect(screen.getByText("第 6 条")).toBeInTheDocument();
    expect(document.querySelectorAll(".toast")).toHaveLength(4);
  });

  it("can be dismissed by hand before the timer fires", async () => {
    mount();
    act(() => { push({ message: "已导入 3 份记忆", duration: 0 }); });

    fireEvent.click(screen.getByRole("button", { name: "关闭提示" }));
    await waitFor(() => expect(screen.queryByText("已导入 3 份记忆")).not.toBeInTheDocument());
  });
});
