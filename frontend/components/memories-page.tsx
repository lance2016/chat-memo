"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { diffLines } from "diff";
import { Activity, BarChart3, CalendarDays, ChevronDown, ChevronRight, File, FileText, Folder, FolderOpen, History, Menu, MessageSquare, RotateCcw, Save, Settings2, Trash2 } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { deleteMemory, errorMessage, getMemory, getMemoryStats, listMemoryNodes, listMemoryVersions, restoreMemoryVersion, saveMemory } from "@/lib/api";
import { buildMemoryTree, type MemoryTreeEntry } from "@/lib/tree";
import type { Memory, MemoryNode, MemoryStats, MemoryVersion } from "@/lib/types";
import { Markdown } from "@/components/markdown";
import { ThemeControl } from "@/components/theme-control";

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

function TreeEntryView({ entry, selected, onSelect, onDeleteDirectory, depth = 0 }: { entry: MemoryTreeEntry; selected: string; onSelect: (entry: MemoryTreeEntry) => void; onDeleteDirectory: (entry: MemoryTreeEntry) => void; depth?: number }) {
  const [open, setOpen] = useState(entry.path === "/memories" || entry.path === "/memories/profile");
  const isIndex = entry.path === "/memories/MEMORY.md";
  return (
    <div>
      <button className={`tree-row ${selected === entry.path ? "selected" : ""}`} style={{ paddingLeft: `${9 + depth * 16}px` }} onClick={() => entry.isDir ? setOpen((value) => !value) : onSelect(entry)}>
        {entry.isDir ? open ? <ChevronDown size={13} /> : <ChevronRight size={13} /> : <span style={{ width: 13 }} />}
        {entry.isDir ? open ? <FolderOpen size={14} color="#a8baff" /> : <Folder size={14} color="#8292b7" /> : <File size={14} color={isIndex ? "var(--accent)" : "#8c98aa"} />}
        <span className="tree-label">{entry.name}</span>{isIndex && <span className="tree-badge">INDEX</span>}{entry.isDir && entry.path !== "/memories" && <span className="icon-button" role="button" tabIndex={0} title="递归删除目录" onClick={(event) => { event.stopPropagation(); onDeleteDirectory(entry); }}><Trash2 size={12} /></span>}
      </button>
      {entry.isDir && open && entry.children.map((child) => <TreeEntryView key={child.path} entry={child} selected={selected} onSelect={onSelect} onDeleteDirectory={onDeleteDirectory} depth={depth + 1} />)}
    </div>
  );
}

function DiffView({ before, after }: { before: string; after: string }) {
  const chunks = diffLines(before, after);
  return <div className="diff-panel">{chunks.length === 0 ? <span className="diff-line">没有变化</span> : chunks.map((chunk, index) => <span className={`diff-line ${chunk.added ? "diff-added" : chunk.removed ? "diff-removed" : ""}`} key={`${index}-${chunk.value}`}>{chunk.added ? "+ " : chunk.removed ? "- " : "  "}{chunk.value}</span>)}</div>;
}

function MemoryStatsPanel({ stats, loading, onOpenFile, onDelete }: { stats: MemoryStats | null; loading: boolean; onOpenFile: (path: string) => void; onDelete: (path: string) => void }) {
  if (loading) return <div className="centered-empty stats-empty">读取记忆使用率…</div>;
  if (!stats) return <div className="centered-empty stats-empty">暂无记忆使用率数据</div>;

  const maxReads = Math.max(...stats.top.map((item) => item.reads), 1);
  const maxDaily = Math.max(...stats.daily.flatMap((item) => [item.reads, item.writes]), 1);
  const maxActor = Math.max(...stats.by_actor.flatMap((item) => [item.reads, item.writes]), 1);

  return <div className="memory-stats-panel">
    <div className="stats-heading"><div><div className="eyebrow">Memory analytics</div><h1>记忆使用率</h1><p>统计模型主动展开记忆文件的情况。索引摘要注入不计入读取次数。</p></div><span className="stats-period">近 30 天</span></div>
    <div className="memory-stat-cards"><div><span>记忆文件</span><strong>{stats.total_memories}</strong></div><div><span>主动读取</span><strong>{stats.total_reads}</strong></div><div><span>写入次数</span><strong>{stats.total_writes}</strong></div><div className={stats.missed_reads ? "stat-warning" : ""}><span>未命中读取</span><strong>{stats.missed_reads}</strong></div></div>
    <div className="memory-stats-grid">
      <section className="memory-stat-card"><div className="stats-card-heading"><div><span className="card-kicker">TOP EXPANDED</span><h2>最常展开</h2></div><Activity size={16} /></div>{stats.top.length ? <div className="stat-list">{stats.top.map((item) => <button className="stat-row stat-row-button" key={item.path} onClick={() => onOpenFile(item.path)}><span className="stat-row-label" title={item.path}>{item.path}</span><i><b style={{ width: `${Math.max(4, item.reads / maxReads * 100)}%` }} /></i><em>{item.reads}</em></button>)}</div> : <div className="stats-card-empty">还没有记忆读取记录。</div>}</section>
      <section className="memory-stat-card"><div className="stats-card-heading"><div><span className="card-kicker">DAILY ACTIVITY</span><h2>每日活动</h2></div><span className="legend"><i className="legend-read" />读 <i className="legend-write" />写</span></div>{stats.daily.length ? <div className="daily-bars">{stats.daily.map((item) => <div className="daily-bar-row" key={item.day}><span>{formatDate(item.day)}</span><i><b className="bar-read" style={{ width: `${Math.max(item.reads ? 5 : 0, item.reads / maxDaily * 100)}%` }} /><b className="bar-write" style={{ width: `${Math.max(item.writes ? 5 : 0, item.writes / maxDaily * 100)}%` }} /></i><em>{item.reads + item.writes}</em></div>)}</div> : <div className="stats-card-empty">近 30 天没有活动。</div>}</section>
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
  const [treeOpen, setTreeOpen] = useState(false);
  const [showStats, setShowStats] = useState(searchParams.get("view") === "stats");
  const [stats, setStats] = useState<MemoryStats | null>(null);
  const [loadingStats, setLoadingStats] = useState(false);
  const [restoring, setRestoring] = useState(false);

  const loadTree = useCallback(async () => {
    const result = await listMemoryNodes();
    setNodes(result);
    return result;
  }, []);

  useEffect(() => {
    void loadTree().then((result) => {
      const files = result.filter((node) => !node.is_dir);
      const requested = searchParams.get("path");
      const fallback = files.find((file) => file.path === "/memories/MEMORY.md")?.path ?? files[0]?.path ?? "";
      setSelectedPath((current) => requested && files.some((file) => file.path === requested) ? requested : files.some((file) => file.path === current) ? current : fallback);
    }).catch((cause: unknown) => setError(errorMessage(cause, "无法加载记忆树"))).finally(() => setLoadingTree(false));
  }, [loadTree, searchParams]);

  const loadFile = useCallback(async (path: string) => {
    setLoadingFile(true);
    setError("");
    setMessage("");
    try {
      const [file, history] = await Promise.all([getMemory(path), listMemoryVersions(path)]);
      setMemory(file);
      setContent(file.content);
      setVersions(history);
      setNewerId(history[0]?.id ?? null);
      setOlderId(history[1]?.id ?? history[0]?.id ?? null);
    } catch (cause) {
      setMemory(null);
      setError(errorMessage(cause, "无法打开记忆文件"));
    } finally {
      setLoadingFile(false);
    }
  }, []);

  const loadStats = useCallback(async () => {
    setLoadingStats(true);
    setError("");
    try {
      setStats(await getMemoryStats(30, 10));
    } catch (cause) {
      setError(errorMessage(cause, "无法加载记忆使用率"));
    } finally {
      setLoadingStats(false);
    }
  }, []);

  useEffect(() => { if (selectedPath) void loadFile(selectedPath); }, [loadFile, selectedPath]);
  useEffect(() => { if (showStats) void loadStats(); }, [loadStats, showStats]);

  const selectFile = (entry: MemoryTreeEntry) => {
    if (entry.isDir) return;
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
    const prompt = isDirectory ? `确定递归删除目录 ${path} 吗？目录下所有记忆文件都会被删除，且不可撤销；历史版本记录会保留。` : `确定删除 ${path} 吗？此操作不可撤销。`;
    if (!window.confirm(prompt)) return;
    try {
      await deleteMemory(path);
      const updated = await loadTree();
      if (memory?.path === path || isDirectory && memory?.path.startsWith(`${path}/`)) { setMemory(null); setContent(""); setVersions([]); }
      const next = updated.find((node) => !node.is_dir && node.path === "/memories/MEMORY.md") ?? updated.find((node) => !node.is_dir);
      if (next) { setSelectedPath(next.path); if (!showStats) router.push(`/memories?path=${encodeURIComponent(next.path)}`); } else setSelectedPath("");
      if (showStats) void loadStats();
      setMessage(isDirectory ? "已递归删除目录" : "已删除记忆");
    } catch (cause) { setError(errorMessage(cause, "删除失败")); }
  };

  const remove = async () => { if (memory) await removePath(memory.path); };

  const restore = async () => {
    const selected = versions.find((version) => version.id === olderId);
    if (!selected || !memory) return;
    if (!window.confirm(`将版本 ${formatTime(selected.created_at)} 的内容恢复到当前文件？`)) return;
    setRestoring(true);
    setError("");
    setMessage("");
    try {
      const restored = await restoreMemoryVersion(selected.id);
      setSelectedPath(restored.path);
      await Promise.all([loadTree(), loadFile(restored.path)]);
      setTab("preview");
      setMessage(`已恢复 ${formatTime(selected.created_at)} 的版本，并新增一条手动历史记录`);
    } catch (cause) {
      setError(errorMessage(cause, "恢复版本失败"));
    } finally {
      setRestoring(false);
    }
  };

  const tree = useMemo(() => buildMemoryTree(nodes), [nodes]);
  const older = versions.find((version) => version.id === olderId);
  const newer = versions.find((version) => version.id === newerId);
  const hasChanges = memory !== null && content !== memory.content;

  const toggleStats = () => {
    const next = !showStats;
    setShowStats(next);
    setMessage("");
    setError("");
    router.push(next ? "/memories?view=stats" : selectedPath ? `/memories?path=${encodeURIComponent(selectedPath)}` : "/memories");
  };

  const openStatsFile = (path: string) => {
    setShowStats(false);
    setSelectedPath(path);
    router.push(`/memories?path=${encodeURIComponent(path)}`);
  };

  return (
    <div className="memory-shell">
      <header className="memory-topbar"><Link className="brand brand-home" href="/" aria-label="返回主页"><div className="brand-mark">✦</div><div><div className="brand-title">个人 AI 助手</div><div className="brand-subtitle">Memory workspace</div></div></Link><div className="memory-topbar-tools"><nav className="memory-nav"><Link href="/"><MessageSquare size={14} />聊天</Link><span className="active"><FileText size={14} />记忆管理</span><Link href="/review"><CalendarDays size={14} />每日回顾</Link><Link href="/settings"><Settings2 size={14} />设置</Link></nav><ThemeControl /></div></header>
      <div className="memory-workspace">
      <aside className={`memory-tree-panel ${treeOpen ? "mobile-open" : ""}`}>
        <div className="memory-header"><h1>长期记忆</h1><p>模型会在聊天中读取和更新这些文件。这里保留每次变更的完整历史。</p></div>
        <div className="tree">{loadingTree ? <div className="centered-empty">加载中…</div> : tree.length ? tree.map((entry) => <TreeEntryView key={entry.path} entry={entry} selected={selectedPath} onSelect={selectFile} onDeleteDirectory={(entry) => void removePath(entry.path, true)} />) : <div className="centered-empty">还没有记忆文件</div>}</div>
      </aside>
      {treeOpen && <button className="sidebar-backdrop" aria-label="关闭记忆树" onClick={() => setTreeOpen(false)} />}
      <main className="memory-editor-panel">
        <header className="editor-topbar"><div className="path-title"><button className="icon-button mobile-menu" aria-label="打开记忆树" onClick={() => setTreeOpen(true)}><Menu size={19} /></button>{showStats ? <><BarChart3 size={16} color="var(--accent)" /><strong>记忆使用率</strong></> : <><FileText size={16} color="var(--accent)" />{memory ? <><strong>{memory.path}</strong>{hasChanges && <span className="unsaved-dot">未保存</span>}</> : <span className="topbar-meta">选择一个文件</span>}</>}</div><div className="editor-actions"><button className="ghost-button stats-toggle" onClick={toggleStats}><BarChart3 size={13} />{showStats ? "返回编辑" : "使用分析"}</button>{!showStats && memory && <><button className="danger-button" onClick={() => void remove()}><Trash2 size={13} />删除</button><button className="primary-button" disabled={!hasChanges || saving} onClick={() => void save()}><Save size={13} />{saving ? "保存中…" : "保存"}</button></>}</div></header>
        {showStats ? <MemoryStatsPanel stats={stats} loading={loadingStats} onOpenFile={openStatsFile} onDelete={(path) => void removePath(path)} /> : !memory ? <div className="centered-empty">{loadingFile ? "打开文件中…" : error || "从左侧选择一个文件"}</div> : <>
          <div className="editor-tabs"><button className={`editor-tab ${tab === "edit" ? "active" : ""}`} onClick={() => setTab("edit")}>编辑</button><button className={`editor-tab ${tab === "preview" ? "active" : ""}`} onClick={() => setTab("preview")}>预览</button></div>
          <div className="editor-area">{loadingFile ? <div className="centered-empty">打开文件中…</div> : tab === "edit" ? <textarea className="editor-textarea" value={content} onChange={(event) => setContent(event.target.value)} spellCheck={false} /> : <div className="preview assistant-content"><Markdown>{content}</Markdown></div>}</div>
          <section className="versions-panel"><div className="versions-head"><span><History size={14} style={{ verticalAlign: "-3px", marginRight: 5 }} />版本历史（{versions.length}）</span><div className="version-selectors">{versions.length > 0 && <><select aria-label="较旧版本" value={olderId ?? ""} onChange={(event) => setOlderId(Number(event.target.value))}>{versions.map((version) => <option key={version.id} value={version.id}>{formatTime(version.created_at)} · {actorLabel(version.actor)}</option>)}</select><select aria-label="较新版本" value={newerId ?? ""} onChange={(event) => setNewerId(Number(event.target.value))}>{versions.map((version) => <option key={version.id} value={version.id}>{formatTime(version.created_at)} · {actorLabel(version.actor)}</option>)}</select><button className="ghost-button" onClick={() => void restore()} disabled={!older || restoring}><RotateCcw size={12} />{restoring ? "恢复中…" : "恢复"}</button></>}</div></div>
            {older && newer && older.id !== newer.id && <DiffView before={older.content} after={newer.content} />}
            <div className="version-list">{versions.length ? versions.map((version) => <div className="version-row" key={version.id}><span className={`actor-badge actor-${version.actor}`}>{actorLabel(version.actor)}</span><button onClick={() => { setOlderId(version.id); setTab("preview"); }}>{version.operation} · {formatTime(version.created_at)}</button></div>) : <span className="topbar-meta">暂无版本记录</span>}</div>
          </section>
          {(message || error) && <div className={`editor-notice ${error ? "danger-text" : ""}`}>{error || message}</div>}
        </>}
      </main>
      </div>
    </div>
  );
}
