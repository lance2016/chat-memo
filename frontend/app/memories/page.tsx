import { Suspense } from "react";
import { MemoriesPage } from "@/components/memories-page";
import { WorkspacePageFallback } from "@/components/workspace-topbar";

export default function Memories() {
  return (
    <Suspense fallback={<WorkspacePageFallback active="memories" message="正在加载记忆…" />}>
      <MemoriesPage />
    </Suspense>
  );
}
