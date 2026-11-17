import { type CSSProperties, useCallback, useEffect, useState } from 'react';

import AppSidebar from '@/components/AppSidebar';
import { SidebarProvider } from '@/components/ui/sidebar';
import { cn } from '@/lib/utils';
import type { HarnessWorkspaceArtifact } from '@/types';

import { CHAT_MAIN_CLASS } from './chatPageStyles';
import { sessionHasUnreadReply } from './chatHelpers';
import { useChatSession } from './useChatSession';
import ChatHeader from './components/ChatHeader';
import MessageList from './components/MessageList';
import Composer from './components/Composer';
import ChatDialogs from './components/ChatDialogs';
import DocumentSidebar, { type OpenChatDocument } from './components/DocumentSidebar';

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
    <SidebarProvider
      open={!chat.sidebarCollapsed}
      onOpenChange={(open) => {
        if (open === chat.sidebarCollapsed) chat.toggleSidebar();
      }}
      style={
        {
          '--sidebar-width': '220px',
          '--sidebar-width-icon': '72px',
        } as CSSProperties
      }
      className="h-screen min-h-0 bg-[#fcfcfc] text-[#18181a]"
    >
      <AppSidebar
        variant="chat"
        sessions={chat.visibleSidebarSessions}
        sessionsLoading={chat.sessionsLoading}
        agents={chat.agents}
        scopeTeams={chat.teams}
        activeSessionId={chat.sessionId}
        sessionFilter={chat.sessionAgentFilter}
        onSessionFilterChange={chat.setSessionAgentFilter}
        sessionFilterOptions={chat.sessionFilterOptions}
        isSessionUnread={(session) => sessionHasUnreadReply(session, chat.sessionReadTimes, chat.sessionId)}
        onOpenSession={chat.openSession}
        onNewConversation={() => {
          const selectedAgent = chat.sessionAgentFilter === 'all'
            ? chat.displayedAgent
            : chat.agents.find((agent) => agent.id === chat.sessionAgentFilter);
          if (selectedAgent) chat.openDraftForAgent(selectedAgent.id);
          else chat.openGallery();
        }}
        onOpenGallery={chat.openGallery}
        handoffCount={chat.handoffs.length}
        onOpenHandoffs={chat.openHandoffInbox}
        onRenameSession={chat.openRename}
        onDeleteSession={chat.requestDelete}
        onOpenAdmin={chat.openAdmin}
      />
      <main className={cn(CHAT_MAIN_CLASS, 'flex-1')}>
        <ChatHeader chat={chat} />
        <MessageList chat={chat} onOpenDocument={openChatDocument} />
        <Composer chat={chat} />
      </main>
      {openDocument && (
        <DocumentSidebar
          chat={chat}
          document={openDocument}
          onClose={() => setOpenDocument(null)}
        />
      )}
      <ChatDialogs chat={chat} />
    </SidebarProvider>
  );
}
