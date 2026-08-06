import { Suspense } from "react";
import { ReviewPage } from "@/components/review-page";
import { WorkspacePageFallback } from "@/components/workspace-topbar";

export default function Review() {
  return (
    <Suspense fallback={<WorkspacePageFallback active="review" message="正在加载每日回顾…" />}>
      <ReviewPage />
    </Suspense>
  );
}
