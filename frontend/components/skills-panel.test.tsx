import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  listSkills: vi.fn(),
  getSkill: vi.fn(),
  installSkill: vi.fn(),
  uploadSkill: vi.fn(),
  setSkillEnabled: vi.fn(),
  deleteSkill: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  ...api,
  errorMessage: (cause: unknown, fallback: string) => (cause instanceof Error ? cause.message : fallback),
}));

vi.mock("@/components/markdown", () => ({
  Markdown: ({ children }: { children: string }) => <div>{children}</div>,
}));

import { SkillsPanel } from "./skills-panel";
import type { Skill, SkillCatalog } from "@/lib/types";

afterEach(() => { cleanup(); vi.clearAllMocks(); });

function skill(overrides: Partial<Skill> = {}): Skill {
  return {
    name: "pdf", description: "处理 PDF 时使用。", version: "1.0", license: "MIT",
    allowed_tools: [], files: ["references/api.md"], size_bytes: 2048,
    enabled: true, source: "anthropics/skills", ref: "HEAD",
    installed_at: "2026-08-09T10:00:00", error: "", warning: "",
    ...overrides,
  };
}

function catalog(overrides: Partial<SkillCatalog> = {}): SkillCatalog {
  return { root: "/skills", enabled: true, total: 1, active: 1, skills: [skill()], ...overrides };
}

it("列出技能并显示启用数量", async () => {
  api.listSkills.mockResolvedValue(catalog());

  render(<SkillsPanel />);

  expect(await screen.findByText("pdf")).toBeTruthy();
  expect(screen.getByText("处理 PDF 时使用。")).toBeTruthy();
  expect(screen.getByText("1 个 · 1 个已启用")).toBeTruthy();
});

it("坏掉的技能仍然列出来，但开关不能动", async () => {
  // 让它凭空消失的话，人只会以为没装上然后再装一遍
  api.listSkills.mockResolvedValue(catalog({
    active: 0,
    skills: [skill({ error: "SKILL.md 缺少 description", enabled: true })],
  }));

  render(<SkillsPanel />);

  expect(await screen.findByText("SKILL.md 缺少 description")).toBeTruthy();
  expect(screen.getByRole("checkbox")).toHaveProperty("disabled", true);
});

it("安装成功后刷新列表", async () => {
  api.listSkills.mockResolvedValue(catalog({ total: 0, active: 0, skills: [] }));
  api.installSkill.mockResolvedValue({ installed: [{ name: "pdf", description: "", replaced: false }], skipped: [] });

  render(<SkillsPanel />);
  await screen.findByText("还没有技能");

  fireEvent.click(screen.getByRole("button", { name: /添加技能/ }));
  fireEvent.change(screen.getByRole("textbox", { name: "技能来源" }), { target: { value: "anthropics/skills" } });
  api.listSkills.mockResolvedValue(catalog());
  fireEvent.click(screen.getByRole("button", { name: "安装" }));

  await waitFor(() => expect(api.installSkill).toHaveBeenCalledWith("anthropics/skills"));
  expect(await screen.findByText(/装好了：pdf/)).toBeTruthy();
});

it("安装失败时显示后端给的原因", async () => {
  api.listSkills.mockResolvedValue(catalog({ total: 0, active: 0, skills: [] }));
  api.installSkill.mockRejectedValue(new Error("这个包里没有找到 SKILL.md"));

  render(<SkillsPanel />);
  await screen.findByText("还没有技能");

  fireEvent.click(screen.getByRole("button", { name: /添加技能/ }));
  fireEvent.change(screen.getByRole("textbox", { name: "技能来源" }), { target: { value: "foo/bar" } });
  fireEvent.click(screen.getByRole("button", { name: "安装" }));

  expect(await screen.findByText("这个包里没有找到 SKILL.md")).toBeTruthy();
});

it("按 Escape 关闭添加技能窗口", async () => {
  api.listSkills.mockResolvedValue(catalog());

  render(<SkillsPanel />);
  await screen.findByText("pdf");
  fireEvent.click(screen.getByRole("button", { name: /添加技能/ }));
  expect(screen.getByRole("dialog", { name: "添加技能" })).toBeTruthy();

  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.queryByRole("dialog", { name: "添加技能" })).toBeNull();
});

it("被跳过的技能要单独说出来", async () => {
  // 只说「装好了 17 个」的话，人不会发现自己想要的那个恰好没装上
  api.listSkills.mockResolvedValue(catalog());
  api.installSkill.mockResolvedValue({
    installed: [{ name: "pdf", description: "", replaced: false }],
    skipped: [{ path: "skills/weird", reason: "SKILL.md 缺少 description" }],
  });

  render(<SkillsPanel />);
  await screen.findByText("pdf");

  fireEvent.click(screen.getByRole("button", { name: /添加技能/ }));
  fireEvent.change(screen.getByRole("textbox", { name: "技能来源" }), { target: { value: "anthropics/skills" } });
  fireEvent.click(screen.getByRole("button", { name: "安装" }));

  expect(await screen.findByText("跳过了 1 个")).toBeTruthy();
  expect(screen.getByText("SKILL.md 缺少 description")).toBeTruthy();
});

it("删除要先确认", async () => {
  api.listSkills.mockResolvedValue(catalog());
  api.getSkill.mockResolvedValue({ ...skill(), body: "# PDF" });
  api.deleteSkill.mockResolvedValue(undefined);

  render(<SkillsPanel />);
  await screen.findByText("pdf");

  fireEvent.click(screen.getByRole("button", { name: "查看技能 pdf" }));
  fireEvent.click(await screen.findByRole("button", { name: "删除技能 pdf" }));
  expect(api.deleteSkill).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole("button", { name: "删除" }));
  await waitFor(() => expect(api.deleteSkill).toHaveBeenCalledWith("pdf"));
});
