export interface UserPreferences {
  enterToSend: boolean;
  autoScroll: boolean;
}

export const defaultPreferences: UserPreferences = {
  enterToSend: true,
  autoScroll: true,
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
