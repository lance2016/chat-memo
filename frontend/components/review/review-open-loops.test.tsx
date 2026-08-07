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

it("按今天发现 / 持续关注 / 今天已处理分组", () => {
  renderLoops([
    loop({ id: 1, text: "今天冒出来的" }),
    loop({ id: 2, text: "上周就挂着的", opened_on: "2026-08-01" }),
    loop({ id: 3, text: "今天做完的", opened_on: "2026-08-02", closed_on: DAY, status: "closed", closed_note: "改完了" }),
  ]);

  expect(within(groupFor("今天发现")).getByText("今天冒出来的")).toBeTruthy();
  expect(within(groupFor("持续关注")).getByText("上周就挂着的")).toBeTruthy();
  expect(within(groupFor("今天已处理")).getByText("今天做完的")).toBeTruthy();
  expect(screen.getByText("改完了")).toBeTruthy();
  // 计数只算还挂着的，闭掉的不该再制造压力
  expect(screen.getByText("2")).toBeTruthy();
});

it("关注很久的条目标出天数并加重", () => {
  renderLoops([loop({ id: 1, text: "拖了很久", opened_on: "2026-07-28" })]);

  const row = screen.getByText("拖了很久").closest(".loop-row") as HTMLElement;
  expect(within(row).getByText("已关注 10 天")).toBeTruthy();
  expect(row.className).toContain("loop-stale");
});

it("勾掉一条会带上它自己", async () => {
  const onClose = vi.fn(async () => {});
  renderLoops([loop({ id: 7, text: "待办一条" })], { onClose });

  fireEvent.click(screen.getByLabelText("标记为已处理"));

  await waitFor(() => expect(onClose).toHaveBeenCalledWith(expect.objectContaining({ id: 7 })));
});

it("手动新增后清空输入框", async () => {
  const onCreate = vi.fn(async () => {});
  renderLoops([], { onCreate });

  fireEvent.click(screen.getByRole("button", { name: "记一条" }));
  const input = screen.getByPlaceholderText("记下一件没有明确日期的事…") as HTMLInputElement;
  fireEvent.change(input, { target: { value: "  自己想起来的事  " } });
  fireEvent.click(screen.getByRole("button", { name: "添加" }));

  await waitFor(() => expect(onCreate).toHaveBeenCalledWith("自己想起来的事"));
  await waitFor(() => expect(screen.queryByPlaceholderText("记下一件没有明确日期的事…")).toBeNull());
});

it("没有关注事项时使用紧凑空态并默认收起输入框", () => {
  const { container } = renderLoops([]);
  expect(screen.getByText("今天没有需要额外关注的事")).toBeTruthy();
  expect(screen.queryByPlaceholderText("记下一件没有明确日期的事…")).toBeNull();
  expect(container.querySelector(".review-open-loops")?.className).toContain("is-empty");
});

it("回顾没生成时给出整理入口", () => {
  const onRun = vi.fn();
  render(<ReviewDigest digest={null} running={false} onRun={onRun} />);

  fireEvent.click(screen.getByRole("button", { name: /整理这一天/ }));
  expect(onRun).toHaveBeenCalled();
});

function digestOf(overrides: Partial<DailyDigest> = {}): DailyDigest {
  return {
    day: DAY,
    headline: "把语音输入接通了",
    highlights: ["接通本地 ASR", "给历史加了预算"],
    title: "",
    observation: "",
    quote: "",
    echoes: [],
    model: "deepseek-v4-flash",
    created_at: "2026-08-07T10:00:00Z",
    updated_at: "2026-08-07T10:00:00Z",
    ...overrides,
  };
}

it("回顾以标题和收获为主体", () => {
  render(<ReviewDigest digest={digestOf()} running={false} onRun={vi.fn()} />);

  expect(screen.getByText("把语音输入接通了")).toBeTruthy();
  expect(screen.getAllByRole("listitem")).toHaveLength(2);
});

it("叙事字段齐全时都渲染出来", () => {
  render(<ReviewDigest running={false} onRun={vi.fn()} digest={digestOf({
    title: "台风夜煮虾滑的那天",
    observation: "你今天两次让我写关于深夜和陪伴的故事。",
    quote: "想回家给她煮一碗",
    echoes: [{ kind: "followup", text: "8/05 说标题太慢，今天解决了" }],
  })} />);

  expect(screen.getByText("台风夜煮虾滑的那天")).toBeTruthy();
  expect(screen.getByText("你今天两次让我写关于深夜和陪伴的故事。")).toBeTruthy();
  expect(screen.getByText("想回家给她煮一碗")).toBeTruthy();
  expect(screen.getByText("8/05 说标题太慢，今天解决了")).toBeTruthy();
});

it("叙事字段为空时不留空壳", () => {
  const { container } = render(<ReviewDigest digest={digestOf()} running={false} onRun={vi.fn()} />);

  // 老 digest 没有这四样，空字符串不能渲染成一条空的引言或分隔线
  expect(container.querySelector(".digest-title")).toBeNull();
  expect(container.querySelector(".digest-observation")).toBeNull();
  expect(container.querySelector(".digest-quote")).toBeNull();
  expect(container.querySelector(".digest-echoes")).toBeNull();
});
