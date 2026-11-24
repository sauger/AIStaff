import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from 'react';
import { useParams, useSearchParams } from 'react-router-dom';

import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { agentResourceCount, employeeDisplayName, employeeProfile, visibleEmployeeAgents } from '@/employee';
import { sessionHasUnreadReply } from '../chatHelpers';
import { compareDeskStatus, employeeDeskStatus } from './employeeDeskStatus';
import type { AgentProfileRead, HarnessWorkspaceArtifact, TeamRead } from '@/types';

import Composer from '../components/Composer';
import MessageList from '../components/MessageList';
import type { OpenChatDocument } from '../components/DocumentSidebar';
import type { UseChatSession } from '../useChatSession';
import ChatHeading from './ChatHeading';
import './workbench.css';
import DocumentOverlay from './DocumentOverlay';
import EmployeeDetailPanel from './EmployeeDetailPanel';
import EmployeeYard from './EmployeeYard';
import WorkbenchEmptyState from './WorkbenchEmptyState';
import WorkbenchHeader from './WorkbenchHeader';
import type { YardEmployee } from './EmployeeYardCard';
import { artForRole } from './employeeArt';
import { useWorkbenchBreakpoints } from './useMediaQuery';

/**
 * 详情栏始终是单个员工。非团队会话用当前员工；团队会话优先当前成员，
 * 当前员工不在团队里时用项目领导，没有领导则用第一位成员。
 */
export function resolveDetailAgent(input: {
  displayedAgent: AgentProfileRead | null;
  displayedTeam: TeamRead | null;
  agents: AgentProfileRead[];
}): AgentProfileRead | null {
  const { displayedAgent, displayedTeam, agents } = input;
  if (!displayedTeam) return displayedAgent;
  const memberIds = new Set(displayedTeam.members.map((member) => member.agent_id));
  if (displayedAgent && memberIds.has(displayedAgent.id)) return displayedAgent;
  const picked = displayedTeam.members.find((member) => member.role === 'leader')
    || displayedTeam.members[0];
  if (!picked) return displayedAgent;
  return agents.find((agent) => agent.id === picked.agent_id) || displayedAgent;
}

/**
 * RapidStaff 三栏工作台。默认直接进入 380px 员工工位 / 会话区 / 300px 详情。
 * 欢迎首屏只在 `?welcome=1` 且没有会话或草稿时出现。
 */
export default function ChatWorkbench({
  chat,
  openDocument,
  onOpenDocument,
  onCloseDocument,
}: {
  chat: UseChatSession;
  openDocument: OpenChatDocument | null;
  onOpenDocument: (artifact: HarnessWorkspaceArtifact, sessionId?: string) => void;
  onCloseDocument: () => void;
}) {
  const { sessionId: routeSessionId, draftAgentId } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const hasConversation = Boolean(routeSessionId || draftAgentId);
  const welcomeRequested = searchParams.get('welcome') === '1';
  const [welcome, setWelcome] = useState(welcomeRequested && !hasConversation);
  const [entering, setEntering] = useState(false);
  const [yardCollapsed, setYardCollapsed] = useState(false);
  const [mobileEmployeesOpen, setMobileEmployeesOpen] = useState(false);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const workspaceRef = useRef<HTMLDivElement>(null);
  const previousPositions = useRef<Array<DOMRect> | null>(null);
  const { isTablet, isMobile } = useWorkbenchBreakpoints();

  const yardAgents = visibleEmployeeAgents(chat.agents, chat.auth?.user).filter(
    (agent) => !agent.is_overall,
  );
  const readTimes = chat.sessionReadTimes ?? {};
  const yardEmployees: YardEmployee[] = yardAgents.map((agent) => {
    const profile = employeeProfile(agent);
    const description = agent.description?.trim() || '';
    const ownSessions = chat.sessions.filter((session) => session.agent_id === agent.id);
    return {
      id: agent.id,
      name: employeeDisplayName(agent),
      artId: artForRole(profile.roleKey, profile.roleName),
      roleName: profile.roleName,
      online: agent.status === 'active',
      intro: description
        ? [description.slice(0, 30), description.slice(30, 60)] as [string, string]
        : [`专注${profile.roleName}工作，`, `协同处理项目任务，让工作信息清晰可追溯。`] as [string, string],
      stats: [
        { value: agentResourceCount(agent, 'knowledge_base'), label: '资料' },
        { value: agentResourceCount(agent, 'general_skill'), label: '技能' },
        { value: agentResourceCount(agent, 'skill'), label: 'SOP' },
      ],
      deskStatus: employeeDeskStatus({
        online: agent.status === 'active',
        running: ownSessions.some((session) => (
          session.status === 'running' || session.status === 'executing'
        )),
        unread: ownSessions.some((session) => sessionHasUnreadReply(session, readTimes, chat.sessionId)),
      }),
    };
  }).sort((left, right) => compareDeskStatus(left.deskStatus ?? 'idle', right.deskStatus ?? 'idle'));

  const yardTeams = chat.teams.map((team) => ({
    id: team.id,
    name: team.name,
    memberIds: team.members.map((member) => member.agent_id),
  }));

  const detailAgent = resolveDetailAgent({
    displayedAgent: chat.displayedAgent,
    displayedTeam: chat.displayedTeam,
    agents: chat.agents,
  });
  const currentYardEmployee = yardEmployees.find((item) => item.id === detailAgent?.id) || null;
  const activeTeamId = chat.displayedTeam?.id || 'all';

  const detailSessions = chat.sessions.filter((session) => (
    chat.displayedTeam
      ? session.team_id === chat.displayedTeam.id
      : chat.displayedAgent && session.agent_id === chat.displayedAgent.id
  ));

  const clearWelcomeQuery = useCallback(() => {
    if (!searchParams.has('welcome')) return;
    const next = new URLSearchParams(searchParams);
    next.delete('welcome');
    setSearchParams(next, { replace: true });
  }, [searchParams, setSearchParams]);

  const enterWorkspace = useCallback(() => {
    if (workspaceRef.current) {
      previousPositions.current = Array.from(
        workspaceRef.current.querySelectorAll('.cw-employee, .cw-empty-desk'),
      ).map((el) => el.getBoundingClientRect());
    }
    setEntering(true);
    setWelcome(false);
    clearWelcomeQuery();
  }, [clearWelcomeQuery]);

  // FLIP：欢迎卡位置 → 工作台位置；会话与详情栏滑入。
  useLayoutEffect(() => {
    if (welcome || !previousPositions.current) return;
    const from = previousPositions.current;
    previousPositions.current = null;
    const workspace = workspaceRef.current;
    if (!workspace) return;
    const reduce = typeof window.matchMedia === 'function'
      && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const animations: Array<Animation> = [];
    const canAnimate = !reduce && typeof window.requestAnimationFrame === 'function'
      && typeof (document.documentElement as HTMLElement & { animate?: unknown }).animate === 'function'
      && !isMobile;
    if (canAnimate) {
      workspace.querySelectorAll('.cw-employee, .cw-empty-desk').forEach((el, index) => {
        const origin = from[index];
        const to = el.getBoundingClientRect();
        if (!origin || !to.width) return;
        animations.push(el.animate(
          [
            {
              transform: `translate(${origin.left - to.left}px, ${origin.top - to.top}px) scale(${origin.width / to.width}, ${origin.height / to.height})`,
            },
            { transform: 'none' },
          ],
          { duration: 500, easing: 'cubic-bezier(0.77, 0, 0.175, 1)' },
        ));
      });
      workspace.querySelectorAll('.cw-chat-panel, .cw-detail-panel').forEach((el) => {
        animations.push(el.animate(
          [{ transform: 'translateX(60px)', opacity: 0 }, { transform: 'none', opacity: 1 }],
          { duration: 300, delay: 200, fill: 'backwards', easing: 'cubic-bezier(0.23, 1, 0.32, 1)' },
        ));
      });
    }
    if (animations.length) {
      Promise.allSettled(animations.map((animation) => animation.finished)).then(() => setEntering(false));
    } else {
      setEntering(false);
    }
    return () => animations.forEach((animation) => animation.cancel());
  }, [welcome, isMobile]);

  // 从会话路由直接进入时不展示欢迎页，并清掉 welcome query，避免刷新又回到欢迎页。
  useEffect(() => {
    if (!routeSessionId && !draftAgentId) return;
    setWelcome(false);
    clearWelcomeQuery();
  }, [routeSessionId, draftAgentId, clearWelcomeQuery]);

  const heading = (
    <ChatHeading
      chat={chat}
      onOpenEmployees={() => setMobileEmployeesOpen(true)}
      onOpenDetails={() => setDetailsOpen(true)}
      onRequestDelete={chat.currentSession ? () => chat.requestDelete(chat.currentSession!) : undefined}
    />
  );
  const emptyState = <WorkbenchEmptyState agent={chat.displayedAgent} yardEmployee={currentYardEmployee} />;

  if (welcome) {
    return (
      <div className="cw-root">
        <WorkbenchHeader chat={chat} />
        <div ref={workspaceRef} className="cw-workspace cw-welcome-workspace">
          <div className="cw-welcome-intro">
            <h1>从一个问题或一项任务开始</h1>
          </div>
          <EmployeeYard
            welcome
            employees={yardEmployees}
            teams={yardTeams}
            defaultTeamId={activeTeamId}
            className="cw-welcome-yard"
            onSelect={(employee) => {
              chat.openAgentWorkspace(employee.id);
              setWelcome(false);
            }}
          />
          <button type="button" className="cw-welcome-start" onClick={enterWorkspace}>
            开始工作
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="cw-root">
      <WorkbenchHeader chat={chat} />
      <div
        ref={workspaceRef}
        className={[
          'cw-workspace',
          yardCollapsed ? 'cw-sidebar-collapsed' : '',
          entering ? 'cw-workspace-entering' : '',
          isTablet ? 'cw-tablet' : '',
          isMobile ? 'cw-mobile' : '',
        ].filter(Boolean).join(' ')}
      >
        <EmployeeYard
          employees={yardEmployees}
          teams={yardTeams}
          defaultTeamId={activeTeamId}
          selectedId={detailAgent?.id}
          collapsed={yardCollapsed}
          onCollapsedChange={setYardCollapsed}
          onSelect={(employee) => chat.openAgentWorkspace(employee.id)}
        />
        <main className="cw-chat-panel">
          {heading}
          <MessageList
            chat={chat}
            variant="workbench"
            className="cw-conversation"
            stackClassName="cw-conversation-stack"
            emptyState={emptyState}
            onOpenDocument={onOpenDocument}
          />
          <Composer chat={chat} variant="workbench" />
          {openDocument && (
            <DocumentOverlay chat={chat} document={openDocument} onClose={onCloseDocument} />
          )}
        </main>
        <EmployeeDetailPanel
          chat={chat}
          detailAgent={detailAgent}
          yardEmployee={currentYardEmployee}
          sessions={detailSessions}
          onOpenDocument={onOpenDocument}
        />
      </div>

      {isMobile && (
        <Sheet open={mobileEmployeesOpen} onOpenChange={setMobileEmployeesOpen}>
          <SheetContent side="left" className="w-[min(420px,92vw)] overflow-y-auto p-0">
            <SheetHeader className="border-b border-[#f0f1f4] px-[16px] py-[14px]">
              <SheetTitle className="text-[16px]">选择数字员工</SheetTitle>
            </SheetHeader>
            <EmployeeYard
              employees={yardEmployees}
              teams={yardTeams}
              defaultTeamId={activeTeamId}
              onSelect={(employee) => {
                chat.openAgentWorkspace(employee.id);
                setMobileEmployeesOpen(false);
              }}
            />
          </SheetContent>
        </Sheet>
      )}
      {isTablet && (
        <Sheet open={detailsOpen} onOpenChange={setDetailsOpen}>
          <SheetContent side="right" className="w-[min(360px,92vw)] overflow-y-auto p-0">
            <SheetHeader className="border-b border-[#f0f1f4] px-[16px] py-[14px]">
              <SheetTitle className="text-[16px]">员工与会话</SheetTitle>
            </SheetHeader>
            <EmployeeDetailPanel
              chat={chat}
              detailAgent={detailAgent}
              yardEmployee={currentYardEmployee}
              sessions={detailSessions}
              onOpenDocument={(artifact, sessionId) => {
                onOpenDocument(artifact, sessionId);
                setDetailsOpen(false);
              }}
            />
          </SheetContent>
        </Sheet>
      )}
    </div>
  );
}
