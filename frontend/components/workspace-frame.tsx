"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { WorkspaceTopbar, type WorkspacePage } from "@/components/workspace-topbar";
import { useI18n } from "@/components/i18n-provider";

const sidebarPreferenceKey = "personal-ai-assistant:sidebar-collapsed";

function activePage(pathname: string): WorkspacePage {
  if (pathname.startsWith("/memories")) return "memories";
  if (pathname.startsWith("/review")) return "review";
  if (pathname.startsWith("/timeline")) return "timeline";
  if (pathname.startsWith("/settings")) return "settings";
  return "chat";
}

/**
 * Persistent application chrome. Next.js keeps layouts mounted between route
 * transitions, so only the content pane changes when a workspace tab opens.
 */
export function WorkspaceFrame({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const active = activePage(pathname);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const { t } = useI18n();

  useEffect(() => {
    try { setSidebarCollapsed(localStorage.getItem(sidebarPreferenceKey) === "true"); } catch {}
  }, []);

  const updateSidebarCollapsed = (collapsed: boolean) => {
    setSidebarCollapsed(collapsed);
    try { localStorage.setItem(sidebarPreferenceKey, String(collapsed)); } catch {}
  };

  return <div className={`workspace-frame ${sidebarCollapsed ? "sidebar-collapsed" : ""}`} data-active-page={active}>
    <a className="skip-link" href="#workspace-main-content">{t("workspace.skipToContent")}</a>
    <WorkspaceTopbar active={active} sidebarCollapsed={sidebarCollapsed} onSidebarCollapsedChange={updateSidebarCollapsed} />
    <div className="workspace-route-content" id="workspace-main-content" tabIndex={-1}>{children}</div>
  </div>;
}
