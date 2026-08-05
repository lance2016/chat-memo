import { Suspense } from "react";
import { MemoriesPage } from "@/components/memories-page";

export default function Memories() {
  return (
    <Suspense fallback={<div className="page-loading">正在加载记忆…</div>}>
      <MemoriesPage />
    </Suspense>
  );
}
