// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from '@testing-library/react';
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

const people = {
  department_id: 'dept-rd',
  as_of: '2026-10-28',
  people: [
    {
      affiliation_id: 'aff-1',
      contact_id: 'contact-1',
      contact_name: '张三',
      agent_id: 'agent-1',
      agent_name: '员工A',
      title: '销售',
      valid_from: '2026-01-01',
      valid_to: null,
    },
  ],
  affiliations: [
    {
      affiliation_id: 'aff-1',
      contact_id: 'contact-1',
      contact_name: '张三',
      agent_id: 'agent-1',
      agent_name: '员工A',
      title: '销售',
      valid_from: '2026-01-01',
      valid_to: null,
    },
  ],
  heads: [],
  current_head: null,
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
      <OrgStructurePage currentUser={adminUser} />
    </I18nProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('OrgStructurePage', () => {
  it('saves the enterprise name, creates a department, and lists people in the selected department', async () => {
    const user = userEvent.setup();
    const calls: { url: string; method?: string; body?: string }[] = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({
        url,
        method: init?.method,
        body: typeof init?.body === 'string' ? init.body : undefined,
      });
      if (url.includes('/api/enterprise/org/settings')) {
        const posted = init?.body ? JSON.parse(String(init.body)) as { enterprise_name?: string } : null;
        return jsonResponse({
          tenant_id: 'tenant_demo',
          enterprise_name: posted?.enterprise_name || 'Demo Enterprise',
        });
      }
      if (url.includes('/people')) return jsonResponse(people);
      if (url.includes('/api/enterprise/org/departments') && init?.method === 'POST') {
        const posted = JSON.parse(String(init.body)) as { name: string; parent_id: string | null };
        return jsonResponse({
          ...department,
          id: 'dept-market',
          name: posted.name,
          parent_id: posted.parent_id,
          path: '/dept-market',
        });
      }
      if (url.includes('/api/enterprise/org/departments')) {
        return jsonResponse({ departments: [department] });
      }
      if (url.includes('/api/enterprise/org/contacts')) {
        return jsonResponse({
          contacts: [{ id: 'contact-1', name: '张三', agent_id: 'agent-1', agent_name: '员工A', title: '销售' }],
        });
      }
      return jsonResponse({});
    }));

    renderPage();

    const name = await screen.findByRole('textbox', { name: '企业名称' });
    expect((name as HTMLInputElement).value).toBe('Demo Enterprise');
    expect(await screen.findByRole('button', { name: '研发' })).toBeTruthy();

    await user.clear(name);
    await user.type(name, '星河科技');
    await user.click(screen.getByRole('button', { name: '保存企业名称' }));

    await user.click(screen.getByRole('button', { name: '研发' }));
    expect(await screen.findByText('张三')).toBeTruthy();
    expect(screen.getByText('暂无负责人')).toBeTruthy();

    await user.type(screen.getByRole('textbox', { name: '部门名称' }), '市场');
    await user.click(screen.getByRole('button', { name: '新增部门' }));

    await waitFor(() => {
      const created = calls.find((call) => call.url.endsWith('/api/enterprise/org/departments') && call.method === 'POST');
      expect(JSON.parse(created?.body || '{}')).toMatchObject({
        tenant_id: 'tenant_demo',
        name: '市场',
        parent_id: null,
      });
    });
    const rename = calls.find((call) => call.url.includes('/api/enterprise/org/settings') && call.method === 'PATCH');
    expect(JSON.parse(rename?.body || '{}').enterprise_name).toBe('星河科技');
    expect(calls.some((call) => call.url.includes('/departments/dept-rd/people'))).toBe(true);
  });
});
