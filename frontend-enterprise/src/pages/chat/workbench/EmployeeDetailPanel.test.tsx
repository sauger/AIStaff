// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, act } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { api } from '@/api/client';
import { I18nProvider } from '@/i18n';
import type { AgentProfileRead, ChatSession } from '@/types';

import type { UseChatSession } from '../useChatSession';
import EmployeeDetailPanel from './EmployeeDetailPanel';
import { EmployeeAppearanceProvider } from './useEmployeeAppearance';

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

const agent: AgentProfileRead = {
  id: 'agent-1',
  tenant_id: 'tenant_demo',
  name: '项目经理',
  description: '统筹项目目标与团队分工。',
  is_overall: false,
  status: 'active',
  metadata: {},
  resources: [
    { id: 'r1', tenant_id: 't', agent_id: 'agent-1', resource_type: 'knowledge_base', resource_id: 'kb-1', status: 'active', metadata: {}, created_at: '', updated_at: '' },
    { id: 'r2', tenant_id: 't', agent_id: 'agent-1', resource_type: 'general_skill', resource_id: 'sk-1', status: 'active', metadata: {}, created_at: '', updated_at: '' },
  ],
  created_at: '',
  updated_at: '',
};

function session(partial: Partial<ChatSession>): ChatSession {
  return {
    id: partial.id || 'session-1',
    tenant_id: 'tenant_demo',
    status: 'active',
    updated_at: '2026-08-01T00:00:00Z',
    title: '会话一',
    ...partial,
  } as ChatSession;
}

function buildChat(sessions: ChatSession[], activeId?: string): UseChatSession {
  return {
    displayedAgent: agent,
    displayedTeam: null,
    displayedMessages: [],
    sessionReadTimes: {},
    sessionId: activeId || 'session-1',
    agents: [agent],
    openSession: vi.fn(),
    openRename: vi.fn(),
    requestDelete: vi.fn(),
    openDraftForAgent: vi.fn(),
    openAgentConfig: vi.fn(),
    openAdmin: vi.fn(),
    activeConversationId: activeId,
  } as unknown as UseChatSession;
}

function renderPanel(chat: UseChatSession, sessions: ChatSession[], detailAgent: AgentProfileRead | null = agent) {
  return render(
    <I18nProvider>
      <EmployeeAppearanceProvider>
        <EmployeeDetailPanel
          chat={chat}
          detailAgent={detailAgent}
          yardEmployee={null}
          sessions={sessions}
          onOpenDocument={vi.fn()}
        />
      </EmployeeAppearanceProvider>
    </I18nProvider>,
  );
}

describe('EmployeeDetailPanel', () => {
  it('renders the employee profile with resource counts', () => {
    renderPanel(buildChat([]), []);
    expect(screen.getByText('项目经理')).toBeTruthy();
    expect(screen.getByText('资料')).toBeTruthy();
    expect(screen.getByText('技能')).toBeTruthy();
    expect(screen.getByText('新建对话')).toBeTruthy();
  });

  it('lists history sessions with pending-answer and running status', () => {
    const sessions = [
      session({ id: 's1', title: '进行中的会话', status: 'running' }),
      session({ id: 's2', title: '待回答的会话', last_agent_question: '要继续吗？' }),
    ];
    renderPanel(buildChat(sessions, 's1'), sessions);
    expect(screen.getByText('进行中')).toBeTruthy();
    expect(screen.getByText('待回答')).toBeTruthy();
  });

  it('pages history ten sessions at a time', () => {
    const sessions = Array.from({ length: 11 }, (_, index) => session({
      id: `s${index}`,
      title: `会话 ${index}`,
    }));
    renderPanel(buildChat(sessions, 's0'), sessions);
    expect(screen.getByText('会话 0')).toBeTruthy();
    expect(screen.queryByText('会话 10')).toBeNull();
    expect(screen.getByText('第 1 / 2 页')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '下一页' }));
    expect(screen.getByText('会话 10')).toBeTruthy();
    expect(screen.queryByText('会话 0')).toBeNull();
  });

  it('searches history through conversation content', async () => {
    vi.useFakeTimers();
    const get = vi.spyOn(api, 'get').mockResolvedValue({
      items: [session({ id: 's2', title: '周报整理' })],
      total: 1,
    });
    const sessions = [session({ id: 's1', title: '报名系统' }), session({ id: 's2', title: '周报整理' })];
    renderPanel(buildChat(sessions), sessions);
    fireEvent.click(screen.getByRole('button', { name: '搜索历史对话' }));
    fireEvent.change(screen.getByLabelText('搜索会话'), { target: { value: '报名截止' } });
    expect(screen.queryByText('报名系统')).toBeNull();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(300);
    });
    const url = String(get.mock.calls[get.mock.calls.length - 1]?.[0]);
    expect(url).toContain('/api/chat/sessions/search?');
    expect(url).toContain('agent_id=agent-1');
    expect(decodeURIComponent(url)).toContain('报名截止');
    expect(screen.getByText('周报整理')).toBeTruthy();
    expect(screen.queryByText('报名系统')).toBeNull();
  });

  it('searches a team history by team id', async () => {
    vi.useFakeTimers();
    const get = vi.spyOn(api, 'get').mockResolvedValue({ items: [], total: 0 });
    const chat = buildChat([]);
    chat.displayedTeam = {
      id: 'team-1',
      name: '增长团队',
      members: [],
    } as unknown as UseChatSession['displayedTeam'];
    renderPanel(chat, []);
    fireEvent.click(screen.getByRole('button', { name: '搜索历史对话' }));
    fireEvent.change(screen.getByLabelText('搜索会话'), { target: { value: '分工' } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(300);
    });
    const url = String(get.mock.calls[get.mock.calls.length - 1]?.[0]);
    expect(url).toContain('team_id=team-1');
    expect(url).not.toContain('agent_id=');
  });

  it('starts a draft for the detail employee during a team session', () => {
    const chat = buildChat([]);
    chat.displayedTeam = {
      id: 'team-1',
      name: '增长团队',
      members: [],
    } as unknown as UseChatSession['displayedTeam'];
    chat.displayedAgent = null;
    renderPanel(chat, []);
    fireEvent.click(screen.getByRole('button', { name: '新建对话' }));
    expect(chat.openDraftForAgent).toHaveBeenCalledWith('agent-1');
    expect(screen.queryByText('成员数')).toBeNull();
    expect(screen.queryByText('项目领导')).toBeNull();
  });

  it('opens persona configuration for the detail employee', () => {
    const chat = buildChat([]);
    renderPanel(chat, []);
    fireEvent.click(screen.getByRole('button', { name: '配置员工' }));
    expect(chat.openAgentConfig).toHaveBeenCalledWith('agent-1');
    expect(chat.openAdmin).not.toHaveBeenCalled();
  });

  it('opens the session when a history row is selected', () => {
    const chat = buildChat([]);
    renderPanel(chat, [session({ id: 's2', title: '目标会话' })]);
    fireEvent.click(screen.getByText('目标会话'));
    expect(chat.openSession).toHaveBeenCalledWith('s2');
  });
});
