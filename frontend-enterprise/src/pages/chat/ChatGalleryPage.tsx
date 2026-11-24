import { api, TENANT_ID } from '@/api/client';
import { notify } from '@/components/ui/app-toast';
import { getEnterpriseAuthSession, isEnterpriseAdmin } from '@/auth';
import type { AgentProfileRead } from '@/types';

import EmployeeGalleryPage from '../EmployeeGalleryPage';
import { useChatSession } from './useChatSession';
import ChatDialogs from './components/ChatDialogs';
import WorkbenchHeader from './workbench/WorkbenchHeader';
import { EmployeeAppearanceProvider } from './workbench/useEmployeeAppearance';

export default function ChatGalleryPage() {
  const chat = useChatSession();
  const auth = getEnterpriseAuthSession();
  const isAdmin = isEnterpriseAdmin(auth?.user);

  async function startGalleryChat(agent: AgentProfileRead) {
    try {
      await api.post<AgentProfileRead>(`/api/chat/agents/${agent.id}/use?tenant_id=${TENANT_ID}`, {});
      await chat.refreshAgents(agent.id);
      chat.openAgentWorkspace(agent.id);
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '无法打开数字员工');
    }
  }

  return (
    <EmployeeAppearanceProvider>
      <div className="cw-page flex h-screen min-h-0 flex-col bg-[#f7f7f7] text-[#272b34]">
        <WorkbenchHeader chat={chat} />
        <main className="min-h-0 flex-1 overflow-y-auto bg-white">
          <EmployeeGalleryPage
            embedded
            currentUser={auth?.user}
            isAdmin={isAdmin}
            onStartChat={startGalleryChat}
          />
        </main>
        <ChatDialogs chat={chat} />
      </div>
    </EmployeeAppearanceProvider>
  );
}
