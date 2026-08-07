"use client";

import { usePathname } from "next/navigation";
import { WorkspaceTopbar, type WorkspacePage } from "@/components/workspace-topbar";

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

  return <div className="workspace-frame">
    <WorkspaceTopbar active={activePage(pathname)} />
    <div className="workspace-route-content">{children}</div>
  </div>;
}
