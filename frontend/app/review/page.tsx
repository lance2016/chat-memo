import { Suspense } from "react";
import { ReviewPage } from "@/components/review-page";
import { WorkspacePageFallback } from "@/components/workspace-topbar";

export default function Review() {
  return (
    <Suspense fallback={<WorkspacePageFallback active="review" messageKey="loading.review" />}>
      <ReviewPage />
    </Suspense>
  );
}
