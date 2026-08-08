"use client";

import { useCallback, useEffect, useState } from "react";
import { Activity, Check, Copy, ExternalLink, RefreshCw, TriangleAlert } from "lucide-react";
import { errorMessage, getObservabilityStatus } from "@/lib/api";
import type { ObservabilityStatus } from "@/lib/types";
import { useI18n } from "@/components/i18n-provider";

/** Phoenix 状态卡。
 *
 * **刻意没有开关。** 启用 Phoenix 需要重建镜像（`INSTALL_OBS` 是构建参数）、
 * 重启进程（`setup_tracing` 在 create_app 里跑一次）、外加起 phoenix 容器 ——
 * 三件事都在启动之前。放个点了没反应的开关比没有更糟，所以这里只报告现状 +
 * 给出那一条启用命令。
 */
export function ObservabilityCard() {
  const { t } = useI18n();
  const [status, setStatus] = useState<ObservabilityStatus | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [copied, setCopied] = useState(false);

  // 浏览器要访问的是宿主机映射端口，后端只知道 compose 网络里的 phoenix:6006 ——
  // 两个地址不通用，所以链接地址走前端构建期变量。
  const phoenixUrl = process.env.NEXT_PUBLIC_PHOENIX_URL?.trim();

  const refresh = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    try {
      setStatus(await getObservabilityStatus(signal));
      setError("");
    } catch (cause) {
      if (cause instanceof DOMException && cause.name === "AbortError") return;
      setError(errorMessage(cause, t("settings.obs.error")));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    const controller = new AbortController();
    void refresh(controller.signal);
    return () => controller.abort();
  }, [refresh]);

  const copyCommand = async () => {
    if (!status) return;
    await navigator.clipboard.writeText(status.enable_command);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  };

  const tone = status?.stage === "ready" ? "success" : status?.stage === "off" ? "neutral" : "warn";

  return <div className="obs-card">
    <div className="obs-card-heading">
      <div className="obs-headline">
        <span className={`tts-status-badge ${tone}`}>
          <span className="tts-status-dot" />
          {loading && !status ? t("settings.obs.checking") : t(`settings.obs.stage.${status?.stage ?? "off"}`)}
        </span>
        {status?.stage === "ready" && <span className="obs-project">{t("settings.obs.project", { name: status.project })}</span>}
      </div>
      <div className="obs-actions">
        <button className="icon-button neutral-hover" type="button" aria-label={t("settings.obs.refresh")} title={t("settings.obs.refresh")} onClick={() => void refresh()} disabled={loading}>
          <RefreshCw size={13} className={loading ? "spin" : ""} />
        </button>
        {status?.stage === "ready" && phoenixUrl && <a className="ghost-button" href={phoenixUrl} target="_blank" rel="noreferrer">
          <ExternalLink size={13} />{t("settings.obs.open")}
        </a>}
      </div>
    </div>

    {error && <div className="obs-detail warn"><TriangleAlert size={13} />{error}</div>}
    {status?.detail && <div className="obs-detail warn"><TriangleAlert size={13} />{status.detail}</div>}

    {status && status.stage !== "ready" && <div className="obs-enable">
      <p>{t("settings.obs.enableHint")}</p>
      <div className="obs-command">
        <code>{status.enable_command}</code>
        <button className="icon-button" type="button" aria-label={t("settings.obs.copy")} title={t("settings.obs.copy")} onClick={() => void copyCommand()}>
          {copied ? <Check size={13} /> : <Copy size={13} />}
        </button>
      </div>
    </div>}

    {status?.stage === "ready" && <dl className="obs-facts">
      <div><dt>{t("settings.obs.tracedPaths")}</dt><dd>{status.traced_paths.join(" · ") || "—"}</dd></div>
      <div><dt>{t("settings.obs.collector")}</dt><dd><code>{status.collector_endpoint}</code></dd></div>
    </dl>}

    {status && <p className="obs-privacy"><Activity size={12} />{status.retention_warning}</p>}
  </div>;
}
