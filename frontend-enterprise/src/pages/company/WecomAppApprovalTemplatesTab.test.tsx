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
  delete: vi.fn(),
};

vi.mock('../../api/client', () => ({
  TENANT_ID: 'tenant_demo',
  api: {
    get: (...args: unknown[]) => api.get(...args),
    post: (...args: unknown[]) => api.post(...args),
    delete: (...args: unknown[]) => api.delete(...args),
  },
}));

const apps = [
  { id: 'wapp_1', name: '审批', is_default: true, status: 'active' },
];

afterEach(() => {
  cleanup();
  api.get.mockReset();
  api.post.mockReset();
  api.delete.mockReset();
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
    expect(screen.getByText('用模板 ID 核对')).toBeTruthy();
    expect(screen.getByTestId('wecom-template-assignments')).toBeTruthy();
    expect(screen.queryByText(/gettemplatelist/)).toBeNull();
  });

  it('shows an assigned template and unassign when the visible list is empty', async () => {
    api.get.mockImplementation(async (path: string) => {
      if (String(path).includes('/approval-templates')) {
        return {
          app_id: 'wapp_1',
          app_name: '审批',
          templates: [],
          assignments: [{
            agent_id: 'agent_a',
            agent_name: '小李',
            template_id: 'tpl_purchase',
            template_name: '采购合同审批',
            scenario_key: 'default',
          }],
          error: '读取企微审批模板失败：/oa/gettemplatelist',
          notice: null,
        };
      }
      if (String(path).includes('/api/enterprise/agents')) {
        return [{ id: 'agent_a', name: '小李' }];
      }
      return [];
    });
    api.post.mockResolvedValue({ assignments: [] });

    const user = userEvent.setup();
    renderTab();
    expect((await screen.findAllByText('tpl_purchase')).length).toBeGreaterThan(0);
    expect(screen.getAllByText('采购合同审批').length).toBeGreaterThan(0);
    expect(screen.getByTestId('wecom-template-assignments')).toBeTruthy();
    expect(screen.queryByText(/gettemplatelist/)).toBeNull();
    expect(screen.queryByTestId('wecom-template-empty')).toBeNull();
    await user.click(screen.getByRole('button', { name: '取消分配 小李' }));
    expect(api.post).toHaveBeenCalledWith(
      expect.stringContaining('/approval-templates/assignments/remove'),
      expect.objectContaining({
        template_id: 'tpl_purchase',
        agent_ids: ['agent_a'],
      }),
    );
  });

  it('saves a verified template and can remove the saved copy', async () => {
    let persisted = false;
    api.get.mockImplementation(async (path: string) => {
      if (String(path).includes('/approval-templates')) {
        return persisted
          ? {
            app_id: 'wapp_1',
            app_name: '审批',
            templates: [{
              template_id: 'tpl_contract',
              name: '采购合同审批',
              control_count: 3,
              source: 'saved',
            }],
            assignments: [],
            error: null,
            notice: null,
          }
          : {
            app_id: 'wapp_1',
            app_name: '审批',
            templates: [],
            assignments: [],
            error: null,
            notice: null,
          };
      }
      return [];
    });
    api.post.mockImplementation(async (path: string) => {
      if (String(path).includes('/lookup')) {
        return {
          template: {
            template_id: 'tpl_contract',
            name: '采购合同审批',
            control_count: 3,
            source: 'lookup',
          },
          error: null,
        };
      }
      if (String(path).includes('/saved')) {
        persisted = true;
        return {
          template: {
            template_id: 'tpl_contract',
            name: '采购合同审批',
            control_count: 3,
            source: 'saved',
          },
          error: null,
        };
      }
      return {};
    });
    api.delete.mockResolvedValue({ ok: true });

    const user = userEvent.setup();
    renderTab();
    expect(await screen.findByTestId('wecom-template-empty')).toBeTruthy();
    await user.type(screen.getByRole('textbox', { name: '模板 ID' }), 'tpl_contract');
    await user.click(screen.getByRole('button', { name: '核对' }));
    expect(await screen.findByTestId('wecom-template-verified')).toBeTruthy();
    expect(screen.getByText('采购合同审批')).toBeTruthy();
    await user.click(screen.getByRole('button', { name: '保存模板' }));
    expect(api.post).toHaveBeenCalledWith(
      expect.stringContaining('/approval-templates/saved'),
      expect.objectContaining({ template_id: 'tpl_contract' }),
    );
    await user.click(await screen.findByRole('button', { name: '取消保存' }));
    expect(api.delete).toHaveBeenCalledWith(
      expect.stringContaining('/approval-templates/saved'),
    );
    expect(String(api.delete.mock.calls[0][0])).toContain('template_id=tpl_contract');
  });

  it('shows the office-template mismatch when lookup fails', async () => {
    api.get.mockImplementation(async (path: string) => {
      if (String(path).includes('/approval-templates')) {
        return {
          app_id: 'wapp_1',
          app_name: '审批',
          templates: [],
          assignments: [],
          error: null,
        };
      }
      return [];
    });
    api.post.mockResolvedValue({
      template: null,
      error: '企微未找到该审批模板（301025）。请使用办公「审批」系统应用里的模板 ID。自建应用「审批接口」编辑页上的 ID 不能用于核对或代发。',
    });

    const user = userEvent.setup();
    renderTab();
    await screen.findByRole('button', { name: '核对' });
    await user.type(screen.getByRole('textbox', { name: '模板 ID' }), 'self-built-id');
    await user.click(screen.getByRole('button', { name: '核对' }));
    const lookupError = await screen.findByTestId('wecom-template-lookup-error');
    expect(lookupError.textContent || '').toContain('301025');
    expect(lookupError.textContent || '').toContain('审批接口');
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
