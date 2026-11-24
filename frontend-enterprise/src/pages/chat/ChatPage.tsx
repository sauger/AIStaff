import { useCallback, useEffect, useState } from 'react';

import type { HarnessWorkspaceArtifact } from '@/types';

import { useChatSession } from './useChatSession';
import ChatDialogs from './components/ChatDialogs';
import type { OpenChatDocument } from './components/DocumentSidebar';
import ChatWorkbench from './workbench/ChatWorkbench';
import { EmployeeAppearanceProvider } from './workbench/useEmployeeAppearance';

export default function ChatPage() {
  const chat = useChatSession();
  const [openDocument, setOpenDocument] = useState<OpenChatDocument | null>(null);

  // 团队协作面板里的产物属于成员会话，session 由调用方传入；普通消息用当前会话。
  const openChatDocument = useCallback(
    (artifact: HarnessWorkspaceArtifact, sessionIdOverride?: string) => {
      const sessionId = sessionIdOverride || chat.activeConversationId;
      if (!sessionId) return;
      setOpenDocument((current) => (
        current
        && current.sessionId === sessionId
        && current.taskFrameId === artifact.task_frame_id
        && current.path === artifact.path
          ? { ...current, revision: current.revision + 1 }
          : {
            sessionId,
            taskFrameId: artifact.task_frame_id,
            path: artifact.path,
            displayName: artifact.display_name || artifact.path,
            revision: 0,
          }
      ));
    },
    [chat.activeConversationId],
  );

  // 切换会话后旧文档侧栏不再有意义，直接收起。
  useEffect(() => {
    setOpenDocument(null);
  }, [chat.activeConversationId]);

  return (
    <EmployeeAppearanceProvider>
      <div className="cw-page">
        <ChatWorkbench
          chat={chat}
          openDocument={openDocument}
          onOpenDocument={openChatDocument}
          onCloseDocument={() => setOpenDocument(null)}
        />
        <ChatDialogs chat={chat} />
      </div>
    </EmployeeAppearanceProvider>
  );
}
