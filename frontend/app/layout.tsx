import type { Metadata } from "next";
import { GlobalSearch } from "@/components/global-search";
import { KeyboardShortcuts } from "@/components/keyboard-shortcuts";
import { ThemeProvider } from "@/components/theme-provider";
import { WorkspaceFrame } from "@/components/workspace-frame";
import { I18nProvider } from "@/components/i18n-provider";
import { ToastProvider } from "@/components/toast";
import "./globals.css";
import "./ios-system.css";

export const metadata: Metadata = {
  title: "朝花夕拾",
  description: "带有长期记忆的个人 AI 助手",
  icons: { icon: "/morning-memory-logo.png" },
};

const themeBootstrap = `(() => {
  try {
    const raw = localStorage.getItem("personal-ai-assistant:preferences");
    const preferences = raw ? (JSON.parse(raw) ?? {}) : {};
    const stored = preferences.theme ?? "light";
    const mode = stored === "light" || stored === "dark" ? stored : "system";
    const resolved = mode === "system"
      ? (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark")
      : mode;
    document.documentElement.dataset.theme = resolved;
    document.documentElement.style.colorScheme = resolved;
    document.documentElement.lang = preferences.locale === "en-US" ? "en-US" : "zh-CN";
  } catch {}
})();`;

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <head><script dangerouslySetInnerHTML={{ __html: themeBootstrap }} /></head>
      <body><I18nProvider><ThemeProvider><ToastProvider><WorkspaceFrame>{children}</WorkspaceFrame><GlobalSearch /><KeyboardShortcuts /></ToastProvider></ThemeProvider></I18nProvider></body>
    </html>
  );
}
