import { Suspense } from "react";
import { SettingsPage } from "@/components/settings-page";

export default function Settings() {
  return (
    <Suspense fallback={<div className="page-loading">正在加载设置…</div>}>
      <SettingsPage />
    </Suspense>
  );
}
