"use client";

import { type KeyboardEvent, useEffect, useId, useRef } from "react";
import { Check, X } from "lucide-react";
import { useI18n } from "@/components/i18n-provider";

export function InputDialog({ open, title, description, value, placeholder, busy = false, onChange, onConfirm, onCancel }: {
  open: boolean;
  title: string;
  description: string;
  value: string;
  placeholder?: string;
  busy?: boolean;
  onChange: (value: string) => void;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const { t } = useI18n();
  const titleId = useId();
  const descriptionId = useId();
  const dialogRef = useRef<HTMLElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (!open) return;
    returnFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.requestAnimationFrame(() => { inputRef.current?.focus(); inputRef.current?.select(); });
    return () => {
      document.body.style.overflow = previousOverflow;
      window.requestAnimationFrame(() => returnFocusRef.current?.focus());
    };
  }, [open]);
  if (!open) return null;
  const onKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key === "Escape" && !busy) { event.preventDefault(); onCancel(); }
    if (event.key === "Enter" && value.trim() && !busy) { event.preventDefault(); onConfirm(); }
    if (event.key !== "Tab" || !dialogRef.current) return;
    const focusable = Array.from(dialogRef.current.querySelectorAll<HTMLElement>("input:not([disabled]), button:not([disabled])"));
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
  return <div className="input-dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) onCancel(); }}>
    <section ref={dialogRef} className="input-dialog" role="dialog" aria-modal="true" aria-labelledby={titleId} aria-describedby={descriptionId} onKeyDown={onKeyDown}>
      <header><div><span className="card-kicker">CONVERSATION</span><h2 id={titleId}>{title}</h2></div><button className="icon-button" type="button" aria-label={t("common.close")} onClick={onCancel} disabled={busy}><X size={16} /></button></header>
      <div className="input-dialog-body"><p id={descriptionId}>{description}</p><label><span>{t("common.title")}</span><input ref={inputRef} value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} disabled={busy} /></label></div>
      <footer><button className="ghost-button" type="button" onClick={onCancel} disabled={busy}>{t("common.cancel")}</button><button className="primary-button" type="button" onClick={onConfirm} disabled={!value.trim() || busy}><Check size={14} />{t("common.saveTitle")}</button></footer>
    </section>
  </div>;
}
