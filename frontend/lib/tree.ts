import type { MemoryNode } from "./types";

export interface MemoryTreeEntry {
  path: string;
  name: string;
  isDir: boolean;
  size: number;
  children: MemoryTreeEntry[];
}

export function buildMemoryTree(nodes: MemoryNode[]): MemoryTreeEntry[] {
  const root: MemoryTreeEntry = { path: "/memories", name: "memories", isDir: true, size: 0, children: [] };
  const byPath = new Map<string, MemoryTreeEntry>([[root.path, root]]);
  const sorted = [...nodes].sort((a, b) => {
    if (a.path === "/memories/MEMORY.md") return -1;
    if (b.path === "/memories/MEMORY.md") return 1;
    return a.path.localeCompare(b.path);
  });

  for (const node of sorted) {
    const parts = node.path.replace(/^\/memories\/?/, "").split("/").filter(Boolean);
    let parent = root;
    let currentPath = "/memories";
    parts.forEach((part, index) => {
      currentPath += `/${part}`;
      let entry = byPath.get(currentPath);
      if (!entry) {
        entry = { path: currentPath, name: part, isDir: index < parts.length - 1 || node.is_dir, size: index === parts.length - 1 ? node.size : 0, children: [] };
        byPath.set(currentPath, entry);
        parent.children.push(entry);
      }
      parent = entry;
    });
  }

  const sortChildren = (entry: MemoryTreeEntry) => {
    entry.children.sort((a, b) => {
      if (a.path === "/memories/MEMORY.md") return -1;
      if (b.path === "/memories/MEMORY.md") return 1;
      if (a.isDir !== b.isDir) return a.isDir ? -1 : 1;
      return a.name.localeCompare(b.name);
    });
    entry.children.forEach(sortChildren);
  };
  sortChildren(root);
  return root.children;
}
