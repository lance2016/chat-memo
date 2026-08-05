import { Suspense } from "react";
import { ChatPage } from "@/components/chat-page";

export default function Home() {
  return (
    <Suspense fallback={<div className="page-loading">正在打开助手…</div>}>
      <ChatPage />
    </Suspense>
  );
}
