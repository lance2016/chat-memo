"use client";

import { useEffect, useState } from "react";
import { preferencesChangeEvent, readPreferences, type ThemeMode, type UserPreferences } from "@/lib/preferences";

function resolveTheme(mode: ThemeMode) {
  if (mode !== "system") return mode;
  return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

function applyTheme(mode: ThemeMode) {
  const resolved = resolveTheme(mode);
  document.documentElement.dataset.theme = resolved;
  document.documentElement.style.colorScheme = resolved;
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [mode, setMode] = useState<ThemeMode>("system");

  useEffect(() => {
    const initial = readPreferences().theme;
    setMode(initial);
    applyTheme(initial);

    const media = window.matchMedia("(prefers-color-scheme: light)");
    const handleSystemChange = () => {
      if (readPreferences().theme === "system") applyTheme("system");
    };
    const handlePreferenceChange = (event: Event) => {
      const next = (event as CustomEvent<UserPreferences>).detail;
      const nextMode = next?.theme ?? readPreferences().theme;
      setMode(nextMode);
      applyTheme(nextMode);
    };

    media.addEventListener("change", handleSystemChange);
    window.addEventListener(preferencesChangeEvent(), handlePreferenceChange);
    return () => {
      media.removeEventListener("change", handleSystemChange);
      window.removeEventListener(preferencesChangeEvent(), handlePreferenceChange);
    };
  }, []);

  useEffect(() => {
    if (mode) applyTheme(mode);
  }, [mode]);

  return <>{children}</>;
}
