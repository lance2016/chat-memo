"use client";

import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { translate, type Locale, type TranslationKey, type TranslationValues } from "@/lib/i18n";
import { preferencesChangeEvent, readPreferences, writePreferences, type UserPreferences } from "@/lib/preferences";

interface I18nContextValue {
  locale: Locale;
  setLocale: (locale: Locale) => void;
  t: (key: TranslationKey, values?: TranslationValues) => string;
}

const I18nContext = createContext<I18nContextValue>({
  locale: "zh-CN",
  setLocale: () => undefined,
  t: (key, values) => translate("zh-CN", key, values),
});

function applyLocale(locale: Locale) {
  document.documentElement.lang = locale;
  document.title = translate(locale, "app.title");
  document.querySelector('meta[name="description"]')?.setAttribute("content", translate(locale, "app.description"));
}

export function I18nProvider({ children }: { children: React.ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>("zh-CN");

  useEffect(() => {
    const initial = readPreferences().locale;
    setLocaleState(initial);
    applyLocale(initial);
    const handlePreferenceChange = (event: Event) => {
      const detail = (event as CustomEvent<UserPreferences>).detail;
      const next = detail?.locale ?? readPreferences().locale;
      setLocaleState(next);
      applyLocale(next);
    };
    window.addEventListener(preferencesChangeEvent(), handlePreferenceChange);
    return () => window.removeEventListener(preferencesChangeEvent(), handlePreferenceChange);
  }, []);

  const value = useMemo<I18nContextValue>(() => ({
    locale,
    setLocale: (next) => {
      setLocaleState(next);
      applyLocale(next);
      writePreferences({ ...readPreferences(), locale: next });
    },
    t: (key, values) => translate(locale, key, values),
  }), [locale]);

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n() {
  return useContext(I18nContext);
}
