import type { Metadata } from "next";
import { GlobalSearch } from "@/components/global-search";
import { ThemeProvider } from "@/components/theme-provider";
import "./globals.css";

export const metadata: Metadata = {
  title: "个人 AI 助手",
  description: "带有长期记忆的个人 AI 助手",
};

const themeBootstrap = `(() => {
  try {
    const raw = localStorage.getItem("personal-ai-assistant:preferences");
    const stored = raw ? JSON.parse(raw).theme : "light";
    const mode = stored === "light" || stored === "dark" ? stored : "system";
    const resolved = mode === "system"
      ? (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark")
      : mode;
    document.documentElement.dataset.theme = resolved;
    document.documentElement.style.colorScheme = resolved;
  } catch {}
})();`;

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <head><script dangerouslySetInnerHTML={{ __html: themeBootstrap }} /></head>
      <body><ThemeProvider>{children}<GlobalSearch /></ThemeProvider></body>
    </html>
  );
}
