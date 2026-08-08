"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Braces, Check, Copy, RefreshCw, Wrench } from "lucide-react";
import { errorMessage, getToolCatalog } from "@/lib/api";
import type { ToolCatalog as ToolCatalogData, ToolDefinition, ToolSchemaProperty } from "@/lib/types";
import { useI18n } from "@/components/i18n-provider";

function propertyType(property: ToolSchemaProperty) {
  if (property.type === "array") return `${property.items?.type ?? "any"}[]`;
  return property.type ?? "any";
}

function ToolItem({ tool }: { tool: ToolDefinition }) {
  const { t } = useI18n();
  const [copied, setCopied] = useState(false);
  const required = new Set(tool.input_schema.required ?? []);
  const properties = Object.entries(tool.input_schema.properties ?? {});
  const schemaText = JSON.stringify(tool.input_schema, null, 2);

  const copySchema = async () => {
    await navigator.clipboard.writeText(schemaText);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  };

  return <details className={`tool-catalog-item ${tool.enabled ? "" : "disabled"}`}>
    <summary>
      <span className="tool-catalog-glyph"><Wrench size={14} /></span>
      <span className="tool-catalog-summary-copy"><code>{tool.name}</code><small>{t("tools.parameters", { count: properties.length })} · {tool.protocols.join(" / ")}</small></span>
      <span className={`tool-status-pill ${tool.enabled ? "enabled" : "disabled"}`}>{tool.enabled ? t("tools.enabled") : t("tools.disabled")}</span>
    </summary>
    <div className="tool-catalog-detail">
      <p className="tool-description">{tool.description}</p>
      <div className="tool-availability"><span className={tool.enabled ? "online" : ""} />{tool.category === "kb" ? tool.enabled ? t("tools.availability.kbEnabled") : t("tools.availability.kbDisabled") : t("tools.availability.all")}{tool.native_protocol ? ` · ${t("tools.native", { provider: tool.native_protocol })}` : ""}</div>
      <div className="tool-schema-heading"><strong>Input schema</strong><span>{properties.length ? t("tools.requiredCount", { count: required.size }) : t("tools.noInput")}</span></div>
      {properties.length > 0 ? <div className="tool-parameter-list">
        {properties.map(([name, property]) => <div className="tool-parameter-row" key={name}>
          <div><code>{name}</code>{required.has(name) && <b>{t("tools.required")}</b>}</div>
          <code className="tool-parameter-type">{propertyType(property)}</code>
          <span>{property.description || (property.enum ? t("tools.values", { values: property.enum.join(" · ") }) : "—")}</span>
          {property.enum && property.description && <small>{property.enum.join(" · ")}</small>}
        </div>)}
      </div> : <div className="tool-no-parameters">{t("tools.noParameters")}</div>}
      <details className="tool-raw-schema">
        <summary><Braces size={13} />{t("tools.rawSchema")}</summary>
        <div><button type="button" className="tool-copy-schema" onClick={() => void copySchema()}>{copied ? <Check size={12} /> : <Copy size={12} />}{copied ? t("tools.copied") : t("tools.copy")}</button><pre>{schemaText}</pre></div>
      </details>
    </div>
  </details>;
}

export function ToolCatalog() {
  const { t } = useI18n();
  const [catalog, setCatalog] = useState<ToolCatalogData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setCatalog(await getToolCatalog());
    } catch (cause) {
      setError(errorMessage(cause, t("tools.loadError")));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => { void load(); }, [load]);

  const groups = useMemo(() => {
    const entries = new Map<string, { label: string; tools: ToolDefinition[] }>();
    for (const tool of catalog?.tools ?? []) {
      const categoryKey = tool.category === "memory" ? "tools.category.memory" : tool.category === "timeline" ? "tools.category.timeline" : "tools.category.knowledge";
      const group = entries.get(tool.category) ?? { label: t(categoryKey), tools: [] };
      group.tools.push(tool);
      entries.set(tool.category, group);
    }
    return [...entries.entries()];
  }, [catalog, t]);

  if (loading && !catalog) return <div className="settings-loading"><RefreshCw size={15} className="spin" />{t("tools.loading")}</div>;
  if (error) return <div className="tool-catalog-error"><span>{error}</span><button className="ghost-button" type="button" onClick={() => void load()}><RefreshCw size={12} />{t("tools.retry")}</button></div>;

  return <div className="tool-catalog">
    <div className="tool-catalog-overview"><div><strong>{catalog?.total ?? 0}</strong><span>{t("tools.total")}</span></div><div><strong>{catalog?.enabled ?? 0}</strong><span>{t("tools.enabledCount")}</span></div><div><strong>{(catalog?.total ?? 0) - (catalog?.enabled ?? 0)}</strong><span>{t("tools.waiting")}</span></div></div>
    {groups.map(([key, group]) => <section className="tool-catalog-group" key={key}><header><strong>{group.label}</strong><span>{group.tools.filter((tool) => tool.enabled).length} / {group.tools.length} {t("tools.available")}</span></header><div>{group.tools.map((tool) => <ToolItem tool={tool} key={tool.name} />)}</div></section>)}
  </div>;
}
