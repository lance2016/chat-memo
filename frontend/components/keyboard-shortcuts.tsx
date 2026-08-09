"use client";

import { type KeyboardEvent as ReactKeyboardEvent, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Command, X } from "lucide-react";
import { useI18n } from "@/components/i18n-provider";
import { confirmAppNavigation } from "@/lib/navigation-guard";
import type { TranslationKey } from "@/lib/i18n";

/** ⌘1…⌘5 的目标，顺序和侧栏导航一致。 */
const pageRoutes = ["/", "/memories", "/review", "/timeline", "/settings"];

type Shortcut = { keys: string[]; label: TranslationKey; values?: Record<string, string | number> };

const globalShortcuts: Shortcut[] = [
  { keys: ["⌘", "K"], label: "shortcuts.search" },
  { keys: ["⌘", "N"], label: "shortcuts.newChat" },
  { keys: ["⌘", "/"], label: "shortcuts.help" },
  { keys: ["⌘", "1…5"], label: "shortcuts.navigate", values: { index: "1–5" } },
  { keys: ["Esc"], label: "shortcuts.close" },
];

const chatShortcuts: Shortcut[] = [
  { keys: ["↩"], label: "shortcuts.send" },
  { keys: ["⇧", "↩"], label: "shortcuts.newline" },
  { keys: ["Esc"], label: "shortcuts.stop" },
];

/**
 * 全局快捷键的唯一注册点。之前整个应用只有 global-search 里的 ⌘K 一个键位，
 * 其余全靠鼠标 —— 在 macOS 上这是最直观的「不像原生应用」的地方。
 *
 * 只处理带 ⌘/Ctrl 的组合键：不带修饰键的单键会和输入框抢按键。
 */
export function KeyboardShortcuts() {
  const { t } = useI18n();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const dialogRef = useRef<HTMLElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (!event.metaKey && !event.ctrlKey) return;
      if (event.altKey) return;

      if (event.key === "/" || (event.key === "?" && event.shiftKey)) {
        event.preventDefault();
        returnFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
        setOpen((current) => !current);
        return;
      }

      // 有未保存改动的页面会否决跳转（设置页、记忆编辑器），所以这里和点击
      // 侧栏走同一道 confirmAppNavigation，不能直接 router.push。
      if (event.key.toLowerCase() === "n") {
        event.preventDefault();
        if (confirmAppNavigation()) router.push("/");
        return;
      }

      const index = Number(event.key);
      if (Number.isInteger(index) && index >= 1 && index <= pageRoutes.length) {
        event.preventDefault();
        if (confirmAppNavigation()) router.push(pageRoutes[index - 1]);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [router]);

  useEffect(() => {
    if (!open) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.requestAnimationFrame(() => closeRef.current?.focus());
    return () => {
      document.body.style.overflow = previousOverflow;
      window.requestAnimationFrame(() => returnFocusRef.current?.focus());
    };
  }, [open]);

  if (!open) return null;

  const onDialogKeyDown = (event: ReactKeyboardEvent<HTMLElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      setOpen(false);
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

  const renderGroup = (title: TranslationKey, shortcuts: Shortcut[]) => <div className="shortcuts-group" key={title}>
    <div className="shortcuts-group-title">{t(title)}</div>
    {shortcuts.map((shortcut) => <div className="shortcuts-row" key={`${title}-${shortcut.label}`}>
      <span>{t(shortcut.label, shortcut.values)}</span>
      <span className="shortcuts-keys">{shortcut.keys.map((key) => <kbd key={key}>{key}</kbd>)}</span>
    </div>)}
  </div>;

  return <div className="shortcuts-overlay" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setOpen(false); }}>
    <section ref={dialogRef} className="shortcuts-dialog" role="dialog" aria-modal="true" aria-label={t("shortcuts.title")} onKeyDown={onDialogKeyDown}>
      <header className="shortcuts-header">
        <span className="shortcuts-header-icon" aria-hidden="true"><Command size={17} /></span>
        <div><h2>{t("shortcuts.title")}</h2><p>{t("shortcuts.description")}</p></div>
        <button ref={closeRef} className="icon-button" type="button" aria-label={t("common.close")} onClick={() => setOpen(false)}><X size={16} /></button>
      </header>
      <div className="shortcuts-body">
        {renderGroup("shortcuts.group.global", globalShortcuts)}
        {renderGroup("shortcuts.group.chat", chatShortcuts)}
      </div>
    </section>
  </div>;
}
