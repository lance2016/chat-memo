import { Suspense } from "react";
import { ReviewPage } from "@/components/review-page";

export default function Review() {
  return (
    <Suspense fallback={<div className="page-loading">正在加载每日回顾…</div>}>
      <ReviewPage />
    </Suspense>
  );
}
