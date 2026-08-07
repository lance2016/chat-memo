import { Suspense } from "react";
import { MemoriesPage } from "@/components/memories-page";
import { WorkspacePageFallback } from "@/components/workspace-topbar";

export default function Memories() {
  return (
    <Suspense fallback={<WorkspacePageFallback active="memories" messageKey="loading.memories" />}>
      <MemoriesPage />
    </Suspense>
  );
}
