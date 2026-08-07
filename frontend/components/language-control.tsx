"use client";

import { useEffect, useRef, useState } from "react";
import { Check, ChevronDown, Languages } from "lucide-react";
import { useI18n } from "@/components/i18n-provider";
import { supportedLocales, type Locale, type TranslationKey } from "@/lib/i18n";

const localeLabels: Record<Locale, TranslationKey> = {
  "zh-CN": "language.zh-CN",
  "en-US": "language.en-US",
};

export function LanguageControl() {
  const { locale, setLocale, t } = useI18n();
  const [open, setOpen] = useState(false);
  const controlRef = useRef<HTMLDivElement>(null);
  const currentLabel = t(localeLabels[locale]);

  useEffect(() => {
    const handlePointerDown = (event: PointerEvent) => {
      if (controlRef.current && !controlRef.current.contains(event.target as Node)) setOpen(false);
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("pointerdown", handlePointerDown);
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("pointerdown", handlePointerDown);
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, []);

  return <div className="theme-control language-control" ref={controlRef}>
    <button className="theme-control-trigger" aria-label={t("language.current", { language: currentLabel })} aria-haspopup="menu" aria-expanded={open} title={t("language.current", { language: currentLabel })} onClick={() => setOpen((value) => !value)}>
      <Languages size={14} />
      <span className="theme-control-label">{currentLabel}</span>
      <ChevronDown size={12} className="theme-control-chevron" />
    </button>
    {open && <div className="theme-control-menu" role="menu" aria-label={t("language.select")}>
      {supportedLocales.map((value) => <button key={value} className={`theme-control-option ${locale === value ? "selected" : ""}`} role="menuitemradio" aria-checked={locale === value} onClick={() => { setLocale(value); setOpen(false); }}>
        <span>{t(localeLabels[value])}</span>
        {locale === value && <Check size={13} />}
      </button>)}
    </div>}
  </div>;
}
