import { Suspense } from "react";
import { SettingsPage } from "@/components/settings-page";
import { WorkspacePageFallback } from "@/components/workspace-topbar";

export default function Settings() {
  return (
    <Suspense fallback={<WorkspacePageFallback active="settings" message="正在加载设置…" />}>
      <SettingsPage />
    </Suspense>
  );
}
