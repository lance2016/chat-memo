import { Suspense } from "react";
import { SettingsPage } from "@/components/settings-page";
import { WorkspacePageFallback } from "@/components/workspace-topbar";

export default function Settings() {
  return (
    <Suspense fallback={<WorkspacePageFallback active="settings" messageKey="loading.settings" />}>
      <SettingsPage />
    </Suspense>
  );
}
