import { enUS } from "@/lib/locales/en-US";
import { zhCN } from "@/lib/locales/zh-CN";

export const supportedLocales = ["zh-CN", "en-US"] as const;
export type Locale = (typeof supportedLocales)[number];
export type TranslationKey = keyof typeof zhCN;
export type TranslationValues = Record<string, string | number>;

const messages: Record<Locale, Record<TranslationKey, string>> = {
  "zh-CN": zhCN,
  "en-US": enUS,
};

export function isLocale(value: unknown): value is Locale {
  return typeof value === "string" && supportedLocales.includes(value as Locale);
}

export function translate(locale: Locale, key: TranslationKey, values?: TranslationValues) {
  const template = messages[locale][key] ?? messages["zh-CN"][key] ?? key;
  if (!values) return template;
  return template.replace(/\{([^}]+)\}/g, (match, name: string) => {
    const value = values[name];
    return value === undefined ? match : String(value);
  });
}
