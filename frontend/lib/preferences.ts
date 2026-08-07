import { isLocale, type Locale } from "@/lib/i18n";

export type ThemeMode = "system" | "light" | "dark";

export interface UserPreferences {
  enterToSend: boolean;
  autoScroll: boolean;
  showThinking: boolean;
  showToolActivity: boolean;
  showUsage: boolean;
  theme: ThemeMode;
  locale: Locale;
}

export const defaultPreferences: UserPreferences = {
  enterToSend: true,
  autoScroll: true,
  showThinking: false,
  showToolActivity: true,
  showUsage: false,
  theme: "light",
  locale: "zh-CN",
};

const STORAGE_KEY = "personal-ai-assistant:preferences";
const CHANGE_EVENT = "personal-ai-assistant:preferences-change";

export function readPreferences(): UserPreferences {
  if (typeof window === "undefined") return defaultPreferences;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return defaultPreferences;
    const parsed = JSON.parse(raw) as Partial<UserPreferences>;
    return {
      enterToSend: parsed.enterToSend !== false,
      autoScroll: parsed.autoScroll !== false,
      showThinking: parsed.showThinking === true,
      showToolActivity: parsed.showToolActivity !== false,
      showUsage: parsed.showUsage === true,
      theme: parsed.theme === "light" || parsed.theme === "dark" ? parsed.theme : "system",
      locale: isLocale(parsed.locale) ? parsed.locale : "zh-CN",
    };
  } catch {
    return defaultPreferences;
  }
}

export function writePreferences(next: UserPreferences) {
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  window.dispatchEvent(new CustomEvent(CHANGE_EVENT, { detail: next }));
}

export function preferencesChangeEvent() {
  return CHANGE_EVENT;
}
