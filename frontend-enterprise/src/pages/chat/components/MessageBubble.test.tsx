// @vitest-environment jsdom

import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { I18nProvider } from '@/i18n';
import type { AgentProfileRead, ChatMessage, ChatSlashCommand, TeamRead } from '@/types';

import type { UseChatSession } from '../useChatSession';
import MessageBubble, { type MessageRender } from './MessageBubble';

vi.mock('./DashboardView', () => ({
  default: ({ descriptor }: { descriptor: { view_id: string } }) => (
    <div data-testid="dashboard-view">{descriptor.view_id}</div>
  ),
}));

const weatherCommand: ChatSlashCommand = {
  kind: 'skill',
  target: 'weather',
  label: 'weather',
  description: '查询天气',
  command: '/skill weather',
};

function renderSlashMessage(content: string, slashCommands: ChatSlashCommand[] = [weatherCommand]) {
  const item: ChatMessage = {
    id: 'message-1',
    role: 'user',
    content,
    created_at: '2026-08-09T00:00:00Z',
  };
  const messageRender: MessageRender = {
    traceTurnId: 'turn-1',
    summary: null,
    details: [],
    expanded: false,
    showInlineTrace: false,
    visibleContent: content,
    citations: [],
    scheduledDraft: null,
    wecomApprovalConfirm: null,
    wecomApprovalSubmitted: false,
    scheduledTaskPrompt: false,
    attachments: [],
    harnessArtifacts: [],
    statusOnly: false,
  };
  const chat = {
    slashCommands,
    toggleTrace: vi.fn(),
    rateMessage: vi.fn(),
    setActiveCitation: vi.fn(),
    confirmScheduledTask: vi.fn(),
    dismissScheduledTaskDraft: vi.fn(),
    confirmWecomApproval: vi.fn(),
    dismissWecomApproval: vi.fn(),
    removeQueuedTurn: vi.fn(),
    tenantId: 'tenant_demo',
    activeConversationId: 'session-1',
  } as unknown as UseChatSession;
  return render(<MessageBubble chat={chat} item={item} render={messageRender} />);
}

describe('MessageBubble slash command card', () => {
  it('renders a sent slash command as a card beside its request text', () => {
    renderSlashMessage('/skill weather 北京天气如何');

    expect(screen.getByRole('group', { name: '技能 weather' })).toBeTruthy();
    expect(screen.getByText('北京天气如何')).toBeTruthy();
    expect(screen.queryByText('/skill weather 北京天气如何')).toBeNull();
  });

  it('keeps the card when the referenced resource is no longer listed', () => {
    renderSlashMessage('/skill archived_weather 北京天气如何', []);

    expect(screen.getByRole('group', { name: '技能 archived_weather' })).toBeTruthy();
  });
});

describe('MessageBubble responsive sizing', () => {
  it('keeps a short user message out of a shrink-to-fit wrapper', () => {
    renderSlashMessage('确认', []);

    const message = screen.getByText('确认');
    const bubble = message.parentElement?.parentElement;
    expect(bubble?.parentElement?.classList.contains('contents')).toBe(true);
  });
});

describe('MessageBubble team group identity', () => {
  it('shows the project leader avatar and name beside assistant messages', () => {
    const item: ChatMessage = {
      id: 'message-team-1',
      role: 'assistant',
      content: '团队回复',
      created_at: '2026-08-15T00:00:00Z',
    };
    const messageRender: MessageRender = {
      traceTurnId: 'turn-team-1',
      summary: null,
      details: [],
      expanded: false,
      showInlineTrace: false,
      visibleContent: item.content,
      citations: [],
      scheduledDraft: null,
      scheduledTaskPrompt: false,
      attachments: [],
      harnessArtifacts: [],
      statusOnly: false,
      wecomApprovalConfirm: null,
      wecomApprovalSubmitted: false,
    };
    const leader = {
      id: 'agent-leader',
      tenant_id: 'tenant_demo',
      name: '人事',
      is_overall: false,
      status: 'active',
      metadata: { employee_profile: { avatar_text: '人', avatar_preset: 'ops-grid' } },
      resources: [],
      created_at: '2026-08-15T00:00:00Z',
      updated_at: '2026-08-15T00:00:00Z',
    } as AgentProfileRead;
    const chat = {
      displayedTeam: { id: 'team-1', name: '项目组' } as TeamRead,
      displayedAgent: leader,
      slashCommands: [],
      toggleTrace: vi.fn(),
      rateMessage: vi.fn(),
      setActiveCitation: vi.fn(),
      confirmScheduledTask: vi.fn(),
      dismissScheduledTaskDraft: vi.fn(),
      removeQueuedTurn: vi.fn(),
      tenantId: 'tenant_demo',
      activeConversationId: 'session-team-1',
    } as unknown as UseChatSession;

    render(<MessageBubble chat={chat} item={item} render={messageRender} />);

    expect(screen.getByLabelText(/员工头像/)).toBeTruthy();
    expect(screen.getByText('人事')).toBeTruthy();
    expect(screen.getByText('项目领导')).toBeTruthy();
    expect(screen.getByText('团队回复')).toBeTruthy();
  });

  it('shows live team progress and hides feedback until synthesis finishes', () => {
    const item: ChatMessage = {
      id: 'message-team-progress',
      role: 'assistant',
      content: '已收到全部 2 项成员回复。',
      metadata: {
        team_run_id: 'team-run-1',
        team_progress: {
          phase: 'synthesizing',
          completed_tasks: 2,
          total_tasks: 2,
          status_text: '正在整理答案',
        },
      },
      created_at: '2026-08-15T00:00:00Z',
    };
    const messageRender: MessageRender = {
      traceTurnId: 'turn-team-progress',
      summary: null,
      details: [],
      expanded: false,
      showInlineTrace: false,
      visibleContent: item.content,
      citations: [],
      scheduledDraft: null,
      scheduledTaskPrompt: false,
      attachments: [],
      harnessArtifacts: [],
      statusOnly: false,
      wecomApprovalConfirm: null,
      wecomApprovalSubmitted: false,
    };
    const chat = {
      displayedTeam: { id: 'team-1', name: '项目组' } as TeamRead,
      slashCommands: [],
      toggleTrace: vi.fn(),
      rateMessage: vi.fn(),
      setActiveCitation: vi.fn(),
      confirmScheduledTask: vi.fn(),
      dismissScheduledTaskDraft: vi.fn(),
      removeQueuedTurn: vi.fn(),
      tenantId: 'tenant_demo',
      activeConversationId: 'session-team-1',
    } as unknown as UseChatSession;

    const { container } = render(<MessageBubble chat={chat} item={item} render={messageRender} />);

    expect(screen.getByText('已收到全部 2 项成员回复。')).toBeTruthy();
    expect(screen.getByRole('status', { name: '正在整理答案' })).toBeTruthy();
    expect(container.querySelector('button[aria-label="点赞"]')).toBeNull();
  });
});

describe('MessageBubble workbench identity', () => {
  it('renders the employee portrait and sender above a workbench reply', () => {
    const item: ChatMessage = {
      id: 'message-workbench-1',
      role: 'assistant',
      content: '整理分工草稿，供你确认。',
      created_at: '2026-11-18T00:00:00Z',
    };
    const messageRender: MessageRender = {
      traceTurnId: 'turn-workbench-1',
      summary: null,
      details: [],
      expanded: false,
      showInlineTrace: false,
      visibleContent: item.content,
      citations: [],
      scheduledDraft: null,
      scheduledTaskPrompt: false,
      attachments: [],
      harnessArtifacts: [],
      statusOnly: false,
      wecomApprovalConfirm: null,
      wecomApprovalSubmitted: false,
    };
    const agent = {
      id: 'agent-project-manager',
      tenant_id: 'tenant_demo',
      name: '项目经理',
      is_overall: false,
      status: 'active',
      metadata: { employee_profile: { role_key: 'project_manager', role_name: '项目推进经理' } },
      resources: [],
      created_at: '2026-11-18T00:00:00Z',
      updated_at: '2026-11-18T00:00:00Z',
    } as AgentProfileRead;
    const chat = {
      displayedAgent: agent,
      displayedTeam: null,
      slashCommands: [],
      toggleTrace: vi.fn(),
      rateMessage: vi.fn(),
      setActiveCitation: vi.fn(),
      confirmScheduledTask: vi.fn(),
      dismissScheduledTaskDraft: vi.fn(),
      removeQueuedTurn: vi.fn(),
      tenantId: 'tenant_demo',
      activeConversationId: 'session-workbench-1',
    } as unknown as UseChatSession;

    const { container } = render(
      <MessageBubble
        chat={chat}
        item={item}
        render={messageRender}
        variant="workbench"
        showAssistantSender
      />,
    );

    expect(container.querySelector('.cw-message-avatar')).toBeTruthy();
    expect(container.querySelector('.cw-message-avatar-art svg')).toBeTruthy();
    expect(screen.getByText('项目经理')).toBeTruthy();
    expect(screen.getByText('整理分工草稿，供你确认。')).toBeTruthy();
  });
});

describe('MessageBubble inline dashboard', () => {
  it('renders the dashboard outside the collapsed execution trace', () => {
    const item: ChatMessage = {
      id: 'message-dashboard-1',
      role: 'assistant',
      content: '已生成大屏',
      created_at: '2026-11-05T00:00:00Z',
    };
    const messageRender: MessageRender = {
      traceTurnId: 'turn-dashboard-1',
      summary: { text: '执行记录', state: 'completed' },
      details: [
        {
          id: 'harness_dashboard_turn-1_view-1',
          kind: 'tool',
          text: '展示数据大屏 经营看板',
          state: 'completed',
          dashboard: {
            view_id: 'dashview_1',
            tenant_id: 'tenant_demo',
            title: '经营看板',
            kpi_count: 2,
            chart_count: 1,
          },
        },
      ],
      expanded: false,
      showInlineTrace: true,
      visibleContent: '已生成大屏',
      citations: [],
      scheduledDraft: null,
      wecomApprovalConfirm: null,
      wecomApprovalSubmitted: false,
      scheduledTaskPrompt: false,
      attachments: [],
      harnessArtifacts: [],
      statusOnly: false,
      dashboards: [
        {
          view_id: 'dashview_1',
          tenant_id: 'tenant_demo',
          title: '经营看板',
          kpi_count: 2,
          chart_count: 1,
        },
      ],
    };
    const chat = {
      toggleTrace: vi.fn(),
      rateMessage: vi.fn(),
      setActiveCitation: vi.fn(),
      confirmScheduledTask: vi.fn(),
      dismissScheduledTaskDraft: vi.fn(),
      confirmWecomApproval: vi.fn(),
      dismissWecomApproval: vi.fn(),
      removeQueuedTurn: vi.fn(),
      tenantId: 'tenant_demo',
      activeConversationId: 'session-1',
    } as unknown as UseChatSession;

    render(
      <I18nProvider>
        <MessageBubble chat={chat} item={item} render={messageRender} />
      </I18nProvider>,
    );

    expect(screen.getByTestId('dashboard-view').textContent).toBe('dashview_1');
    expect(screen.getByText('已生成大屏')).toBeTruthy();
  });
});
