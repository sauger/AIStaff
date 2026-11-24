// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { I18nProvider } from '@/i18n';
import type { AgentProfileRead, ChatSession } from '@/types';

import type { UseChatSession } from '../useChatSession';
import ChatWorkbench from './ChatWorkbench';

afterEach(cleanup);

const agent: AgentProfileRead = {
  id: 'agent-1',
  tenant_id: 'tenant_demo',
  name: '项目经理',
  is_overall: false,
  status: 'active',
  metadata: { role_key: 'project-board' },
  resources: [],
  created_at: '',
  updated_at: '',
};

function buildChat(): UseChatSession {
  return {
    auth: { user: { id: 'user-1', tenant_id: 'tenant_demo', username: 'qianya', role: 'admin' } },
    agents: [agent],
    teams: [],
    sessions: [] as ChatSession[],
    sessionId: '',
    activeConversationId: 'draft:agent-1',
    displayedAgent: agent,
    displayedTeam: null,
    displayedProfile: null,
    displayedMessages: [],
    emptyRoleSummary: '',
    emptyProfileTags: [],
    emptyStats: [],
    turnTraceRef: { current: new Map() },
    uiConfig: {},
    currentStream: { turnId: null },
    runningTurn: null,
    expandedTraceIds: [],
    collapsedTraceIds: [],
    isCurrentStreamingTrace: () => false,
    dismissedDraftMessageIds: [],
    createdScheduledTasks: {},
    currentScheduledDraft: null,
    hasVisibleMessageScheduledDraft: false,
    confirmScheduledTask: vi.fn(),
    dismissScheduledTaskDraft: vi.fn(),
    chatMessagesRef: { current: null },
    handleChatMessagesScroll: () => {},
    SHOW_DEBUG: false,
    lastTurn: null,
    setActiveCitation: vi.fn(),
    tenantId: 'tenant_demo',
    input: '',
    setInput: vi.fn(),
    slashCommands: [],
    composerAttachments: [],
    composerDragActive: false,
    composerPlusOpen: false,
    setComposerPlusOpen: vi.fn(),
    composerIntent: null,
    setComposerIntent: vi.fn(),
    readyComposerAttachments: [],
    uploadingComposerAttachment: false,
    currentSessionRunning: false,
    composerActive: true,
    showComposerAvatar: false,
    enabledModelConfigs: [],
    selectedModelConfig: null,
    changeModelConfig: vi.fn(),
    showModelSetupNotice: false,
    modelSetupNoticeText: '',
    canConfigureModels: false,
    setModelSetupOpen: vi.fn(),
    isComposing: false,
    setIsComposing: () => {},
    fileInputRef: { current: null },
    send: vi.fn(),
    abortStream: vi.fn(),
    handleComposerPaste: () => {},
    handleComposerFileChange: () => {},
    handleComposerDragEnter: () => {},
    handleComposerDragOver: () => {},
    handleComposerDragLeave: () => {},
    handleComposerDrop: () => {},
    removeComposerAttachment: () => {},
    handleComposerPlusAction: vi.fn(),
    openDraftForAgent: vi.fn(),
    openAgentWorkspace: vi.fn(),
    openAgentConfig: vi.fn(),
    openWorkbench: vi.fn(),
    openWelcome: vi.fn(),
    openSession: vi.fn(),
    openRename: vi.fn(),
    requestDelete: vi.fn(),
    openGallery: vi.fn(),
    openHandoffInbox: vi.fn(),
    openAdmin: vi.fn(),
    logout: vi.fn(),
    currentSession: null,
  } as unknown as UseChatSession;
}

function LocationEcho() {
  const location = useLocation();
  return <div data-testid="location">{`${location.pathname}${location.search}`}</div>;
}

function renderWorkbench(
  route: string,
  chat: UseChatSession = buildChat(),
  matches: (query: string) => boolean = () => false,
) {
  window.matchMedia = ((query: string) => ({
    matches: matches(query),
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
  return render(
    <MemoryRouter initialEntries={[route]}>
      <I18nProvider>
        <LocationEcho />
        <Routes>
          <Route path="/workspace/chat" element={workbenchElement(chat)} />
          <Route path="/workspace/chat/:sessionId" element={workbenchElement(chat)} />
          <Route path="/workspace/chat/draft/:draftAgentId" element={workbenchElement(chat)} />
        </Routes>
      </I18nProvider>
    </MemoryRouter>,
  );
}

function workbenchElement(chat: UseChatSession) {
  return (
    <ChatWorkbench
      chat={chat}
      openDocument={null}
      onOpenDocument={vi.fn()}
      onCloseDocument={vi.fn()}
    />
  );
}

describe('ChatWorkbench', () => {
  it('opens the three-pane workspace on the chat root', () => {
    renderWorkbench('/workspace/chat');
    expect(screen.queryByText('从一个问题或一项任务开始')).toBeNull();
    expect(screen.getByText('和项目经理开始协作')).toBeTruthy();
    expect(screen.getByLabelText('数字员工')).toBeTruthy();
    expect(screen.queryByRole('button', { name: '切换语言' })).toBeNull();
  });

  it('keeps the welcome screen behind welcome=1', () => {
    renderWorkbench('/workspace/chat?welcome=1');
    expect(screen.getByText('从一个问题或一项任务开始')).toBeTruthy();
    expect(screen.getByRole('button', { name: '开始工作' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '开始工作' }));
    expect(screen.getByText('和项目经理开始协作')).toBeTruthy();
    expect(screen.getByText('新建对话')).toBeTruthy();
    expect(screen.getByTestId('location').textContent).toBe('/workspace/chat');
  });

  it('skips the welcome screen when routed straight to a session', () => {
    renderWorkbench('/workspace/chat/session-1');
    expect(screen.queryByText('从一个问题或一项任务开始')).toBeNull();
  });

  it('hides the overall agent and shows an inactive employee as offline', () => {
    const chat = buildChat();
    chat.agents = [
      agent,
      { ...agent, id: 'overall', name: '整体智能体', is_overall: true },
      { ...agent, id: 'paused', name: '停用员工', status: 'inactive' },
    ];
    renderWorkbench('/workspace/chat', chat);
    expect(screen.getByRole('button', { name: '项目经理' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: '整体智能体' })).toBeNull();
    const offline = screen.getByRole('button', { name: '停用员工离线' });
    expect(offline.className).toContain('is-offline');
    expect(offline.querySelector('.cw-work-scene')).toBeNull();
  });

  it('shows a laptop while work is running and a document when the result is unread', () => {
    const chat = buildChat();
    const teammate = { ...agent, id: 'agent-2', name: '商务总监' };
    chat.agents = [agent, teammate];
    chat.sessionReadTimes = {};
    chat.sessions = [
      {
        id: 'running-1',
        tenant_id: 'tenant_demo',
        agent_id: 'agent-1',
        status: 'running',
        updated_at: '2026-08-02T00:00:00Z',
      },
      {
        id: 'done-1',
        tenant_id: 'tenant_demo',
        agent_id: 'agent-2',
        status: 'active',
        summary: '方案已整理好',
        updated_at: '2026-08-03T00:00:00Z',
      },
    ] as UseChatSession['sessions'];
    renderWorkbench('/workspace/chat', chat);
    expect(screen.getByRole('img', { name: '项目经理正在使用电脑工作' })).toBeTruthy();
    const unread = screen.getByRole('img', { name: '商务总监有未读完成结果' });
    expect(unread.getAttribute('data-result-ready')).toBe('true');
    expect(unread.querySelector('.summon-wave')).toBeTruthy();
    expect(unread.querySelector('.result-paper')).toBeTruthy();
  });

  it('shows the team leader and team filter for a team session', () => {
    const leader = { ...agent, id: 'leader-1', name: '项目经理' };
    const outsider = { ...agent, id: 'other-1', name: '测试工程师' };
    const team = {
      id: 'team-1',
      tenant_id: 'tenant_demo',
      name: '增长团队',
      owner_user_id: 'user-1',
      config: {},
      status: 'active',
      members: [
        { id: 'm1', team_id: 'team-1', agent_id: 'leader-1', role: 'leader', created_at: '' },
      ],
      created_at: '',
      updated_at: '',
    };
    const chat = buildChat();
    chat.agents = [leader, outsider];
    chat.teams = [team];
    chat.displayedTeam = team;
    chat.displayedAgent = outsider;
    chat.currentSession = {
      id: 'session-1',
      tenant_id: 'tenant_demo',
      status: 'active',
      updated_at: '2026-08-01T00:00:00Z',
      title: '增长团队 · TL 对话',
      team_id: 'team-1',
    } as ChatSession;
    renderWorkbench('/workspace/chat/session-1', chat);
    expect(screen.getByText('1名成员')).toBeTruthy();
    expect(screen.getAllByText('增长团队').length).toBeGreaterThan(0);
    expect(screen.queryByRole('button', { name: '测试工程师' })).toBeNull();
    expect(screen.queryByText('成员数')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '新建对话' }));
    expect(chat.openDraftForAgent).toHaveBeenCalledWith('leader-1');
  });

  it('opens the employee workspace when a workstation is clicked', () => {
    const chat = buildChat();
    renderWorkbench('/workspace/chat', chat);
    fireEvent.click(screen.getByRole('button', { name: '项目经理' }));
    expect(chat.openAgentWorkspace).toHaveBeenCalledWith('agent-1');
    expect(chat.openDraftForAgent).not.toHaveBeenCalled();
  });

  it('opens the shared detail drawer on tablet and phone widths', () => {
    renderWorkbench(
      '/workspace/chat',
      buildChat(),
      (query) => query.includes('1023') || query.includes('767'),
    );
    expect(screen.queryByRole('button', { name: '打开会话列表' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '打开员工详情和会话', hidden: true }));
    expect(screen.getByRole('dialog', { name: '员工与会话' })).toBeTruthy();
  });
});
