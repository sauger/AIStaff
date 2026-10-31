// @vitest-environment jsdom

import { cleanup, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { I18nProvider } from '@/i18n';
import type { EnterpriseAuthUser } from '@/auth';

import CompanyMembersPage from './CompanyMembersPage';

const admin: EnterpriseAuthUser = {
  id: 'user-admin',
  tenant_id: 'tenant_demo',
  username: 'admin',
  role: 'admin',
};

const department = {
  id: 'dept-sales',
  name: '销售',
  parent_id: null,
  path: '/dept-sales',
  sort_order: 0,
  created_at: '',
  updated_at: '',
};

function jsonResponse(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    statusText: 'OK',
    text: async () => JSON.stringify(body ?? {}),
  } as Response;
}

function renderPage() {
  return render(
    <I18nProvider>
      <MemoryRouter>
        <CompanyMembersPage currentUser={admin} />
      </MemoryRouter>
    </I18nProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('CompanyMembersPage', () => {
  it('merges people and digital employees and filters by type', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/org/people')) {
        return jsonResponse({
          people: [
            {
              id: 'person-1',
              tenant_id: 'tenant_demo',
              name: '张三',
              employee_no: 'E001',
              status: 'active',
              primary_user_id: 'user-1',
              primary_user_name: '张三',
              primary_username: 'zhangsan',
              source: 'manual',
              created_at: '',
              updated_at: '',
            },
          ],
        });
      }
      if (url.includes('/org/tree')) {
        return jsonResponse({
          departments: [department],
          people: [
            {
              affiliation_id: 'aff-1',
              person_id: 'person-1',
              name: '张三',
              department_id: 'dept-sales',
              position_title: '销售经理',
              valid_from: '2026-01-01',
              valid_to: null,
            },
          ],
          agents: [],
        });
      }
      if (url.includes('/org/agents')) {
        return jsonResponse({
          agents: [
            {
              agent_id: 'agent-1',
              name: '客户助手',
              status: 'active',
              primary_owner_name: '李四',
              departments: [
                {
                  department_id: 'dept-sales',
                  department_name: '销售',
                  is_primary: true,
                  assignment_role: 'owner_department',
                },
              ],
            },
          ],
        });
      }
      if (url.includes('/org/departments')) {
        return jsonResponse({ departments: [department] });
      }
      if (url.includes('/api/auth/users')) {
        return jsonResponse([
          { id: 'user-1', tenant_id: 'tenant_demo', username: 'zhangsan', display_name: '张三', role: 'member', source: 'web' },
        ]);
      }
      return jsonResponse({});
    }));

    const user = userEvent.setup();
    renderPage();

    expect(await screen.findByText('张三')).toBeTruthy();
    expect(await screen.findByText('客户助手')).toBeTruthy();
    expect(screen.getByText('企业人员')).toBeTruthy();
    expect(screen.getByText('数字员工')).toBeTruthy();

    await user.click(screen.getByRole('tab', { name: /企业人员 1/ }));
    expect(screen.getByText('张三')).toBeTruthy();
    expect(screen.queryByText('客户助手')).toBeNull();

    await user.click(screen.getByRole('tab', { name: /数字员工 1/ }));
    expect(screen.queryByText('张三')).toBeNull();
    expect(screen.getByText('客户助手')).toBeTruthy();

    await user.click(screen.getByRole('tab', { name: /全部 2/ }));
    expect(screen.getByText('张三')).toBeTruthy();
    expect(screen.getByText('客户助手')).toBeTruthy();
  });

  it('filters by search text', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/org/people')) {
        return jsonResponse({
          people: [
            { id: 'person-1', tenant_id: 'tenant_demo', name: '张三', status: 'active', source: 'manual', created_at: '', updated_at: '' },
          ],
        });
      }
      if (url.includes('/org/tree')) {
        return jsonResponse({ departments: [], people: [], agents: [] });
      }
      if (url.includes('/org/agents')) return jsonResponse({ agents: [] });
      if (url.includes('/org/departments')) return jsonResponse({ departments: [] });
      if (url.includes('/api/auth/users')) return jsonResponse([]);
      return jsonResponse({});
    }));

    const user = userEvent.setup();
    renderPage();
    expect(await screen.findByText('张三')).toBeTruthy();
    const search = screen.getByRole('textbox', { name: '筛选成员' });
    await user.type(search, '不存在');
    expect(await screen.findByText('没有符合筛选条件的成员。')).toBeTruthy();
  });

  it('brings back name, email, and userid from a WeCom multi-select', async () => {
    const user = userEvent.setup();
    const writeText = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue(undefined);
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/directory/departments/') && url.includes('/users')) {
        if (url.includes('/departments/16/')) {
          return jsonResponse({
            department_id: 16,
            users: [
              { userid: 'zhangsan', name: '张三', email: 'zhangsan@example.com', department_ids: [16] },
              { userid: 'lisi', name: '李四', email: 'lisi@example.com', department_ids: [16] },
            ],
          });
        }
        return jsonResponse({ department_id: 1, users: [] });
      }
      if (url.includes('/directory/departments')) {
        return jsonResponse({
          app_name: '审批',
          departments: [
            { id: 1, name: '公司', parent_id: 0, order: 1 },
            { id: 16, name: '数字化赋能中心', parent_id: 1, order: 8 },
          ],
        });
      }
      if (url.includes('/org/people')) return jsonResponse({ people: [] });
      if (url.includes('/org/tree')) return jsonResponse({ departments: [], people: [], agents: [] });
      if (url.includes('/org/agents')) return jsonResponse({ agents: [] });
      if (url.includes('/org/departments')) return jsonResponse({ departments: [] });
      if (url.includes('/api/auth/users')) return jsonResponse([]);
      return jsonResponse({});
    }));

    renderPage();
    await user.click(await screen.findByRole('button', { name: '从企微选择' }));
    await user.click(await screen.findByRole('button', { name: '数字化赋能中心' }));
    await user.click(await screen.findByRole('checkbox', { name: '张三 zhangsan' }));
    await user.click(screen.getByRole('checkbox', { name: '李四 lisi' }));
    await user.click(screen.getByRole('button', { name: '确认选择' }));

    const selected = await screen.findByTestId('wecom-selected-people');
    expect(selected.textContent).toContain('张三');
    expect(selected.textContent).toContain('zhangsan@example.com');
    expect(selected.textContent).toContain('zhangsan');
    expect(selected.textContent).toContain('李四');
    expect(selected.textContent).toContain('lisi@example.com');
    expect(selected.textContent).toContain('lisi');
    const fill = screen.getByRole('button', { name: '填入新建人员' }) as HTMLButtonElement;
    expect(fill.disabled).toBe(true);

    await user.click(within(selected).getByRole('button', { name: '复制全部' }));
    expect(writeText).toHaveBeenCalledWith(expect.stringContaining('zhangsan'));
    expect(writeText).toHaveBeenCalledWith(expect.stringContaining('lisi@example.com'));
  });

  it('fills the new person form from a single WeCom selection', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/directory/departments/') && url.includes('/users')) {
        return jsonResponse({
          users: [
            { userid: 'zhangsan', name: '张三', email: 'zhangsan@example.com', department_ids: [16] },
          ],
        });
      }
      if (url.includes('/directory/departments')) {
        return jsonResponse({
          departments: [{ id: 16, name: '数字化赋能中心', parent_id: 0, order: 1 }],
        });
      }
      if (url.includes('/org/people')) return jsonResponse({ people: [] });
      if (url.includes('/org/tree')) return jsonResponse({ departments: [], people: [], agents: [] });
      if (url.includes('/org/agents')) return jsonResponse({ agents: [] });
      if (url.includes('/org/departments')) return jsonResponse({ departments: [] });
      if (url.includes('/api/auth/users')) return jsonResponse([]);
      return jsonResponse({});
    }));

    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole('button', { name: '从企微选择' }));
    await user.click(await screen.findByRole('checkbox', { name: '张三 zhangsan' }));
    await user.click(screen.getByRole('button', { name: '确认选择' }));
    await user.click(await screen.findByRole('button', { name: '填入新建人员' }));

    const dialog = await screen.findByRole('dialog', { name: '新建企业人员' });
    expect(within(dialog).getByDisplayValue('张三')).toBeTruthy();
    expect(within(dialog).getByDisplayValue('zhangsan@example.com')).toBeTruthy();
    expect(within(dialog).getByText('zhangsan')).toBeTruthy();
  });
});
