import type { Metadata } from "next";
import { GlobalSearch } from "@/components/global-search";
import { ThemeProvider } from "@/components/theme-provider";
import "./globals.css";

export const metadata: Metadata = {
  title: "个人 AI 助手",
  description: "带有长期记忆的个人 AI 助手",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <body><ThemeProvider>{children}<GlobalSearch /></ThemeProvider></body>
    </html>
  );
}
