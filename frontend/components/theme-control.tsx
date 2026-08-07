"use client";

import { useEffect, useRef, useState } from "react";
import { Check, ChevronDown, Monitor, Moon, Sun } from "lucide-react";
import { preferencesChangeEvent, readPreferences, writePreferences, type ThemeMode, type UserPreferences } from "@/lib/preferences";
import { useI18n } from "@/components/i18n-provider";
import type { TranslationKey } from "@/lib/i18n";

const themeOptions: { value: ThemeMode; label: TranslationKey; icon: typeof Monitor }[] = [
  { value: "system", label: "theme.system", icon: Monitor },
  { value: "light", label: "theme.light", icon: Sun },
  { value: "dark", label: "theme.dark", icon: Moon },
];

export function ThemeControl() {
  const { t } = useI18n();
  const [theme, setTheme] = useState<ThemeMode>("system");
  const [open, setOpen] = useState(false);
  const controlRef = useRef<HTMLDivElement>(null);
  const current = themeOptions.find((option) => option.value === theme) ?? themeOptions[0];
  const CurrentIcon = current.icon;
  const currentLabel = t(current.label);

  useEffect(() => {
    setTheme(readPreferences().theme);
    const handlePreferenceChange = (event: Event) => {
      const detail = (event as CustomEvent<UserPreferences>).detail;
      setTheme(detail?.theme ?? readPreferences().theme);
    };
    const handlePointerDown = (event: PointerEvent) => {
      if (controlRef.current && !controlRef.current.contains(event.target as Node)) setOpen(false);
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener(preferencesChangeEvent(), handlePreferenceChange);
    window.addEventListener("pointerdown", handlePointerDown);
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener(preferencesChangeEvent(), handlePreferenceChange);
      window.removeEventListener("pointerdown", handlePointerDown);
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, []);

  const selectTheme = (value: ThemeMode) => {
    writePreferences({ ...readPreferences(), theme: value });
    setTheme(value);
    setOpen(false);
  };

  return <div className="theme-control" ref={controlRef}>
    <button className="theme-control-trigger" aria-label={t("theme.current", { theme: currentLabel })} aria-haspopup="menu" aria-expanded={open} title={t("theme.current", { theme: currentLabel })} onClick={() => setOpen((value) => !value)}>
      <CurrentIcon size={14} />
      <span className="theme-control-label">{currentLabel}</span>
      <ChevronDown size={12} className="theme-control-chevron" />
    </button>
    {open && <div className="theme-control-menu" role="menu" aria-label={t("theme.select")}>
      {themeOptions.map(({ value, label, icon: Icon }) => <button key={value} className={`theme-control-option ${theme === value ? "selected" : ""}`} role="menuitemradio" aria-checked={theme === value} onClick={() => selectTheme(value)}>
        <Icon size={14} />
        <span>{t(label)}</span>
        {theme === value && <Check size={13} />}
      </button>)}
    </div>}
  </div>;
}
