import { Suspense } from "react";
import { ChatPage } from "@/components/chat-page";
import { WorkspacePageFallback } from "@/components/workspace-topbar";

export default function Home() {
  return (
    <Suspense fallback={<WorkspacePageFallback active="chat" messageKey="loading.chat" />}>
      <ChatPage />
    </Suspense>
  );
}
