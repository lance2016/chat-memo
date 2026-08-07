import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ReviewDigest } from "./review-digest";
import { ReviewOpenLoops } from "./review-open-loops";
import type { DailyDigest, OpenLoop } from "@/lib/types";

afterEach(cleanup);

const DAY = "2026-08-07";

function loop(overrides: Partial<OpenLoop> & { id: number; text: string }): OpenLoop {
  return {
    opened_on: DAY,
    closed_on: null,
    closed_note: null,
    status: "open",
    actor: "consolidation",
    source_conversation_id: null,
    ...overrides,
  };
}

function renderLoops(loops: OpenLoop[], handlers: Partial<Parameters<typeof ReviewOpenLoops>[0]> = {}) {
  const noop = vi.fn(async () => {});
  return render(<ReviewOpenLoops
    loops={loops}
    day={DAY}
    onClose={noop}
    onReopen={noop}
    onDrop={noop}
    onCreate={noop}
    {...handlers}
  />);
}

function groupFor(heading: string) {
  return screen.getByRole("heading", { name: heading }).closest(".loop-group") as HTMLElement;
}

it("按今天新增 / 还挂着 / 今天闭环分组", () => {
  renderLoops([
    loop({ id: 1, text: "今天冒出来的" }),
    loop({ id: 2, text: "上周就挂着的", opened_on: "2026-08-01" }),
    loop({ id: 3, text: "今天做完的", opened_on: "2026-08-02", closed_on: DAY, status: "closed", closed_note: "改完了" }),
  ]);

  expect(within(groupFor("今天新增")).getByText("今天冒出来的")).toBeTruthy();
  expect(within(groupFor("还挂着")).getByText("上周就挂着的")).toBeTruthy();
  expect(within(groupFor("今天闭环")).getByText("今天做完的")).toBeTruthy();
  expect(screen.getByText("改完了")).toBeTruthy();
  // 计数只算还挂着的，闭掉的不该再制造压力
  expect(screen.getByText("2")).toBeTruthy();
});

it("挂了很久的条目标出天数并加重", () => {
  renderLoops([loop({ id: 1, text: "拖了很久", opened_on: "2026-07-28" })]);

  const row = screen.getByText("拖了很久").closest(".loop-row") as HTMLElement;
  expect(within(row).getByText("挂了 10 天")).toBeTruthy();
  expect(row.className).toContain("loop-stale");
});

it("勾掉一条会带上它自己", async () => {
  const onClose = vi.fn(async () => {});
  renderLoops([loop({ id: 7, text: "待办一条" })], { onClose });

  fireEvent.click(screen.getByLabelText("标记为已完成"));

  await waitFor(() => expect(onClose).toHaveBeenCalledWith(expect.objectContaining({ id: 7 })));
});

it("手动新增后清空输入框", async () => {
  const onCreate = vi.fn(async () => {});
  renderLoops([], { onCreate });

  const input = screen.getByPlaceholderText("自己加一条…") as HTMLInputElement;
  fireEvent.change(input, { target: { value: "  自己想起来的事  " } });
  fireEvent.click(screen.getByRole("button", { name: "加上" }));

  await waitFor(() => expect(onCreate).toHaveBeenCalledWith("自己想起来的事"));
  await waitFor(() => expect(input.value).toBe(""));
});

it("没有待办时给空态而不是空白", () => {
  renderLoops([]);
  expect(screen.getByText("没有悬着的事")).toBeTruthy();
});

it("回顾没生成时给出整理入口", () => {
  const onRun = vi.fn();
  render(<ReviewDigest digest={null} running={false} onRun={onRun} />);

  fireEvent.click(screen.getByRole("button", { name: /整理这一天/ }));
  expect(onRun).toHaveBeenCalled();
});

it("回顾以标题和收获为主体", () => {
  const digest: DailyDigest = {
    day: DAY,
    headline: "把语音输入接通了",
    highlights: ["接通本地 ASR", "给历史加了预算"],
    model: "deepseek-v4-flash",
    created_at: "2026-08-07T10:00:00Z",
    updated_at: "2026-08-07T10:00:00Z",
  };
  render(<ReviewDigest digest={digest} running={false} onRun={vi.fn()} />);

  expect(screen.getByText("把语音输入接通了")).toBeTruthy();
  expect(screen.getAllByRole("listitem")).toHaveLength(2);
});
