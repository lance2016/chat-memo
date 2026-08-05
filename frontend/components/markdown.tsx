"use client";

import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Components } from "react-markdown";
import { codeToHtml } from "shiki";

function ShikiCode({ code, language }: { code: string; language: string }) {
  const [html, setHtml] = useState("");
  useEffect(() => {
    let active = true;
    codeToHtml(code, { lang: language || "text", theme: "github-dark-default" })
      .then((result) => { if (active) setHtml(result); })
      .catch(() => { if (active) setHtml(""); });
    return () => { active = false; };
  }, [code, language]);

  if (html) return <div className="code-wrap" dangerouslySetInnerHTML={{ __html: html }} />;
  return <div className="code-wrap"><pre><code>{code}</code></pre></div>;
}

const components: Components = {
  code({ className, children, ...props }) {
    const language = /language-(\w+)/.exec(className ?? "")?.[1] ?? "text";
    const code = String(children).replace(/\n$/, "");
    const inline = !className && !code.includes("\n");
    if (inline) return <code className="inline-code" {...props}>{children}</code>;
    return <ShikiCode code={code} language={language} />;
  },
};

export function Markdown({ children }: { children: string }) {
  return <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>{children}</ReactMarkdown>;
}
