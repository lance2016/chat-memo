"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronRight, Download, FileText, Package, RefreshCw, Trash2, TriangleAlert, Upload } from "lucide-react";
import { deleteSkill, errorMessage, getSkill, installSkill, listSkills, setSkillEnabled, uploadSkill } from "@/lib/api";
import type { Skill, SkillCatalog, SkillDetail, SkillInstallResult } from "@/lib/types";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { Markdown } from "@/components/markdown";

function formatSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
}

function SkillRow({ skill, busy, onToggle, onRemove, onInspect }: {
  skill: Skill;
  busy: boolean;
  onToggle: (enabled: boolean) => void;
  onRemove: () => void;
  onInspect: () => void;
}) {
  return <div className={`skill-row ${skill.enabled && !skill.error ? "active" : ""} ${skill.error ? "broken" : ""}`}>
    <div className="skill-row-main">
      <div className="skill-row-title">
        <code>{skill.name}</code>
        {skill.version && <span className="skill-version">v{skill.version}</span>}
        {skill.error && <span className="skill-broken-flag"><TriangleAlert size={12} />无法加载</span>}
      </div>
      <p>{skill.error || skill.description}</p>
      {skill.warning && !skill.error && <p className="skill-row-warning"><TriangleAlert size={11} />{skill.warning}</p>}
      <div className="skill-row-meta">
        <span>{skill.source}</span>
        <span>{skill.files.length} 个附带文件 · {formatSize(skill.size_bytes)}</span>
        {skill.license && <span>{skill.license}</span>}
      </div>
    </div>
    <div className="skill-row-actions">
      <button className="ghost-button" type="button" onClick={onInspect} disabled={busy}><FileText size={13} />查看</button>
      <label className="settings-toggle-inline" title={skill.error ? "解析失败的技能不会进对话" : "关掉后模型看不到这个技能"}>
        <input type="checkbox" checked={skill.enabled} disabled={busy || Boolean(skill.error)} onChange={(event) => onToggle(event.target.checked)} />
        <span className="toggle-track" aria-hidden="true"><span /></span>
      </label>
      <button className="icon-button danger-hover" type="button" aria-label={`删除技能 ${skill.name}`} onClick={onRemove} disabled={busy}><Trash2 size={14} /></button>
    </div>
  </div>;
}

export function SkillsPanel() {
  const [catalog, setCatalog] = useState<SkillCatalog | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [skipped, setSkipped] = useState<SkillInstallResult["skipped"]>([]);
  const [source, setSource] = useState("");
  const [installing, setInstalling] = useState(false);
  const [busyName, setBusyName] = useState("");
  const [removeTarget, setRemoveTarget] = useState<Skill | null>(null);
  const [detail, setDetail] = useState<SkillDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

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
    <div className="skills-overview">
      <div><strong>{catalog?.total ?? 0}</strong><span>已安装</span></div>
      <div><strong>{catalog?.active ?? 0}</strong><span>对话中生效</span></div>
      <div><strong className={catalog?.enabled ? "value-success" : ""}>{catalog ? catalog.enabled ? "已开启" : "已关闭" : "—"}</strong><span>技能总开关</span></div>
    </div>

    <form className="skill-install-form" onSubmit={(event) => { event.preventDefault(); if (source.trim()) void runInstall(() => installSkill(source.trim())); }}>
      <input
        type="text"
        value={source}
        placeholder="anthropics/skills/skills/pdf 或一个 .zip 地址"
        onChange={(event) => setSource(event.target.value)}
        disabled={installing}
      />
      <button className="primary-button" type="submit" disabled={installing || !source.trim()}><Download size={13} />{installing ? "安装中…" : "安装"}</button>
      <button className="ghost-button" type="button" onClick={() => fileRef.current?.click()} disabled={installing}><Upload size={13} />上传 zip</button>
      <input
        ref={fileRef}
        type="file"
        accept=".zip"
        hidden
        onChange={(event) => {
          const file = event.target.files?.[0];
          event.target.value = "";
          if (file) void runInstall(() => uploadSkill(file));
        }}
      />
    </form>
    <p className="skill-install-hint">
      支持 <code>owner/repo</code>、<code>owner/repo/子目录@分支</code>、GitHub 网页地址和 zip 直链。
      技能是第三方写的操作说明，装之前先看清楚内容 —— 模型会照着做。
    </p>

    {error && <div className="settings-error"><TriangleAlert size={14} /><span>{error}</span></div>}
    {message && <div className="settings-success">{message}</div>}
    {skipped.length > 0 && <div className="skill-skipped-list">
      <strong><TriangleAlert size={13} />跳过了 {skipped.length} 个</strong>
      {skipped.map((item) => <div key={item.path}><code>{item.path}</code><span>{item.reason}</span></div>)}
    </div>}

    {loading && !catalog ? <div className="settings-loading"><RefreshCw size={15} className="spin" />读取技能…</div>
      : catalog?.skills.length ? <div className="skill-list">
        {catalog.skills.map((skill) => <SkillRow
          key={skill.name}
          skill={skill}
          busy={busyName === skill.name}
          onToggle={(enabled) => void toggle(skill, enabled)}
          onRemove={() => setRemoveTarget(skill)}
          onInspect={() => void inspect(skill)}
        />)}
      </div> : <div className="debug-empty">
        <Package size={16} />
        <strong>还没有技能</strong>
        <span>装一个试试，或者把技能目录直接拷进 {catalog?.root || "技能目录"}。</span>
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

    {(detail || detailLoading) && <div className="debug-dialog-backdrop" role="presentation" onClick={() => setDetail(null)}>
      <section className="debug-dialog skill-detail-dialog" role="dialog" aria-modal="true" onClick={(event) => event.stopPropagation()}>
        <header className="debug-dialog-header">
          <div><span className="card-kicker">SKILL.md</span><h2>{detail?.name ?? "读取中…"}</h2></div>
          <button className="icon-button" type="button" aria-label="关闭技能详情" onClick={() => setDetail(null)}><ChevronRight size={16} /></button>
        </header>
        {detailLoading ? <div className="settings-loading"><RefreshCw size={15} className="spin" />读取技能正文…</div> : detail && <div className="skill-detail-body">
          <p className="skill-detail-description">{detail.description}</p>
          {detail.files.length > 0 && <div className="skill-detail-files"><strong>附带文件</strong><ul>{detail.files.map((file) => <li key={file}><code>{file}</code></li>)}</ul></div>}
          <Markdown>{detail.body || detail.error || "（正文是空的）"}</Markdown>
        </div>}
      </section>
    </div>}
  </div>;
}
