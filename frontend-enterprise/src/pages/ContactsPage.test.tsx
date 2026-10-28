// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { I18nProvider } from '@/i18n';
import { ENTERPRISE_AGENT_STORAGE_KEY } from '@/lib/agent-scope-storage';
import type { EnterpriseAuthUser } from '@/auth';
import type { AgentProfileRead, ContactListResponse, ContactRead } from '@/types';

import ContactsPage from './ContactsPage';

const ownedAgent: AgentProfileRead = {
  id: 'agent-1',
  tenant_id: 'tenant_demo',
  name: '小艾',
  is_overall: false,
  status: 'active',
  metadata: { owner_user_id: 'user-owner' },
  resources: [],
  created_at: '2026-08-01T00:00:00Z',
  updated_at: '2026-08-01T00:00:00Z',
};

const otherAgent: AgentProfileRead = {
  ...ownedAgent,
  id: 'agent-2',
  name: '别人的员工',
  metadata: { owner_user_id: 'user-other' },
};

const ownerUser: EnterpriseAuthUser = {
  id: 'user-owner',
  tenant_id: 'tenant_demo',
  username: 'owner',
  role: 'member',
};

const adminUser: EnterpriseAuthUser = {
  id: 'user-admin',
  tenant_id: 'tenant_demo',
  username: 'admin',
  role: 'admin',
};

const sample: ContactRead = {
  id: 'contact-1',
  agent_id: 'agent-1',
  name: '张三',
  phone: '13800000000',
  email: 'zhangsan@example.com',
  title: '销售',
  city: '上海',
  notes: '重点客户',
  wecom_userid: 'zhangsan',
  feishu_openid: null,
  dingtalk_userid: null,
  primary_user_id: 'user_zhang',
  created_at: '2026-10-28T00:00:00Z',
  updated_at: '2026-10-28T00:00:00Z',
};

function jsonResponse(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    statusText: 'OK',
    text: async () => JSON.stringify(body ?? {}),
  } as Response;
}

function renderContacts(currentUser: EnterpriseAuthUser) {
  return render(
    <I18nProvider>
      <MemoryRouter>
        <ContactsPage currentUser={currentUser} />
      </MemoryRouter>
    </I18nProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

describe('ContactsPage', () => {
  beforeEach(() => {
    window.localStorage.setItem(ENTERPRISE_AGENT_STORAGE_KEY, 'agent-1');
    if (!Element.prototype.hasPointerCapture) {
      Element.prototype.hasPointerCapture = () => false;
    }
    if (!Element.prototype.releasePointerCapture) {
      Element.prototype.releasePointerCapture = () => undefined;
    }
  });

  it('shows an empty address book and opens the create form', async () => {
    const user = userEvent.setup();
    const empty: ContactListResponse = { agent_id: 'agent-1', can_write: true, contacts: [] };
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/enterprise/agents')) return jsonResponse([ownedAgent]);
      if (url.includes('/api/enterprise/contacts')) return jsonResponse(empty);
      return jsonResponse([]);
    }));

    renderContacts(ownerUser);

    expect(await screen.findByText('还没有联系人，先新增一条吧。')).toBeTruthy();
    expect(screen.getByText('通讯录')).toBeTruthy();
    await user.click(screen.getByRole('button', { name: '新增联系人' }));
    expect(screen.getByText('姓名')).toBeTruthy();
    expect(screen.getByText('企微 userid')).toBeTruthy();
    expect(screen.getByText('飞书 openid')).toBeTruthy();
    expect(screen.getByText('钉钉 userid')).toBeTruthy();
    expect(screen.queryByText('对话账号')).toBeNull();
  });

  it('lists a contact and saves an edit without sending an account id', async () => {
    const user = userEvent.setup();
    const calls: { url: string; body?: string }[] = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({ url, body: typeof init?.body === 'string' ? init.body : undefined });
      if (url.includes('/api/enterprise/agents')) return jsonResponse([ownedAgent]);
      if (url.includes('/api/enterprise/contacts/contact-1')) return jsonResponse({ ...sample, name: '张三丰' });
      if (url.includes('/api/enterprise/contacts')) {
        return jsonResponse({ agent_id: 'agent-1', can_write: true, contacts: [sample] });
      }
      return jsonResponse([]);
    }));

    renderContacts(ownerUser);

    expect(await screen.findByText('张三')).toBeTruthy();
    expect(screen.getByText('已关联对话账号')).toBeTruthy();
    expect(screen.getByText('企微')).toBeTruthy();
    expect(screen.getByText('zhangsan')).toBeTruthy();
    expect(screen.getByText('重点客户')).toBeTruthy();
    await user.click(screen.getByRole('button', { name: '编辑 张三' }));
    const name = screen.getByPlaceholderText('例如：张三');
    await user.clear(name);
    await user.type(name, '张三丰');
    await user.click(screen.getByRole('button', { name: '保存' }));

    const update = calls.find((call) => call.url.includes('/api/enterprise/contacts/contact-1'));
    expect(update?.body).toBeTruthy();
    const body = JSON.parse(update?.body || '{}') as Record<string, string>;
    expect(body.name).toBe('张三丰');
    expect(body.wecom_userid).toBe('zhangsan');
    expect(body.primary_user_id).toBeUndefined();
  });

  it('hides write actions when an admin views another employee', async () => {
    window.localStorage.setItem(ENTERPRISE_AGENT_STORAGE_KEY, 'agent-2');
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/enterprise/agents')) return jsonResponse([otherAgent]);
      if (url.includes('/api/enterprise/contacts')) {
        return jsonResponse({
          agent_id: 'agent-2',
          can_write: false,
          contacts: [{ ...sample, id: 'contact-2', agent_id: 'agent-2', name: '李四' }],
        });
      }
      return jsonResponse([]);
    }));

    renderContacts(adminUser);

    expect(await screen.findByText('管理员正在查阅其他员工的通讯录，只能查看，不能新增、编辑或删除。')).toBeTruthy();
    expect(screen.queryByRole('button', { name: '新增联系人' })).toBeNull();
    expect(screen.queryByRole('button', { name: '编辑' })).toBeNull();
  });
});
