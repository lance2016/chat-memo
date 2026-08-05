import { describe, expect, it } from "vitest";
import { buildMemoryTree } from "./tree";

describe("buildMemoryTree", () => {
  it("builds directories and puts MEMORY.md first", () => {
    const tree = buildMemoryTree([
      { path: "/memories/z.md", is_dir: false, size: 1 },
      { path: "/memories/profile/preferences.md", is_dir: false, size: 2 },
      { path: "/memories/MEMORY.md", is_dir: false, size: 3 },
    ]);
    expect(tree.map((entry) => entry.name)).toEqual(["MEMORY.md", "profile", "z.md"]);
    expect(tree[1].children[0].path).toBe("/memories/profile/preferences.md");
  });
});
