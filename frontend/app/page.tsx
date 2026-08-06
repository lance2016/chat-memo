import { Suspense } from "react";
import { ChatPage } from "@/components/chat-page";
import { WorkspacePageFallback } from "@/components/workspace-topbar";

export default function Home() {
  return (
    <Suspense fallback={<WorkspacePageFallback active="chat" message="正在打开助手…" />}>
      <ChatPage />
    </Suspense>
  );
}
