"use client";

import { useEffect, useRef, useState } from "react";
import { ArrowUpRight, BookOpen, Command, LoaderCircle, MessageSquare, Search, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { errorMessage, searchAll } from "@/lib/api";
import type { SearchConversationHit, SearchMemoryHit, SearchResults } from "@/lib/types";

function formatTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

function HighlightText({ text, query }: { text: string; query: string }) {
  const normalizedText = text.toLocaleLowerCase();
  const normalizedQuery = query.toLocaleLowerCase();
  if (!normalizedQuery) return <>{text}</>;

  const parts: React.ReactNode[] = [];
  let cursor = 0;
  let index = normalizedText.indexOf(normalizedQuery, cursor);
  while (index >= 0) {
    if (index > cursor) parts.push(text.slice(cursor, index));
    parts.push(<mark key={`${index}-${cursor}`}>{text.slice(index, index + query.length)}</mark>);
    cursor = index + query.length;
    index = normalizedText.indexOf(normalizedQuery, cursor);
  }
  if (cursor < text.length) parts.push(text.slice(cursor));
  return <>{parts.length ? parts : text}</>;
}

function ConversationResult({ hit, query, onOpen }: { hit: SearchConversationHit; query: string; onOpen: (hit: SearchConversationHit) => void }) {
  return <button className="search-result" onClick={() => onOpen(hit)}>
    <span className="search-result-icon"><MessageSquare size={15} /></span>
    <span className="search-result-copy"><strong>{hit.title}</strong><span><HighlightText text={hit.snippet} query={query} /></span></span>
    <span className="search-result-meta">{hit.matches > 1 ? `${hit.matches} 处` : "对话"} · {formatTime(hit.created_at)}<ArrowUpRight size={13} /></span>
  </button>;
}

function MemoryResult({ hit, query, onOpen }: { hit: SearchMemoryHit; query: string; onOpen: (hit: SearchMemoryHit) => void }) {
  return <button className="search-result" onClick={() => onOpen(hit)}>
    <span className="search-result-icon memory-result-icon"><BookOpen size={15} /></span>
    <span className="search-result-copy"><strong>{hit.path}</strong><span><HighlightText text={hit.snippet} query={query} /></span></span>
    <span className="search-result-meta">记忆 <ArrowUpRight size={13} /></span>
  </button>;
}

export function GlobalSearch() {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResults | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setOpen(true);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  useEffect(() => {
    if (!open) return;
    inputRef.current?.focus();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const trimmed = query.trim();
    abortRef.current?.abort();
    if (trimmed.length < 2) {
      setResults(null);
      setLoading(false);
      setError("");
      return;
    }

    const controller = new AbortController();
    abortRef.current = controller;
    const timer = window.setTimeout(() => {
      setLoading(true);
      setError("");
      void searchAll(trimmed, 20, controller.signal)
        .then(setResults)
        .catch((cause: unknown) => {
          if (cause instanceof DOMException && cause.name === "AbortError") return;
          setError(errorMessage(cause, "搜索失败"));
          setResults(null);
        })
        .finally(() => {
          if (!controller.signal.aborted) setLoading(false);
        });
    }, 300);

    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [open, query]);

  const close = () => {
    abortRef.current?.abort();
    setOpen(false);
    setQuery("");
    setResults(null);
    setError("");
  };

  const openConversation = (hit: SearchConversationHit) => {
    close();
    router.push(`/?conversation=${hit.conversation_id}&message=${hit.message_id}`);
  };

  const openMemory = (hit: SearchMemoryHit) => {
    close();
    router.push(`/memories?path=${encodeURIComponent(hit.path)}`);
  };

  return <>
    <button className="global-search-trigger" aria-label="全局搜索" title="全局搜索（⌘K / Ctrl+K）" onClick={() => setOpen(true)}><Search size={14} /><span>搜索</span><kbd><Command size={10} />K</kbd></button>
    {open && <div className="search-overlay" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) close(); }}>
      <section className="search-dialog" role="dialog" aria-modal="true" aria-label="全局搜索">
        <div className="search-input-wrap"><Search size={17} /><input ref={inputRef} value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => { if (event.key === "Escape") close(); }} placeholder="搜索对话和记忆…" /><kbd>ESC</kbd><button className="icon-button" aria-label="关闭搜索" onClick={close}><X size={16} /></button></div>
        <div className="search-body">
          {query.trim().length < 2 && <div className="search-hint"><Command size={16} /><span>搜索至少输入 2 个字符</span><small>支持搜索对话正文和记忆内容</small></div>}
          {loading && <div className="search-status"><LoaderCircle size={16} className="spin" />正在搜索…</div>}
          {error && <div className="search-error">{error}</div>}
          {!loading && !error && results && !results.conversations.length && !results.memories.length && <div className="search-status">没有找到与“{results.query}”相关的内容</div>}
          {!loading && results && results.conversations.length > 0 && <div className="search-group"><div className="search-group-title"><MessageSquare size={13} />对话 <span>{results.conversations.length}</span></div>{results.conversations.map((hit) => <ConversationResult key={`${hit.conversation_id}-${hit.message_id}`} hit={hit} query={results.query} onOpen={openConversation} />)}</div>}
          {!loading && results && results.memories.length > 0 && <div className="search-group"><div className="search-group-title"><BookOpen size={13} />记忆 <span>{results.memories.length}</span></div>{results.memories.map((hit) => <MemoryResult key={hit.path} hit={hit} query={results.query} onOpen={openMemory} />)}</div>}
        </div>
        <footer className="search-footer"><span>点击结果打开</span><span>Esc 关闭</span><span>搜索内容来自对话正文和记忆文件</span></footer>
      </section>
    </div>}
  </>;
}
