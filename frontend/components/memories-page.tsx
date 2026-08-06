"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { diffLines } from "diff";
import { Activity, BarChart3, ChevronDown, ChevronRight, File, FileText, Folder, FolderOpen, History, Menu, RefreshCw, RotateCcw, Save, Trash2, TriangleAlert } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { deleteMemory, errorMessage, getMemory, getMemoryStats, listMemoryNodes, listMemoryVersions, restoreMemoryVersion, saveMemory } from "@/lib/api";
import { buildMemoryTree, type MemoryTreeEntry } from "@/lib/tree";
import type { Memory, MemoryNode, MemoryStats, MemoryVersion } from "@/lib/types";
import { Markdown } from "@/components/markdown";
import { WorkspaceTopbar } from "@/components/workspace-topbar";
import { LatestRequest } from "@/lib/latest-request";
import { confirmAppNavigation, useNavigationGuard } from "@/lib/navigation-guard";
import { ConfirmDialog } from "@/components/confirm-dialog";

function actorLabel(actor: MemoryVersion["actor"]) {
  return actor === "chat" ? "聊天" : actor === "consolidation" ? "每日整理" : "手动编辑";
}

function formatTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric" }).format(new Date(`${value}T00:00:00`));
}

function actorLabelFromName(actor: string) {
  return actor === "chat" ? "聊天" : actor === "consolidation" ? "每日整理" : "手动编辑";
}

function memoryLabel(path: string) {
  const relative = path.replace(/^\/memories\//, "").replace(/\.md$/, "");
  const name = relative.split("/").pop() || relative;
  return name === "MEMORY" ? "记忆索引" : name.replace(/[-_]/g, " ");
}

function TreeEntryView({ entry, selected, onSelect, onDeleteDirectory, depth = 0 }: { entry: MemoryTreeEntry; selected: string; onSelect: (entry: MemoryTreeEntry) => void; onDeleteDirectory: (entry: MemoryTreeEntry) => void; depth?: number }) {
  const [open, setOpen] = useState(entry.path === "/memories" || entry.path === "/memories/profile");
  const isIndex = entry.path === "/memories/MEMORY.md";
  return (
    <div>
      <div className={`tree-row ${selected === entry.path ? "selected" : ""}`} style={{ paddingLeft: `${9 + depth * 16}px` }}>
        <button className="tree-row-main" type="button" onClick={() => entry.isDir ? setOpen((value) => !value) : onSelect(entry)} aria-expanded={entry.isDir ? open : undefined}>
          {entry.isDir ? open ? <ChevronDown size={13} /> : <ChevronRight size={13} /> : <span style={{ width: 13 }} />}
          {entry.isDir ? open ? <FolderOpen size={14} color="#a8baff" /> : <Folder size={14} color="#8292b7" /> : <File size={14} color={isIndex ? "var(--accent)" : "#8c98aa"} />}
          <span className="tree-label">{entry.name}</span>{isIndex && <span className="tree-badge">INDEX</span>}
        </button>
        {entry.isDir && entry.path !== "/memories" && <button className="icon-button tree-delete" type="button" title="递归删除目录" aria-label={`递归删除目录 ${entry.path}`} onClick={() => onDeleteDirectory(entry)}><Trash2 size={12} /></button>}
      </div>
      {entry.isDir && open && entry.children.map((child) => <TreeEntryView key={child.path} entry={child} selected={selected} onSelect={onSelect} onDeleteDirectory={onDeleteDirectory} depth={depth + 1} />)}
    </div>
  );
}

function DiffView({ before, after }: { before: string; after: string }) {
  const chunks = diffLines(before, after);
  return <div className="diff-panel">{chunks.length === 0 ? <span className="diff-line">没有变化</span> : chunks.map((chunk, index) => <span className={`diff-line ${chunk.added ? "diff-added" : chunk.removed ? "diff-removed" : ""}`} key={`${index}-${chunk.value}`}>{chunk.added ? "+ " : chunk.removed ? "- " : "  "}{chunk.value}</span>)}</div>;
}

function MemoryStatsPanel({ stats, loading, error, onRetry, onOpenFile, onDelete }: { stats: MemoryStats | null; loading: boolean; error: string; onRetry: () => void; onOpenFile: (path: string) => void; onDelete: (path: string) => void }) {
  if (loading) return <div className="centered-empty stats-empty">读取记忆使用率…</div>;
  if (!stats) return <div className="centered-empty stats-empty"><div className="centered-state">{error && <TriangleAlert size={20} />}<strong>{error ? "无法读取记忆活动" : "暂无记忆使用率数据"}</strong>{error && <span>{error}</span>}{error && <button className="ghost-button" onClick={onRetry}><RefreshCw size={12} />重试</button>}</div></div>;

  const maxReads = Math.max(...stats.top.map((item) => item.reads), 1);
  const maxDaily = Math.max(...stats.daily.flatMap((item) => [item.reads, item.writes]), 1);
  const maxActor = Math.max(...stats.by_actor.flatMap((item) => [item.reads, item.writes]), 1);
  const activeDays = stats.daily.filter((item) => item.reads + item.writes > 0).slice(-14);

  return <div className="memory-stats-panel">
    <div className="stats-heading"><div><div className="eyebrow">Memory activity</div><h1>记忆活动</h1><p>查看模型真正读取和更新了哪些长期记忆。索引摘要注入不计入读取次数。</p></div><span className="stats-period">近 30 天</span></div>
    <div className="memory-stat-cards"><div><span>记忆文件</span><strong>{stats.total_memories}</strong></div><div><span>主动读取</span><strong>{stats.total_reads}</strong></div><div><span>写入次数</span><strong>{stats.total_writes}</strong></div><div className={stats.missed_reads ? "stat-warning" : ""}><span>未命中读取</span><strong>{stats.missed_reads}</strong></div></div>
    <div className="memory-stats-grid">
      <section className="memory-stat-card"><div className="stats-card-heading"><div><span className="card-kicker">MOST USED</span><h2>经常使用的记忆</h2></div><Activity size={16} /></div>{stats.top.length ? <div className="stat-list">{stats.top.map((item) => <button className="stat-row stat-row-button" key={item.path} onClick={() => onOpenFile(item.path)}><span className="stat-row-label" title={item.path}><strong>{memoryLabel(item.path)}</strong><small>{item.path.replace("/memories/", "")}</small></span><i><b style={{ width: `${Math.max(4, item.reads / maxReads * 100)}%` }} /></i><em>{item.reads}</em></button>)}</div> : <div className="stats-card-empty">还没有记忆读取记录。</div>}</section>
      <section className="memory-stat-card"><div className="stats-card-heading"><div><span className="card-kicker">ACTIVE DAYS</span><h2>发生读写的日期</h2></div><span className="legend"><i className="legend-read" />读 <i className="legend-write" />写</span></div>{activeDays.length ? <div className="daily-bars">{activeDays.map((item) => <div className="daily-bar-row" key={item.day}><span>{formatDate(item.day)}</span><i><b className="bar-read" style={{ width: `${Math.max(item.reads ? 5 : 0, item.reads / maxDaily * 100)}%` }} /><b className="bar-write" style={{ width: `${Math.max(item.writes ? 5 : 0, item.writes / maxDaily * 100)}%` }} /></i><em>{item.reads + item.writes}</em></div>)}</div> : <div className="stats-card-empty">近 30 天没有发生记忆读写。</div>}</section>
      <section className="memory-stat-card"><div className="stats-card-heading"><div><span className="card-kicker">BY ACTOR</span><h2>变更来源</h2></div></div>{stats.by_actor.length ? <div className="stat-list">{stats.by_actor.map((item) => <div className="actor-stat-row" key={item.actor}><span className={`actor-badge actor-${item.actor}`}>{actorLabelFromName(item.actor)}</span><i><b style={{ width: `${Math.max(item.reads ? 4 : 0, item.reads / maxActor * 100)}%` }} /></i><em>读 {item.reads} · 写 {item.writes}</em></div>)}</div> : <div className="stats-card-empty">暂无来源统计。</div>}</section>
      <section className="memory-stat-card"><div className="stats-card-heading"><div><span className="card-kicker">NO DEEP READ</span><h2>尚未展开细节</h2></div><span className="count-pill">{stats.never_read}</span></div>{stats.unused.length ? <div className="stat-list">{stats.unused.map((item) => <div className="unused-row" key={item.path}><button className="stat-row-button" onClick={() => onOpenFile(item.path)}><strong title={item.path}>{item.path}</strong><span>{item.content_chars.toLocaleString()} 字符 · {item.idle_days === null ? "从未展开" : `闲置 ${item.idle_days} 天`}</span></button><button className="icon-button" title="删除记忆" aria-label={`删除${item.path}`} onClick={() => onDelete(item.path)}><Trash2 size={13} /></button></div>)}</div> : <div className="stats-card-empty">所有记忆都至少展开过一次。</div>}</section>
    </div>
  </div>;
}

export function MemoriesPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [nodes, setNodes] = useState<MemoryNode[]>([]);
  const [selectedPath, setSelectedPath] = useState(searchParams.get("path") || "");
  const [memory, setMemory] = useState<Memory | null>(null);
  const [content, setContent] = useState("");
  const [versions, setVersions] = useState<MemoryVersion[]>([]);
  const [olderId, setOlderId] = useState<number | null>(null);
  const [newerId, setNewerId] = useState<number | null>(null);
  const [tab, setTab] = useState<"edit" | "preview">("edit");
  const [loadingTree, setLoadingTree] = useState(true);
  const [loadingFile, setLoadingFile] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [treeError, setTreeError] = useState("");
  const [treeOpen, setTreeOpen] = useState(false);
  const [showStats, setShowStats] = useState(searchParams.get("view") === "stats");
  const [stats, setStats] = useState<MemoryStats | null>(null);
  const [loadingStats, setLoadingStats] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<{ path: string; isDirectory: boolean } | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [restoreTarget, setRestoreTarget] = useState<MemoryVersion | null>(null);
  const fileRequestsRef = useRef(new LatestRequest());
  const statsRequestsRef = useRef(new LatestRequest());
  const hasChanges = memory !== null && content !== memory.content;

  useNavigationGuard(hasChanges, "当前记忆文件有未保存的修改，确定放弃并离开吗？");

  useEffect(() => () => {
    fileRequestsRef.current.invalidate();
    statsRequestsRef.current.invalidate();
  }, []);

  const loadTree = useCallback(async () => {
    setTreeError("");
    const result = await listMemoryNodes();
    setNodes(result);
    return result;
  }, []);

  const syncTreeSelection = useCallback((result: MemoryNode[]) => {
    const files = result.filter((node) => !node.is_dir);
    const requested = searchParams.get("path");
    const fallback = files.find((file) => file.path === "/memories/MEMORY.md")?.path ?? files[0]?.path ?? "";
    setSelectedPath((current) => requested && files.some((file) => file.path === requested) ? requested : files.some((file) => file.path === current) ? current : fallback);
  }, [searchParams]);

  useEffect(() => {
    void loadTree().then(syncTreeSelection).catch((cause: unknown) => setTreeError(errorMessage(cause, "无法加载记忆树"))).finally(() => setLoadingTree(false));
  }, [loadTree, syncTreeSelection]);

  const loadFile = useCallback(async (path: string) => {
    const request = fileRequestsRef.current.begin();
    setLoadingFile(true);
    setError("");
    setMessage("");
    try {
      const [file, history] = await Promise.all([getMemory(path), listMemoryVersions(path)]);
      if (!fileRequestsRef.current.isCurrent(request)) return;
      setMemory(file);
      setContent(file.content);
      setVersions(history);
      setNewerId(history[0]?.id ?? null);
      setOlderId(history[1]?.id ?? history[0]?.id ?? null);
    } catch (cause) {
      if (!fileRequestsRef.current.isCurrent(request)) return;
      setMemory(null);
      setError(errorMessage(cause, "无法打开记忆文件"));
    } finally {
      if (fileRequestsRef.current.isCurrent(request)) setLoadingFile(false);
    }
  }, []);

  const loadStats = useCallback(async () => {
    const request = statsRequestsRef.current.begin();
    setLoadingStats(true);
    setError("");
    try {
      const result = await getMemoryStats(30, 10);
      if (statsRequestsRef.current.isCurrent(request)) setStats(result);
    } catch (cause) {
      if (!statsRequestsRef.current.isCurrent(request)) return;
      setError(errorMessage(cause, "无法加载记忆使用率"));
    } finally {
      if (statsRequestsRef.current.isCurrent(request)) setLoadingStats(false);
    }
  }, []);

  useEffect(() => { if (selectedPath) void loadFile(selectedPath); }, [loadFile, selectedPath]);
  useEffect(() => { if (showStats) void loadStats(); }, [loadStats, showStats]);

  const selectFile = (entry: MemoryTreeEntry) => {
    if (entry.isDir) return;
    if (entry.path !== selectedPath && !confirmAppNavigation()) return;
    setSelectedPath(entry.path);
    setTreeOpen(false);
    router.push(`/memories?path=${encodeURIComponent(entry.path)}`);
  };

  const save = async () => {
    if (!memory || saving) return;
    setSaving(true); setError(""); setMessage("");
    try {
      const saved = await saveMemory(memory.path, content);
      setMemory(saved);
      setContent(saved.content);
      const history = await listMemoryVersions(saved.path);
      setVersions(history); setNewerId(history[0]?.id ?? null); setOlderId(history[1]?.id ?? history[0]?.id ?? null);
      setMessage("已保存，版本记录标记为手动编辑");
    } catch (cause) { setError(errorMessage(cause, "保存失败")); } finally { setSaving(false); }
  };

  const removePath = async (path: string, isDirectory = false) => {
    try {
      await deleteMemory(path);
      const updated = await loadTree();
      if (memory?.path === path || isDirectory && memory?.path.startsWith(`${path}/`)) { setMemory(null); setContent(""); setVersions([]); }
      const next = updated.find((node) => !node.is_dir && node.path === "/memories/MEMORY.md") ?? updated.find((node) => !node.is_dir);
      if (next) { setSelectedPath(next.path); if (!showStats) router.push(`/memories?path=${encodeURIComponent(next.path)}`); } else setSelectedPath("");
      if (showStats) void loadStats();
      setMessage(isDirectory ? "已递归删除目录" : "已删除记忆");
      return true;
    } catch (cause) {
      setError(errorMessage(cause, "删除失败"));
      return false;
    }
  };

  const confirmDelete = async () => {
    const target = deleteTarget;
    if (!target || deleting) return;
    setDeleting(true);
    setError("");
    const deleted = await removePath(target.path, target.isDirectory);
    if (deleted) setDeleteTarget(null);
    setDeleting(false);
  };

  const requestRestore = () => {
    const selected = versions.find((version) => version.id === olderId);
    if (!selected || !memory) return;
    setRestoreTarget(selected);
  };

  const confirmRestore = async () => {
    const selected = restoreTarget;
    if (!selected || !memory || restoring) return;
    setRestoring(true);
    setError("");
    setMessage("");
    try {
      const restored = await restoreMemoryVersion(selected.id);
      setSelectedPath(restored.path);
      await Promise.all([loadTree(), loadFile(restored.path)]);
      setTab("preview");
      setMessage(`已恢复 ${formatTime(selected.created_at)} 的版本，并新增一条手动历史记录`);
      setRestoreTarget(null);
    } catch (cause) {
      setError(errorMessage(cause, "恢复版本失败"));
    } finally {
      setRestoring(false);
    }
  };

  const tree = useMemo(() => buildMemoryTree(nodes), [nodes]);
  const deleteFileCount = deleteTarget?.isDirectory ? nodes.filter((node) => !node.is_dir && node.path.startsWith(`${deleteTarget.path}/`)).length : 0;
  const deletingCurrentWithChanges = hasChanges && deleteTarget !== null && (deleteTarget.path === memory?.path || deleteTarget.isDirectory && memory?.path.startsWith(`${deleteTarget.path}/`));
  const deleteWarning = [
    deleteTarget?.isDirectory ? `该目录下的 ${deleteFileCount} 个记忆文件会被一起删除。` : "该文件会立即从长期记忆中移除。",
    deletingCurrentWithChanges ? "当前未保存的编辑也会丢失。" : "",
  ].filter(Boolean).join(" ");
  const older = versions.find((version) => version.id === olderId);
  const newer = versions.find((version) => version.id === newerId);
  const toggleStats = () => {
    const next = !showStats;
    setShowStats(next);
    setMessage("");
    setError("");
    router.push(next ? "/memories?view=stats" : selectedPath ? `/memories?path=${encodeURIComponent(selectedPath)}` : "/memories");
  };

  const openStatsFile = (path: string) => {
    if (path !== selectedPath && !confirmAppNavigation()) return;
    setShowStats(false);
    setSelectedPath(path);
    router.push(`/memories?path=${encodeURIComponent(path)}`);
  };

  return (
    <div className="memory-shell">
      <WorkspaceTopbar active="memories" subtitle="Memory workspace" />
      <div className="memory-workspace">
      <aside className={`memory-tree-panel ${treeOpen ? "mobile-open" : ""}`}>
        <div className="memory-header"><h1>长期记忆</h1><p>模型会在聊天中读取和更新这些文件。这里保留每次变更的完整历史。</p></div>
        <div className="tree">{loadingTree ? <div className="centered-empty">加载中…</div> : treeError ? <div className="centered-empty"><div className="centered-state"><TriangleAlert size={20} /><strong>无法加载记忆目录</strong><span>{treeError}</span><button className="ghost-button" onClick={() => { setLoadingTree(true); void loadTree().then(syncTreeSelection).catch((cause: unknown) => setTreeError(errorMessage(cause, "无法加载记忆树"))).finally(() => setLoadingTree(false)); }}><RefreshCw size={12} />重试</button></div></div> : tree.length ? tree.map((entry) => <TreeEntryView key={entry.path} entry={entry} selected={selectedPath} onSelect={selectFile} onDeleteDirectory={(entry) => setDeleteTarget({ path: entry.path, isDirectory: true })} />) : <div className="centered-empty"><div className="centered-state"><strong>还没有长期记忆</strong><span>当助手保存值得长期保留的信息后，文件会出现在这里。</span></div></div>}</div>
      </aside>
      {treeOpen && <button className="sidebar-backdrop" aria-label="关闭记忆树" onClick={() => setTreeOpen(false)} />}
      <main className="memory-editor-panel">
        <header className="editor-topbar">
          <div className="path-title"><button className="icon-button mobile-menu" aria-label="打开记忆树" onClick={() => setTreeOpen(true)}><Menu size={19} /></button><FileText size={16} color="var(--accent)" /><div className="memory-context"><span>记忆管理</span><ChevronRight size={13} /><strong>{showStats ? "使用分析" : memory?.path ?? "选择一个文件"}</strong>{!showStats && hasChanges && <em>未保存</em>}</div></div>
          <div className="editor-actions"><div className="memory-view-switcher" role="tablist" aria-label="记忆视图"><button className={!showStats ? "active" : ""} role="tab" aria-selected={!showStats} onClick={() => showStats && toggleStats()}><FileText size={13} />文件</button><button className={showStats ? "active" : ""} role="tab" aria-selected={showStats} onClick={() => !showStats && toggleStats()}><BarChart3 size={13} />使用分析</button></div>{!showStats && memory && <><button className="danger-button" onClick={() => setDeleteTarget({ path: memory.path, isDirectory: false })}><Trash2 size={13} />删除</button><button className="primary-button" disabled={!hasChanges || saving} onClick={() => void save()}><Save size={13} />{saving ? "保存中…" : "保存"}</button></>}</div>
        </header>
        {showStats ? <MemoryStatsPanel stats={stats} loading={loadingStats} error={error} onRetry={() => void loadStats()} onOpenFile={openStatsFile} onDelete={(path) => setDeleteTarget({ path, isDirectory: false })} /> : !memory ? <div className="centered-empty">{loadingFile ? "打开文件中…" : <div className="centered-state">{error && <TriangleAlert size={20} />}<strong>{error ? "无法打开记忆文件" : "选择一份长期记忆"}</strong><span>{error || "从左侧目录选择文件，查看内容与完整版本历史。"}</span>{error && selectedPath && <button className="ghost-button" onClick={() => void loadFile(selectedPath)}><RefreshCw size={12} />重试</button>}</div>}</div> : <>
          <div className="editor-tabs"><button className={`editor-tab ${tab === "edit" ? "active" : ""}`} onClick={() => setTab("edit")}>编辑</button><button className={`editor-tab ${tab === "preview" ? "active" : ""}`} onClick={() => setTab("preview")}>预览</button></div>
          <div className="editor-area">{loadingFile ? <div className="centered-empty">打开文件中…</div> : tab === "edit" ? <textarea className="editor-textarea" value={content} onChange={(event) => setContent(event.target.value)} spellCheck={false} /> : <div className="preview assistant-content"><Markdown>{content}</Markdown></div>}</div>
          <section className="versions-panel"><div className="versions-head"><span><History size={14} style={{ verticalAlign: "-3px", marginRight: 5 }} />版本历史（{versions.length}）</span><div className="version-selectors">{versions.length > 0 && <><select aria-label="较旧版本" value={olderId ?? ""} onChange={(event) => setOlderId(Number(event.target.value))}>{versions.map((version) => <option key={version.id} value={version.id}>{formatTime(version.created_at)} · {actorLabel(version.actor)}</option>)}</select><select aria-label="较新版本" value={newerId ?? ""} onChange={(event) => setNewerId(Number(event.target.value))}>{versions.map((version) => <option key={version.id} value={version.id}>{formatTime(version.created_at)} · {actorLabel(version.actor)}</option>)}</select><button className="ghost-button" onClick={requestRestore} disabled={!older || restoring}><RotateCcw size={12} />{restoring ? "恢复中…" : "恢复"}</button></>}</div></div>
            {older && newer && older.id !== newer.id && <DiffView before={older.content} after={newer.content} />}
            <div className="version-list">{versions.length ? versions.map((version) => <div className="version-row" key={version.id}><span className={`actor-badge actor-${version.actor}`}>{actorLabel(version.actor)}</span><button onClick={() => { setOlderId(version.id); setTab("preview"); }}>{version.operation} · {formatTime(version.created_at)}</button></div>) : <span className="topbar-meta">暂无版本记录</span>}</div>
          </section>
          {(message || error) && <div className={`editor-notice ${error ? "danger-text" : ""}`}>{error || message}</div>}
        </>}
      </main>
      </div>
      <ConfirmDialog
        open={deleteTarget !== null}
        title={deleteTarget?.isDirectory ? "递归删除这个目录？" : "删除这份记忆？"}
        description={deleteTarget?.isDirectory ? "目录本身以及目录中的全部记忆文件都会从当前记忆树中移除。" : "助手后续将无法再读取这份记忆。历史版本仍会保留，可从每日回顾中恢复。"}
        subject={deleteTarget?.path}
        warning={deleteWarning}
        confirmLabel={deleteTarget?.isDirectory ? `删除目录及 ${deleteFileCount} 个文件` : "删除记忆文件"}
        busy={deleting}
        onCancel={() => setDeleteTarget(null)}
        onConfirm={() => void confirmDelete()}
      />
      <ConfirmDialog
        open={restoreTarget !== null}
        title="恢复这份历史版本？"
        description="当前文件会被替换为所选版本的内容，并新增一条手动恢复记录。"
        subject={restoreTarget ? `${restoreTarget.path} · ${formatTime(restoreTarget.created_at)}` : undefined}
        warning={hasChanges ? "当前编辑尚未保存，恢复后这些修改会被覆盖。" : undefined}
        confirmLabel="恢复版本"
        busy={restoring}
        onCancel={() => setRestoreTarget(null)}
        onConfirm={() => void confirmRestore()}
      />
    </div>
  );
}
