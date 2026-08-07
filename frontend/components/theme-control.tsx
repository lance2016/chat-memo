"use client";

import { useEffect, useState } from "react";
import { Monitor, Moon, Sun } from "lucide-react";
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
  const current = themeOptions.find((option) => option.value === theme) ?? themeOptions[0];
  const CurrentIcon = current.icon;
  const currentLabel = t(current.label);
  const currentIndex = themeOptions.findIndex((option) => option.value === current.value);
  const next = themeOptions[(currentIndex + 1) % themeOptions.length];
  const nextLabel = t(next.label);
  const actionLabel = t("theme.switch", { current: currentLabel, next: nextLabel });

  useEffect(() => {
    setTheme(readPreferences().theme);
    const handlePreferenceChange = (event: Event) => {
      const detail = (event as CustomEvent<UserPreferences>).detail;
      setTheme(detail?.theme ?? readPreferences().theme);
    };
    window.addEventListener(preferencesChangeEvent(), handlePreferenceChange);
    return () => window.removeEventListener(preferencesChangeEvent(), handlePreferenceChange);
  }, []);

  const selectTheme = (value: ThemeMode) => {
    writePreferences({ ...readPreferences(), theme: value });
    setTheme(value);
  };

  return <div className="theme-control">
    <button className="theme-control-trigger direct-control-trigger" aria-label={actionLabel} title={actionLabel} onClick={() => selectTheme(next.value)}>
      <CurrentIcon size={14} />
      <span className="theme-control-label">{currentLabel}</span>
    </button>
  </div>;
}
