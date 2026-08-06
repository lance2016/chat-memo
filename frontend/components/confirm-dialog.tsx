"use client";

import { type KeyboardEvent, useEffect, useId, useRef } from "react";
import { LoaderCircle, TriangleAlert, X } from "lucide-react";

export function ConfirmDialog({
  open,
  title,
  description,
  subject,
  warning,
  confirmLabel,
  busy = false,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  description: string;
  subject?: string;
  warning?: string;
  confirmLabel: string;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const titleId = useId();
  const descriptionId = useId();
  const dialogRef = useRef<HTMLElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;
    returnFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.requestAnimationFrame(() => cancelRef.current?.focus());
    return () => {
      document.body.style.overflow = previousOverflow;
      window.requestAnimationFrame(() => returnFocusRef.current?.focus());
    };
  }, [open]);

  if (!open) return null;

  const handleKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key === "Escape" && !busy) {
      event.preventDefault();
      onCancel();
      return;
    }
    if (event.key !== "Tab" || !dialogRef.current) return;
    const focusable = Array.from(dialogRef.current.querySelectorAll<HTMLElement>("button:not([disabled])"));
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  return <div className="confirm-dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) onCancel(); }}>
    <section ref={dialogRef} className="confirm-dialog" role="alertdialog" aria-modal="true" aria-labelledby={titleId} aria-describedby={descriptionId} onKeyDown={handleKeyDown}>
      <header className="confirm-dialog-header">
        <span className="confirm-dialog-icon" aria-hidden="true"><TriangleAlert size={19} /></span>
        <div><span className="card-kicker">DANGER ZONE</span><h2 id={titleId}>{title}</h2></div>
        <button className="icon-button" type="button" aria-label="关闭确认窗口" onClick={onCancel} disabled={busy}><X size={16} /></button>
      </header>
      <div className="confirm-dialog-body">
        <p id={descriptionId}>{description}</p>
        {subject && <div className="confirm-dialog-subject" title={subject}>{subject}</div>}
        {warning && <div className="confirm-dialog-warning"><TriangleAlert size={13} /><span>{warning}</span></div>}
      </div>
      <footer className="confirm-dialog-actions">
        <button ref={cancelRef} className="ghost-button" type="button" onClick={onCancel} disabled={busy}>取消</button>
        <button className="danger-button confirm-danger-button" type="button" onClick={onConfirm} disabled={busy}>{busy ? <><LoaderCircle size={14} className="spin" />处理中…</> : confirmLabel}</button>
      </footer>
    </section>
  </div>;
}
