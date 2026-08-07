import { describe, expect, it } from "vitest";
import { isLocale, translate } from "./i18n";

describe("i18n", () => {
  it("translates the same key for both supported locales", () => {
    expect(translate("zh-CN", "nav.settings")).toBe("设置");
    expect(translate("en-US", "nav.settings")).toBe("Settings");
  });

  it("interpolates named values", () => {
    expect(translate("en-US", "theme.current", { theme: "Dark" })).toBe("Current theme: Dark");
  });

  it("accepts only supported locale identifiers", () => {
    expect(isLocale("zh-CN")).toBe(true);
    expect(isLocale("en-US")).toBe(true);
    expect(isLocale("fr-FR")).toBe(false);
  });
});
