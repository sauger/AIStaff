// @vitest-environment jsdom

import { cleanup, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { I18nProvider } from '@/i18n';
import { SidebarProvider } from '@/components/ui/sidebar';
import { TooltipProvider } from '@/components/ui/tooltip';
import type { AgentProfileRead, TeamRead } from '@/types';

import AppSidebar from './AppSidebar';

const agent: AgentProfileRead = {
  id: 'agent-1',
  tenant_id: 'tenant_demo',
  name: '小艾',
  is_overall: false,
  status: 'active',
  metadata: {},
  resources: [],
  created_at: '2026-08-01T00:00:00Z',
  updated_at: '2026-08-01T00:00:00Z',
};

const team: TeamRead = {
  id: 'team-1',
  tenant_id: 'tenant_demo',
  name: '增长团队',
  description: '',
  owner_user_id: 'user-1',
  config: {},
  status: 'active',
  members: [],
  created_at: '2026-08-01T00:00:00Z',
  updated_at: '2026-08-01T00:00:00Z',
};

function stubRadixPointerApis() {
  if (!window.ResizeObserver) {
    window.ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    } as unknown as typeof ResizeObserver;
  }
  if (!window.matchMedia) {
    window.matchMedia = ((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    })) as typeof window.matchMedia;
  }
  if (!Element.prototype.hasPointerCapture) {
    Element.prototype.hasPointerCapture = () => false;
  }
  if (!Element.prototype.releasePointerCapture) {
    Element.prototype.releasePointerCapture = () => {};
  }
  if (!Element.prototype.scrollIntoView) {
    Element.prototype.scrollIntoView = () => {};
  }
}

function renderSidebar(props: {
  selectedAgentId: string;
  onSelectAgent?: (value: string) => void;
  scopeTeams?: TeamRead[];
  isAdmin?: boolean;
  selected?: string;
  onNavigate?: (route: string) => void;
}) {
  return render(
    <I18nProvider>
      <TooltipProvider>
        <SidebarProvider>
          <AppSidebar
            selected={props.selected ?? '/enterprise/dashboard'}
            onNavigate={props.onNavigate ?? (() => {})}
            isAdmin={props.isAdmin ?? false}
            scopeAgents={[agent]}
            scopeTeams={props.scopeTeams ?? [team]}
            selectedAgentId={props.selectedAgentId}
            onSelectAgent={props.onSelectAgent ?? (() => {})}
            onOpenChat={() => {}}
          />
        </SidebarProvider>
      </TooltipProvider>
    </I18nProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

beforeEach(() => {
  stubRadixPointerApis();
  window.localStorage.removeItem('ultrarag_enterprise_company_nav_expanded');
});

describe('AppSidebar agent switcher team group', () => {
  it('lists teams under a team group and emits the team scope value on select', async () => {
    const user = userEvent.setup();
    const onSelectAgent = vi.fn();
    renderSidebar({ selectedAgentId: 'agent-1', onSelectAgent });

    await user.click(screen.getByLabelText('切换当前员工'));
    const menu = await screen.findByRole('menu');
    // 分组标签与团队项上的 Badge 都写作「团队」。
    expect(within(menu).getAllByText('团队').length).toBeGreaterThanOrEqual(2);
    const teamItem = within(menu)
      .getAllByRole('menuitem')
      .find((item) => item.textContent?.includes('增长团队'));
    expect(teamItem).toBeTruthy();

    await user.click(teamItem!);
    expect(onSelectAgent).toHaveBeenCalledWith('team:team-1');
  });

  it('shows the team name and team caption when a team scope is selected', () => {
    renderSidebar({ selectedAgentId: 'team:team-1' });

    const trigger = screen.getByLabelText('切换当前员工');
    expect(within(trigger).getByText('当前团队')).toBeTruthy();
    expect(within(trigger).getByText('增长团队')).toBeTruthy();
  });

  it('keeps enterprise management in admin navigation, not employee capabilities', () => {
    renderSidebar({ selectedAgentId: 'agent-1' });
    expect(screen.queryByText('企业管理')).toBeNull();
    cleanup();
    renderSidebar({ selectedAgentId: 'agent-1', isAdmin: true });
    expect(screen.getByText('企业管理')).toBeTruthy();
    expect(screen.getByText('员工能力')).toBeTruthy();
    expect(screen.getByText('通讯录')).toBeTruthy();
    const capability = screen.getByText('员工能力').parentElement;
    expect(capability?.textContent?.includes('企业管理')).toBe(false);
    expect(screen.getByRole('button', { name: '企业管理', expanded: true })).toBeTruthy();
    expect(screen.getByText('组织架构')).toBeTruthy();
    expect(screen.getByText('成员')).toBeTruthy();
    expect(screen.getByText('企微应用')).toBeTruthy();
  });

  it('folds company children without navigating and restores a deep link', async () => {
    const user = userEvent.setup();
    const onNavigate = vi.fn();
    renderSidebar({ selectedAgentId: 'agent-1', isAdmin: true, onNavigate });

    await user.click(screen.getByRole('button', { name: '企业管理' }));
    expect(onNavigate).not.toHaveBeenCalled();
    expect(screen.queryByText('组织架构')).toBeNull();
    expect(screen.queryByText('企微应用')).toBeNull();
    expect(window.localStorage.getItem('ultrarag_enterprise_company_nav_expanded')).toBe('0');
    cleanup();

    renderSidebar({ selectedAgentId: 'agent-1', isAdmin: true, onNavigate });
    expect(screen.queryByText('企微应用')).toBeNull();
    cleanup();

    window.localStorage.setItem('ultrarag_enterprise_company_nav_expanded', '0');
    renderSidebar({
      selectedAgentId: 'agent-1',
      isAdmin: true,
      onNavigate,
      selected: '/enterprise/company/wecom-apps',
    });
    expect(screen.getByText('企微应用')).toBeTruthy();
    expect(window.localStorage.getItem('ultrarag_enterprise_company_nav_expanded')).toBe('1');
    await user.click(screen.getByText('企微应用'));
    expect(onNavigate).toHaveBeenCalledWith('/enterprise/company/wecom-apps');
  });

  it('places the file cabinet and mail next to knowledge in capability navigation', () => {
    renderSidebar({ selectedAgentId: 'agent-1' });
    expect(screen.getByText('知识库')).toBeTruthy();
    expect(screen.getByText('文件柜')).toBeTruthy();
    expect(screen.getByText('邮件')).toBeTruthy();
    expect(screen.getByText('通讯录')).toBeTruthy();
    expect(screen.getByText('密钥')).toBeTruthy();
    expect(screen.getByText('认证配置')).toBeTruthy();
  });

  it('places 接入渠道 between 员工档案 and 定时任务 in profile navigation', () => {
    renderSidebar({ selectedAgentId: 'agent-1' });
    const labels = ['员工档案', '接入渠道', '定时任务', '记忆', '对话日志'];
    const nodes = labels.map((label) => screen.getByText(label));
    for (let i = 0; i < nodes.length - 1; i += 1) {
      expect(
        nodes[i].compareDocumentPosition(nodes[i + 1]) & Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
    }
  });

  it('falls back to a bare team label when the selected team is unknown', () => {
    renderSidebar({ selectedAgentId: 'team:missing', scopeTeams: [team] });

    const trigger = screen.getByLabelText('切换当前员工');
    expect(within(trigger).getByText('当前团队')).toBeTruthy();
    expect(within(trigger).getByText('团队')).toBeTruthy();
  });
});

describe('AppSidebar chat variant group conversations', () => {
  const leaderAgent: AgentProfileRead = { ...agent, id: 'agent-tl', name: '队长' };
  const teamWithLeader: TeamRead = {
    ...team,
    members: [
      {
        id: 'member-1',
        team_id: 'team-1',
        agent_id: 'agent-tl',
        role: 'leader',
        agent_name: '队长',
        created_at: '2026-08-01T00:00:00Z',
      },
      {
        id: 'member-2',
        team_id: 'team-1',
        agent_id: 'agent-1',
        role: 'member',
        agent_name: '小艾',
        created_at: '2026-08-01T00:00:00Z',
      },
    ],
  };

  function renderChatSidebar(scopeTeams: TeamRead[] = [teamWithLeader]) {
    const sessions = [
      {
        id: 'session-tl',
        tenant_id: 'tenant_demo',
        agent_id: 'agent-tl',
        team_id: 'team-1',
        title: '团队 增长团队 · TL 对话',
        status: 'active',
        updated_at: '2026-08-01T00:00:00Z',
      },
      {
        id: 'session-member',
        tenant_id: 'tenant_demo',
        agent_id: 'agent-1',
        title: '写公告',
        status: 'active',
        updated_at: '2026-08-01T00:00:00Z',
      },
    ];
    return render(
      <I18nProvider>
        <TooltipProvider>
          <SidebarProvider>
            <AppSidebar
              variant="chat"
              sessions={sessions}
              agents={[agent, leaderAgent]}
              scopeTeams={scopeTeams}
              sessionFilter="all"
              onSessionFilterChange={() => {}}
              sessionFilterOptions={[{ value: 'all', label: '全部员工 · 2' }]}
              isSessionUnread={() => false}
              onOpenSession={() => {}}
              onOpenGallery={() => {}}
              onRenameSession={() => {}}
              onDeleteSession={() => {}}
              onOpenAdmin={() => {}}
            />
          </SidebarProvider>
        </TooltipProvider>
      </I18nProvider>,
    );
  }

  it('renders employee sessions and team groups as different conversation types', () => {
    renderChatSidebar();

    expect(screen.getByLabelText('群聊')).toBeTruthy();
    const groupRow = screen.getByText('增长团队').closest('[role="button"]');
    expect(groupRow?.textContent).toContain('2 位成员 · 团队群聊');
    const employeeRow = screen.getByText('写公告').closest('[role="button"]');
    expect(employeeRow?.textContent).not.toContain('群聊');
  });

  it('keeps a group label while the team roster is still loading', () => {
    renderChatSidebar([]);

    expect(screen.getByText('增长团队')).toBeTruthy();
    expect(screen.getByLabelText('群聊')).toBeTruthy();
  });

  it('shows the employee name in a styled tooltip on avatar hover', async () => {
    const user = userEvent.setup();
    renderChatSidebar();

    const employeeRow = screen.getByText('写公告').closest('[role="button"]');
    const avatar = employeeRow?.querySelector('span');
    expect(avatar).toBeTruthy();
    await user.hover(avatar as Element);

    const tooltip = await screen.findByRole('tooltip');
    expect(tooltip.textContent).toContain('小艾');
  });
});
