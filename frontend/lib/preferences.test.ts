import { beforeEach, describe, expect, it, vi } from "vitest";
import { defaultPreferences, isProfileAvatarImage, profileInitials, readPreferences, writePreferences } from "@/lib/preferences";

describe("profile preferences", () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.restoreAllMocks();
  });

  it("keeps a compressed local image avatar", () => {
    const avatar = "data:image/webp;base64,AAAA";
    writePreferences({ ...defaultPreferences, profileAvatar: avatar });

    expect(readPreferences().profileAvatar).toBe(avatar);
    expect(isProfileAvatarImage(avatar)).toBe(true);
    expect(profileInitials("Lance", avatar)).toBe("La");
  });

  it("rejects oversized image data instead of rendering data-url text", () => {
    const oversized = `data:image/webp;base64,${"A".repeat(180_000)}`;
    window.localStorage.setItem("personal-ai-assistant:preferences", JSON.stringify({ ...defaultPreferences, profileAvatar: oversized }));

    expect(readPreferences().profileAvatar).toBe(defaultPreferences.profileAvatar);
  });

  it("limits text avatars to two characters", () => {
    window.localStorage.setItem("personal-ai-assistant:preferences", JSON.stringify({ ...defaultPreferences, profileAvatar: "记忆助手" }));

    expect(readPreferences().profileAvatar).toBe("记忆");
  });
});
