"use client";

import { Languages } from "lucide-react";
import { useI18n } from "@/components/i18n-provider";
import type { Locale, TranslationKey } from "@/lib/i18n";

const localeLabels: Record<Locale, TranslationKey> = {
  "zh-CN": "language.zh-CN",
  "en-US": "language.en-US",
};

export function LanguageControl() {
  const { locale, setLocale, t } = useI18n();
  const currentLabel = t(localeLabels[locale]);
  const nextLocale: Locale = locale === "zh-CN" ? "en-US" : "zh-CN";
  const nextLabel = t(localeLabels[nextLocale]);
  const actionLabel = t("language.switch", { current: currentLabel, next: nextLabel });

  return <div className="theme-control language-control">
    <button className="theme-control-trigger direct-control-trigger" aria-label={actionLabel} title={actionLabel} onClick={() => setLocale(nextLocale)}>
      <Languages size={14} />
      <span className="theme-control-label">{currentLabel}</span>
    </button>
  </div>;
}
