import { isLocale, type Locale } from "@/lib/i18n";

export type ThemeMode = "system" | "light" | "dark";
export type ChatSkin = "mist" | "ocean" | "lavender" | "graphite";
export type ProfileTone = "blue" | "violet" | "teal" | "orange";

export interface UserPreferences {
  enterToSend: boolean;
  autoScroll: boolean;
  showToolActivity: boolean;
  showUsage: boolean;
  theme: ThemeMode;
  chatSkin: ChatSkin;
  /** Composer choices are keyed by stable model-profile slug. */
  modelThinking: Record<string, boolean>;
  modelThinkingEffort: Record<string, string>;
  locale: Locale;
  profileName: string;
  profileAvatar: string;
  profileTone: ProfileTone;
}

export const defaultPreferences: UserPreferences = {
  enterToSend: true,
  autoScroll: true,
  showToolActivity: true,
  showUsage: false,
  theme: "light",
  chatSkin: "mist",
  modelThinking: {},
  modelThinkingEffort: {},
  locale: "zh-CN",
  profileName: "Lance",
  profileAvatar: "L",
  profileTone: "blue",
};

const STORAGE_KEY = "personal-ai-assistant:preferences";
const CHANGE_EVENT = "personal-ai-assistant:preferences-change";

function booleanRecord(value: unknown): Record<string, boolean> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return Object.fromEntries(
    Object.entries(value).filter((entry): entry is [string, boolean] => typeof entry[1] === "boolean"),
  );
}

function stringRecord(value: unknown): Record<string, string> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return Object.fromEntries(
    Object.entries(value).filter((entry): entry is [string, string] => typeof entry[1] === "string"),
  );
}

export function readPreferences(): UserPreferences {
  if (typeof window === "undefined") return defaultPreferences;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return defaultPreferences;
    const parsed = JSON.parse(raw) as Partial<UserPreferences>;
    const rawAvatar = typeof parsed.profileAvatar === "string" ? parsed.profileAvatar.trim() : "";
    const avatarLooksLikeImage = rawAvatar.toLocaleLowerCase().startsWith("data:image/");
    const profileAvatar = /^data:image\/(?:png|jpeg|webp);base64,[a-z0-9+/]+=*$/i.test(rawAvatar) && rawAvatar.length <= 180_000
      ? rawAvatar
      : avatarLooksLikeImage ? "" : rawAvatar.slice(0, 2);
    return {
      enterToSend: parsed.enterToSend !== false,
      autoScroll: parsed.autoScroll !== false,
      showToolActivity: parsed.showToolActivity !== false,
      showUsage: parsed.showUsage === true,
      theme: parsed.theme === "light" || parsed.theme === "dark" ? parsed.theme : "system",
      chatSkin: parsed.chatSkin === "ocean" || parsed.chatSkin === "lavender" || parsed.chatSkin === "graphite" ? parsed.chatSkin : "mist",
      modelThinking: booleanRecord(parsed.modelThinking),
      modelThinkingEffort: stringRecord(parsed.modelThinkingEffort),
      locale: isLocale(parsed.locale) ? parsed.locale : "zh-CN",
      profileName: typeof parsed.profileName === "string" && parsed.profileName.trim() ? parsed.profileName.trim().slice(0, 32) : defaultPreferences.profileName,
      profileAvatar: profileAvatar || defaultPreferences.profileAvatar,
      profileTone: parsed.profileTone === "violet" || parsed.profileTone === "teal" || parsed.profileTone === "orange" ? parsed.profileTone : "blue",
    };
  } catch {
    return defaultPreferences;
  }
}

export function profileInitials(name: string, avatar: string) {
  const label = isProfileAvatarImage(avatar) ? name.trim() : avatar.trim() || name.trim();
  return label ? label.slice(0, 2) : "M";
}

export function isProfileAvatarImage(value: string) {
  return /^data:image\/(?:png|jpeg|webp);base64,/i.test(value);
}

export function writePreferences(next: UserPreferences) {
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  window.dispatchEvent(new CustomEvent(CHANGE_EVENT, { detail: next }));
}

export function preferencesChangeEvent() {
  return CHANGE_EVENT;
}
