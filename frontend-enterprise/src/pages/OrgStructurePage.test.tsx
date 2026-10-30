// @vitest-environment jsdom

import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { I18nProvider } from '@/i18n';
import type { EnterpriseAuthUser } from '@/auth';

import OrgStructurePage from './OrgStructurePage';

const adminUser: EnterpriseAuthUser = {
  id: 'user-admin',
  tenant_id: 'tenant_demo',
  username: 'admin',
  role: 'admin',
};

const department = {
  id: 'dept-rd',
  name: '研发',
  parent_id: null,
  path: '/dept-rd',
  sort_order: 0,
  created_at: '2026-10-28T00:00:00Z',
  updated_at: '2026-10-28T00:00:00Z',
};

const marketDepartment = {
  id: 'dept-market',
  name: '市场',
  parent_id: null,
  path: '/dept-market',
  sort_order: 1,
  created_at: '2026-10-28T00:00:00Z',
  updated_at: '2026-10-28T00:00:00Z',
};

const person = {
  id: 'person-1',
  tenant_id: 'tenant_demo',
  name: '张三',
  employee_no: 'E001',
  status: 'active',
  source: 'manual',
  created_at: '2026-10-28T00:00:00Z',
  updated_at: '2026-10-28T00:00:00Z',
};

const agent = {
  agent_id: 'agent-1',
  name: '客户助手',
  status: 'active',
  primary_owner_name: '李四',
  departments: [
    {
      department_id: 'dept-rd',
      department_name: '研发',
      is_primary: true,
      assignment_role: 'owner_department',
    },
  ],
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
        <OrgStructurePage currentUser={adminUser} />
      </MemoryRouter>
    </I18nProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('OrgStructurePage', () => {
  it('lists human and digital employee nodes and saves a department', async () => {
    const user = userEvent.setup();
    const calls: { url: string; method?: string; body?: string }[] = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({
        url,
        method: init?.method,
        body: typeof init?.body === 'string' ? init.body : undefined,
      });
      if (url.includes('/org/settings')) {
        return jsonResponse({ tenant_id: 'tenant_demo', enterprise_name: 'Demo Enterprise' });
      }
      if (url.includes('/org/tree')) {
        return jsonResponse({
          departments: [department, marketDepartment],
          people: [
            {
              affiliation_id: 'aff-1',
              person_id: 'person-1',
              name: '张三',
              department_id: 'dept-rd',
              position_title: '销售经理',
              valid_from: '2026-01-01',
              valid_to: null,
            },
          ],
          agents: [
            {
              agent_id: 'agent-1',
              name: '客户助手',
              department_id: 'dept-rd',
              status: 'active',
              is_primary: true,
              assignment_role: 'owner_department',
              primary_owner_name: '李四',
            },
          ],
        });
      }
      if (url.includes('/departments/dept-rd/people')) {
        return jsonResponse({
          department_id: 'dept-rd',
          as_of: '2026-10-28',
          people: [{ affiliation_id: 'aff-1', person_id: 'person-1', person_name: '张三', department_id: 'dept-rd', position_title: '销售经理', valid_from: '2026-01-01', valid_to: null }],
          affiliations: [{ affiliation_id: 'aff-1', person_id: 'person-1', person_name: '张三', department_id: 'dept-rd', position_title: '销售经理', valid_from: '2026-01-01', valid_to: null }],
          heads: [],
          current_head: null,
          agents: [{ agent_id: 'agent-1', name: '客户助手', status: 'active', primary_owner_name: '李四', department_id: 'dept-rd', is_primary: true, assignment_role: 'owner_department' }],
        });
      }
      if (url.includes('/org/people')) {
        return jsonResponse({ people: [person] });
      }
      if (url.includes('/org/agents')) {
        return jsonResponse({ agents: [agent] });
      }
      if (url.includes('/departments') && init?.method === 'POST') {
        const posted = JSON.parse(String(init.body)) as { name: string };
        return jsonResponse({ ...department, id: 'dept-new', name: posted.name, path: '/dept-new' });
      }
      return jsonResponse({});
    }));

    renderPage();

    expect((await screen.findByRole('textbox', { name: '企业名称' }) as HTMLInputElement).value).toBe('Demo Enterprise');
    expect((await screen.findAllByText('研发')).length).toBeGreaterThan(0);
    expect(screen.getByText('企业人员')).toBeTruthy();
    expect(screen.getByText('数字员工')).toBeTruthy();
    expect(screen.getAllByText('张三').length).toBeGreaterThan(0);
    expect(screen.getAllByText('客户助手').length).toBeGreaterThan(0);
    expect(screen.getAllByText('主责').length).toBeGreaterThan(0);

    await user.click(screen.getByRole('button', { name: /部 研发/ }));
    const peoplePanel = await screen.findByTestId('org-people');
    expect((await within(peoplePanel).findAllByText('张三')).length).toBeGreaterThan(0);
    expect(within(peoplePanel).getAllByText('客户助手').length).toBeGreaterThan(0);

    // 收尾任职(含离职人员)不得携带 person_id，否则后端换人校验会拦截收尾操作。
    await user.click(within(peoplePanel).getByRole('button', { name: '保存任职' }));
    await waitFor(() => {
      const saved = calls.find((call) => call.url.includes('/org/affiliations/aff-1') && call.method === 'PATCH');
      const body = JSON.parse(saved?.body || '{}');
      expect(body).toMatchObject({ tenant_id: 'tenant_demo' });
      expect('person_id' in body).toBe(false);
    });

    // 跨部门移动走专用 move 端点：结束旧任职+新建，保留原部门历史。
    await user.click(screen.getByRole('button', { name: '移动' }));
    expect(await screen.findByText('移动部门')).toBeTruthy();
    await user.selectOptions(
      screen.getByRole('combobox', { name: '目标部门' }),
      'dept-market',
    );
    await user.click(screen.getByRole('button', { name: '确认移动' }));
    await waitFor(() => {
      const moved = calls.find((call) => call.url.includes('/org/affiliations/aff-1/move'));
      expect(moved?.method).toBe('POST');
      expect(JSON.parse(moved?.body || '{}')).toMatchObject({
        tenant_id: 'tenant_demo',
        department_id: 'dept-market',
      });
    });

    await user.click(screen.getByRole('button', { name: '新增部门' }));
    await user.type(screen.getByRole('textbox', { name: '部门名称' }), '市场');
    await user.click(screen.getByRole('button', { name: '保存' }));
    await waitFor(() => {
      const created = calls.find((call) => call.url.endsWith('/org/departments') && call.method === 'POST');
      expect(JSON.parse(created?.body || '{}')).toMatchObject({
        tenant_id: 'tenant_demo',
        name: '市场',
        parent_id: null,
      });
    });
  });
});
