// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { I18nProvider } from '@/i18n';

import WecomAppApprovalTemplatesTab from './WecomAppApprovalTemplatesTab';

const api = {
  get: vi.fn(),
  post: vi.fn(),
};

vi.mock('../../api/client', () => ({
  TENANT_ID: 'tenant_demo',
  api: {
    get: (...args: unknown[]) => api.get(...args),
    post: (...args: unknown[]) => api.post(...args),
  },
}));

const apps = [
  { id: 'wapp_1', name: '审批', is_default: true, status: 'active' },
];

afterEach(() => {
  cleanup();
  api.get.mockReset();
  api.post.mockReset();
});

function renderTab() {
  return render(
    <I18nProvider>
      <MemoryRouter>
        <WecomAppApprovalTemplatesTab apps={apps} preferredAppId={null} />
      </MemoryRouter>
    </I18nProvider>,
  );
}

describe('WecomAppApprovalTemplatesTab', () => {
  it('shows template id and name, and an empty state when WeCom returns nothing', async () => {
    api.get.mockImplementation(async (path: string) => {
      if (String(path).includes('/approval-templates')) {
        return {
          app_id: 'wapp_1',
          app_name: '审批',
          templates: [],
          assignments: [],
          error: '当前应用没有审批接口权限。请在企微后台把该应用加入「审批 - 可调用接口的应用」。',
        };
      }
      return [];
    });

    renderTab();
    expect(await screen.findByTestId('wecom-template-empty')).toBeTruthy();
    expect(screen.getByText(/没有审批接口权限/)).toBeTruthy();
  });

  it('assigns a template to a digital employee', async () => {
    api.get.mockImplementation(async (path: string) => {
      if (String(path).includes('/approval-templates')) {
        return {
          app_id: 'wapp_1',
          app_name: '审批',
          templates: [{ template_id: 'tpl_leave', name: '请假', control_count: null, source: 'catalog' }],
          assignments: [],
          error: null,
          notice: null,
        };
      }
      if (String(path).includes('/api/enterprise/agents')) {
        return [{ id: 'agent_a', name: '小李' }, { id: 'agent_b', name: '小王' }];
      }
      return [];
    });
    api.post.mockResolvedValue({
      assignments: [{ agent_id: 'agent_a', agent_name: '小李', template_id: 'tpl_leave', scenario_key: 'default' }],
    });

    const user = userEvent.setup();
    renderTab();
    expect(await screen.findByText('tpl_leave')).toBeTruthy();
    expect(screen.getByText('请假')).toBeTruthy();
    await user.click(screen.getByRole('button', { name: '分配给数字员工' }));
    await user.click(screen.getByRole('checkbox', { name: '小李' }));
    await user.click(screen.getByRole('button', { name: '保存分配' }));

    expect(api.post).toHaveBeenCalledWith(
      expect.stringContaining('/approval-templates/assignments'),
      expect.objectContaining({
        template_id: 'tpl_leave',
        agent_ids: ['agent_a'],
        scenario_key: 'default',
      }),
    );
  });
});
