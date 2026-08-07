"use client";

import { useEffect, useState } from "react";
import { Check, Copy, WrapText } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Components } from "react-markdown";
import { useI18n } from "@/components/i18n-provider";

function ShikiCode({ code, language }: { code: string; language: string }) {
  const { t } = useI18n();
  const [html, setHtml] = useState("");
  const [copied, setCopied] = useState(false);
  const [wrapped, setWrapped] = useState(false);
  const [theme, setTheme] = useState("github-light-default");

  useEffect(() => {
    const update = () => setTheme(document.documentElement.dataset.theme === "dark" ? "github-dark-default" : "github-light-default");
    update();
    const observer = new MutationObserver(update);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    let active = true;
    setHtml("");
    import("shiki").then(({ codeToHtml }) => codeToHtml(code, { lang: language || "text", theme }))
      .then((result) => { if (active) setHtml(result); })
      .catch(() => { if (active) setHtml(""); });
    return () => { active = false; };
  }, [code, language, theme]);

  const copy = async () => {
    await navigator.clipboard.writeText(code);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  };

  return <div className={`code-wrap ${wrapped ? "code-wrap-lines" : ""}`}>
    <div className="code-toolbar"><span>{language || "text"}</span><div><button type="button" title={t("chat.code.wrap")} aria-pressed={wrapped} onClick={() => setWrapped((value) => !value)}><WrapText size={13} /></button><button type="button" onClick={() => void copy()}>{copied ? <Check size={13} /> : <Copy size={13} />}{copied ? t("chat.copied") : t("chat.copy")}</button></div></div>
    {html ? <div className="code-highlight" dangerouslySetInnerHTML={{ __html: html }} /> : <pre><code>{code}</code></pre>}
  </div>;
}

function codeComponent(highlight: boolean): NonNullable<Components["code"]> {
  return function Code({ className, children, ...props }) {
    const language = /language-([^\s]+)/.exec(className ?? "")?.[1] ?? "text";
    const code = String(children).replace(/\n$/, "");
    const inline = !className && !code.includes("\n");
    if (inline) return <code className="inline-code" {...props}>{children}</code>;
    if (highlight) return <ShikiCode code={code} language={language} />;
    return <div className="code-wrap streaming-code"><div className="code-toolbar"><span>{language}</span></div><pre><code className={className} {...props}>{code}</code></pre></div>;
  };
}

function markdownComponents(highlight: boolean): Components {
  return {
    pre({ children }) { return <>{children}</>; },
    code: codeComponent(highlight),
    table({ children }) { return <div className="markdown-table-wrap"><table>{children}</table></div>; },
    a({ href, children, ...props }) {
      const external = Boolean(href && /^https?:\/\//.test(href));
      return <a href={href} target={external ? "_blank" : undefined} rel={external ? "noreferrer" : undefined} {...props}>{children}</a>;
    },
  };
}

export function Markdown({ children, highlightCode = true }: { children: string; highlightCode?: boolean }) {
  return <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents(highlightCode)}>{children}</ReactMarkdown>;
}
