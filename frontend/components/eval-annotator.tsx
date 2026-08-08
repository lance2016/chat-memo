"use client";

import { useCallback, useEffect, useState } from "react";
import { Check, Plus, RefreshCw, Trash2, TriangleAlert, X } from "lucide-react";
import { errorMessage, getEvalCase, saveEvalExpect } from "@/lib/api";
import type { EvalCaseDetail, EvalExpect } from "@/lib/types";

/** 标注一条样本。
 *
 * 这是「等标注」真正卡住的地方 —— 在此之前只能手改 JSON。
 *
 * 三条标注原则直接做进了界面，而不是写在文档里等人记得：
 * 1. **标事实点不标文本** —— 一行一条，超长会被后端 `validate()` 挡下
 * 2. **必须有反例** —— `no_op` 是个显眼的开关，不是某个字段里的 true
 * 3. **快照是冻结的** —— 对话和记忆只读，动了就不再是同一条样本
 */

function StringList({ label, hint, items, onChange, placeholder }: {
  label: string;
  hint: string;
  items: string[];
  onChange: (next: string[]) => void;
  placeholder: string;
}) {
  return <div className="annot-field">
    <div className="annot-field-head"><strong>{label}</strong><small>{hint}</small></div>
    <div className="annot-list">
      {items.map((item, index) => <div className="annot-row" key={index}>
        <input
          value={item}
          placeholder={placeholder}
          onChange={(event) => onChange(items.map((v, i) => (i === index ? event.target.value : v)))}
        />
        <button className="icon-button" type="button" aria-label={`删除第 ${index + 1} 条`} onClick={() => onChange(items.filter((_, i) => i !== index))}>
          <X size={13} />
        </button>
      </div>)}
      <button className="ghost-button annot-add" type="button" onClick={() => onChange([...items, ""])}>
        <Plus size={12} />添加
      </button>
    </div>
  </div>;
}

export function EvalAnnotator({ caseId, onClose, onSaved }: {
  caseId: string;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [detail, setDetail] = useState<EvalCaseDetail | null>(null);
  const [expect, setExpect] = useState<EvalExpect | null>(null);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  const load = useCallback(async () => {
    try {
      const data = await getEvalCase(caseId);
      setDetail(data);
      setExpect(data.expect);
      setError("");
    } catch (cause) {
      setError(errorMessage(cause, "读不到这条样本"));
    }
  }, [caseId]);

  useEffect(() => { void load(); }, [load]);

  const save = async () => {
    if (!expect) return;
    setSaving(true);
    try {
      const data = await saveEvalExpect(caseId, expect);
      setDetail(data);
      setExpect(data.expect);
      setSaved(true);
      window.setTimeout(() => setSaved(false), 1600);
      onSaved();
      setError("");
    } catch (cause) {
      setError(errorMessage(cause, "保存失败"));
    } finally {
      setSaving(false);
    }
  };

  if (error && !detail) return <div className="annot-panel"><div className="obs-detail warn"><TriangleAlert size={13} />{error}</div></div>;
  if (!detail || !expect) return <div className="annot-panel"><div className="settings-loading"><RefreshCw size={14} className="spin" />读取样本…</div></div>;

  const patch = (changes: Partial<EvalExpect>) => setExpect({ ...expect, ...changes });

  return <div className="annot-panel">
    <div className="annot-head">
      <div>
        <strong>{detail.id}</strong>
        <small>{detail.conversations.length} 个会话 · 整理前 {Object.keys(detail.memory_before).length} 个记忆文件</small>
      </div>
      <div className="annot-head-actions">
        <button className="primary-button" type="button" onClick={() => void save()} disabled={saving}>
          {saved ? <Check size={13} /> : null}{saving ? "保存中…" : saved ? "已保存" : "保存标注"}
        </button>
        <button className="icon-button" type="button" aria-label="关闭标注" onClick={onClose}><X size={15} /></button>
      </div>
    </div>

    {detail.note && <p className="annot-note">{detail.note}</p>}
    {error && <div className="obs-detail warn"><TriangleAlert size={13} />{error}</div>}
    {detail.problems.length > 0 && <div className="obs-detail warn">
      <TriangleAlert size={13} />
      <span>{detail.problems.join("；")}</span>
    </div>}

    <label className="annot-noop">
      <input type="checkbox" checked={expect.no_op} onChange={(event) => patch({ no_op: event.target.checked })} />
      <span><strong>这天不该写任何记忆</strong><small>反例。只测正例的数据集会奖励一个疯狂写记忆的模型 —— 至少留两三条</small></span>
    </label>

    {!expect.no_op && <>
      <StringList
        label="该记住的事实"
        hint="一行一条，写事实点不是整段摘要（超长会被拦下）"
        items={expect.facts}
        onChange={(facts) => patch({ facts })}
        placeholder="例：用户对花生过敏"
      />

      <div className="annot-field">
        <div className="annot-field-head"><strong>该改掉的旧记录</strong><small>stale 要填整理前记忆里那段过期原文，否则判不了「新旧并存」</small></div>
        <div className="annot-list">
          {expect.corrections.map((item, index) => <div className="annot-correction" key={index}>
            <input
              value={item.stale}
              placeholder="旧说法（记忆里的原文片段）"
              onChange={(event) => patch({ corrections: expect.corrections.map((c, i) => (i === index ? { ...c, stale: event.target.value } : c)) })}
            />
            <input
              value={item.becomes}
              placeholder="应改成（可留空）"
              onChange={(event) => patch({ corrections: expect.corrections.map((c, i) => (i === index ? { ...c, becomes: event.target.value } : c)) })}
            />
            <button className="icon-button" type="button" aria-label={`删除第 ${index + 1} 条修正`} onClick={() => patch({ corrections: expect.corrections.filter((_, i) => i !== index) })}>
              <Trash2 size={13} />
            </button>
          </div>)}
          <button className="ghost-button annot-add" type="button" onClick={() => patch({ corrections: [...expect.corrections, { stale: "", becomes: "" }] })}>
            <Plus size={12} />添加
          </button>
        </div>
      </div>
    </>}

    <StringList
      label="明确不该进记忆的"
      hint="一次性问答、技术细节、不该被倒推出来的东西"
      items={expect.forbidden}
      onChange={(forbidden) => patch({ forbidden })}
      placeholder="例：不要从「他问了 nginx」推出「他在用 nginx」"
    />

    <details className="annot-source">
      <summary><span><strong>这天的对话</strong><small>只读 —— 冻结的输入，改了就不是同一条样本</small></span></summary>
      {detail.conversations.map((conversation, index) => <div className="annot-conversation" key={index}>
        <strong>{conversation.title}</strong>
        {conversation.messages.map((message, messageIndex) => <p key={messageIndex}>
          <em>{message.role === "user" ? "用户" : "助手"}</em>{message.text}
        </p>)}
      </div>)}
    </details>

    <details className="annot-source">
      <summary><span><strong>整理前的记忆</strong><small>只读 —— 判断「该改掉什么」的依据</small></span></summary>
      {Object.entries(detail.memory_before).map(([path, content]) => <div className="annot-memory" key={path}>
        <code>{path}</code>
        <pre>{content}</pre>
      </div>)}
    </details>
  </div>;
}
