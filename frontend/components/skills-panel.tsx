"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronRight, Download, Package, Plus, RefreshCw, Trash2, TriangleAlert, Upload, X } from "lucide-react";
import { deleteSkill, errorMessage, getSkill, installSkill, listSkills, setSkillEnabled, uploadSkill } from "@/lib/api";
import type { Skill, SkillCatalog, SkillDetail, SkillInstallResult } from "@/lib/types";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { Markdown } from "@/components/markdown";

function formatSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
}

function SkillRow({ skill, busy, onToggle, onInspect }: {
  skill: Skill;
  busy: boolean;
  onToggle: (enabled: boolean) => void;
  onInspect: () => void;
}) {
  return <div className={`skill-row ${skill.enabled && !skill.error ? "active" : ""} ${skill.error ? "broken" : ""}`}>
    <button className="skill-row-open" type="button" aria-label={`查看技能 ${skill.name}`} onClick={onInspect} disabled={busy}>
      <span className="skill-row-icon" aria-hidden="true"><Package size={16} /></span>
      <span className="skill-row-main">
        <span className="skill-row-title">
          <code>{skill.name}</code>
          {skill.version && <span className="skill-version">v{skill.version}</span>}
          {skill.error && <span className="skill-broken-flag"><TriangleAlert size={12} />无法加载</span>}
        </span>
        <span className="skill-row-description">{skill.error || skill.description}</span>
        {skill.warning && !skill.error && <span className="skill-row-warning"><TriangleAlert size={11} />{skill.warning}</span>}
        <span className="skill-row-meta">
          <span>{skill.source}</span>
          <span>{skill.files.length} 个附带文件 · {formatSize(skill.size_bytes)}</span>
          {skill.license && <span>{skill.license}</span>}
        </span>
      </span>
      <ChevronRight className="skill-row-chevron" size={16} aria-hidden="true" />
    </button>
    <label className="settings-toggle-inline" title={skill.error ? "解析失败的技能不会进对话" : "关掉后模型看不到这个技能"}>
      <input type="checkbox" aria-label={`${skill.enabled ? "停用" : "启用"}技能 ${skill.name}`} checked={skill.enabled} disabled={busy || Boolean(skill.error)} onChange={(event) => onToggle(event.target.checked)} />
      <span className="toggle-track" aria-hidden="true"><span /></span>
    </label>
  </div>;
}

function closeDetailState(setDetail: (value: SkillDetail | null) => void, setDetailTarget: (value: Skill | null) => void) {
  setDetail(null);
  setDetailTarget(null);
}

export function SkillsPanel() {
  const [catalog, setCatalog] = useState<SkillCatalog | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [skipped, setSkipped] = useState<SkillInstallResult["skipped"]>([]);
  const [source, setSource] = useState("");
  const [installOpen, setInstallOpen] = useState(false);
  const [installing, setInstalling] = useState(false);
  const [busyName, setBusyName] = useState("");
  const [removeTarget, setRemoveTarget] = useState<Skill | null>(null);
  const [detail, setDetail] = useState<SkillDetail | null>(null);
  const [detailTarget, setDetailTarget] = useState<Skill | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!installOpen && !detail) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (installOpen && !installing) setInstallOpen(false);
      else if (detail && !detailLoading) closeDetailState(setDetail, setDetailTarget);
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [detail, detailLoading, installOpen, installing]);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setCatalog(await listSkills());
    } catch (cause) {
      setError(errorMessage(cause, "读取技能列表失败"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const runInstall = async (action: () => Promise<SkillInstallResult>) => {
    setInstalling(true);
    setError("");
    setMessage("");
    setSkipped([]);
    try {
      const result = await action();
      const names = result.installed.map((item) => `${item.name}${item.replaced ? "（覆盖）" : ""}`);
      setMessage(names.length ? `装好了：${names.join("、")}` : "这个来源里没有技能");
      // 跳过的单独显示：混进上面那句里会被一长串技能名淹掉
      setSkipped(result.skipped ?? []);
      setSource("");
      await load();
      setInstallOpen(false);
    } catch (cause) {
      setError(errorMessage(cause, "安装失败"));
    } finally {
      setInstalling(false);
    }
  };

  const toggle = async (skill: Skill, enabled: boolean) => {
    setBusyName(skill.name);
    setError("");
    try {
      await setSkillEnabled(skill.name, enabled);
      await load();
    } catch (cause) {
      setError(errorMessage(cause, "切换失败"));
    } finally {
      setBusyName("");
    }
  };

  const remove = async () => {
    if (!removeTarget) return;
    setBusyName(removeTarget.name);
    try {
      await deleteSkill(removeTarget.name);
      setMessage(`已删除 ${removeTarget.name}`);
      await load();
    } catch (cause) {
      setError(errorMessage(cause, "删除失败"));
    } finally {
      setBusyName("");
      setRemoveTarget(null);
    }
  };

  const inspect = async (skill: Skill) => {
    setDetailTarget(skill);
    setDetailLoading(true);
    setError("");
    try {
      setDetail(await getSkill(skill.name));
    } catch (cause) {
      setError(errorMessage(cause, "读取技能失败"));
    } finally {
      setDetailLoading(false);
    }
  };

  return <div className="skills-panel">
    <section className="skills-group skills-install-section" aria-labelledby="skills-manage-heading">
      <h3 className="settings-group-label" id="skills-manage-heading">管理</h3>
      <div className="skills-group-surface">
        <button className="skill-add-row" type="button" onClick={() => { setError(""); setInstallOpen(true); }}>
          <span className="skill-add-icon" aria-hidden="true"><Plus size={18} /></span>
          <span><strong>添加技能</strong><small>从 GitHub 地址或 ZIP 文件安装</small></span>
          <ChevronRight size={16} aria-hidden="true" />
        </button>
      </div>
      {error && !installOpen && <div className="settings-error"><TriangleAlert size={14} /><span>{error}</span></div>}
      {message && <div className="settings-success">{message}</div>}
      {skipped.length > 0 && <div className="skill-skipped-list">
        <strong><TriangleAlert size={13} />跳过了 {skipped.length} 个</strong>
        {skipped.map((item) => <div key={item.path}><code>{item.path}</code><span>{item.reason}</span></div>)}
      </div>}
    </section>

    <section className="skills-group skills-library-section" aria-labelledby="skills-library-heading">
      <header className="skills-library-heading">
        <h3 className="settings-group-label" id="skills-library-heading">已安装</h3>
        <span>{catalog ? `${catalog.total} 个 · ${catalog.active} 个已启用` : "正在读取"}</span>
      </header>
      <div className="skills-group-surface">
        {loading && !catalog ? <div className="settings-loading"><RefreshCw size={15} className="spin" />读取技能…</div>
          : catalog?.skills.length ? <div className="skill-list">
          {catalog.skills.map((skill) => <SkillRow
            key={skill.name}
            skill={skill}
            busy={busyName === skill.name}
            onToggle={(enabled) => void toggle(skill, enabled)}
            onInspect={() => void inspect(skill)}
          />)}
          </div> : <div className="debug-empty">
          <Package size={18} />
          <strong>还没有技能</strong>
          <span>安装一个技能，或把技能目录拷进 {catalog?.root || "技能目录"}。</span>
          </div>}
      </div>
    </section>

    {installOpen && <div className="debug-dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !installing) setInstallOpen(false); }}>
      <section className="debug-dialog skill-install-dialog" role="dialog" aria-modal="true" aria-labelledby="skill-install-title">
        <header className="debug-dialog-header">
          <div><h2 id="skill-install-title">添加技能</h2><p>粘贴可信来源，或选择本地 ZIP 文件。</p></div>
          <button className="icon-button" type="button" aria-label="关闭添加技能" onClick={() => setInstallOpen(false)} disabled={installing}><X size={16} /></button>
        </header>
        <form className="skill-install-form" onSubmit={(event) => { event.preventDefault(); if (source.trim()) void runInstall(() => installSkill(source.trim())); }}>
          <label htmlFor="skill-source">GitHub 或 ZIP 地址</label>
          <div className="skill-install-source-row">
            <input id="skill-source" type="text" value={source} aria-label="技能来源" placeholder="github.com/owner/repo/tree/main/skills/name" autoFocus onChange={(event) => setSource(event.target.value)} disabled={installing} />
            <button className="primary-button" type="submit" disabled={installing || !source.trim()}><Download size={14} />{installing ? "安装中…" : "安装"}</button>
          </div>
          <div className="skill-install-divider"><span>或</span></div>
          <button className="skill-upload-button" type="button" onClick={() => fileRef.current?.click()} disabled={installing}><Upload size={17} /><span><strong>选择 ZIP 文件</strong><small>从这台设备上传</small></span><ChevronRight size={16} /></button>
          <input ref={fileRef} type="file" accept=".zip" hidden onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ""; if (file) void runInstall(() => uploadSkill(file)); }} />
        </form>
        {error && <div className="settings-error"><TriangleAlert size={14} /><span>{error}</span></div>}
        <p className="skill-install-hint">技能会向模型提供任务说明。仅安装你信任的来源。</p>
      </section>
    </div>}

    <ConfirmDialog
      open={Boolean(removeTarget)}
      title={`删除技能 ${removeTarget?.name ?? ""}？`}
      description="技能目录会从磁盘上删除，这一步不可撤销。之后可以从原来的来源重新安装。"
      confirmLabel="删除"
      busy={busyName === removeTarget?.name}
      onCancel={() => setRemoveTarget(null)}
      onConfirm={() => void remove()}
    />

    {(detail || detailLoading) && <div className="debug-dialog-backdrop" role="presentation" onClick={() => closeDetailState(setDetail, setDetailTarget)}>
      <section className="debug-dialog skill-detail-dialog" role="dialog" aria-modal="true" onClick={(event) => event.stopPropagation()}>
        <header className="debug-dialog-header">
          <div><span className="card-kicker">SKILL.md</span><h2>{detail?.name ?? "读取中…"}</h2></div>
          <button className="icon-button" type="button" aria-label="关闭技能详情" onClick={() => closeDetailState(setDetail, setDetailTarget)}><X size={16} /></button>
        </header>
        {detailLoading ? <div className="settings-loading"><RefreshCw size={15} className="spin" />读取技能正文…</div> : detail && <div className="skill-detail-body">
          <p className="skill-detail-description">{detail.description}</p>
          {detail.files.length > 0 && <div className="skill-detail-files"><strong>附带文件</strong><ul>{detail.files.map((file) => <li key={file}><code>{file}</code></li>)}</ul></div>}
          <Markdown>{detail.body || detail.error || "（正文是空的）"}</Markdown>
        </div>}
        {detailTarget && <footer className="skill-detail-footer"><button className="danger-button" type="button" aria-label={`删除技能 ${detailTarget.name}`} onClick={() => { setRemoveTarget(detailTarget); closeDetailState(setDetail, setDetailTarget); }}><Trash2 size={14} />删除技能</button></footer>}
      </section>
    </div>}
  </div>;
}
