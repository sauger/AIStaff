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
});
