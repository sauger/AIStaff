// @vitest-environment jsdom

import { cleanup, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { I18nProvider } from '@/i18n';
import { ENTERPRISE_AGENT_STORAGE_KEY } from '@/lib/agent-scope-storage';
import type { EnterpriseAuthUser } from '@/auth';
import type {
  AgentProfileRead,
  ContactListResponse,
  ContactRead,
  EnterprisePersonRead,
  OrgDepartmentRead,
  OrgTreePersonRead,
} from '@/types';

import ContactsPage from './ContactsPage';

const companyName = '木里木外家具启东有限公司';

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

const rootDepartment: OrgDepartmentRead = {
  id: 'dept-1',
  name: '未来创研中心',
  parent_id: null,
  path: '/dept-1',
  sort_order: 1,
  created_at: '2026-08-01T00:00:00Z',
  updated_at: '2026-08-01T00:00:00Z',
};

const childDepartment: OrgDepartmentRead = {
  id: 'dept-2',
  name: '品牌发展中心',
  parent_id: 'dept-1',
  path: '/dept-1/dept-2',
  sort_order: 1,
  created_at: '2026-08-01T00:00:00Z',
  updated_at: '2026-08-01T00:00:00Z',
};

const orgPerson: EnterprisePersonRead = {
  id: 'person-1',
  tenant_id: 'tenant_demo',
  name: '王五',
  employee_no: 'E100',
  phone: '13900000000',
  email: 'wangwu@example.com',
  status: 'active',
  source: 'manual',
  created_at: '2026-08-01T00:00:00Z',
  updated_at: '2026-08-01T00:00:00Z',
};

const treePerson: OrgTreePersonRead = {
  affiliation_id: 'aff-1',
  person_id: 'person-1',
  name: '王五',
  department_id: 'dept-1',
  position_title: '工程师',
  valid_from: '2026-01-01',
  valid_to: null,
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

function detail() {
  return screen.getByRole('region', { name: '通讯录详情' });
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

  it('shows the company and personal roots, with an empty personal directory', async () => {
    const user = userEvent.setup();
    const empty: ContactListResponse = { agent_id: 'agent-1', can_write: true, contacts: [] };
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/enterprise/agents')) return jsonResponse([ownedAgent]);
      if (url.includes('/api/enterprise/org/')) return jsonResponse({});
      if (url.includes('/api/enterprise/contacts')) return jsonResponse(empty);
      return jsonResponse([]);
    }));

    renderContacts(ownerUser);

    expect(await screen.findByRole('button', { name: '未命名企业' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '个人通讯录' })).toBeTruthy();
    expect(screen.getByText('通讯录')).toBeTruthy();
    await user.click(screen.getByRole('button', { name: '个人通讯录' }));
    expect(await within(detail()).findByText('还没有联系人，先新增一条吧。')).toBeTruthy();
    await user.click(await within(detail()).findByRole('button', { name: '新增联系人' }));
    expect(within(detail()).getByText('姓名')).toBeTruthy();
    expect(within(detail()).getByText('企微 userid')).toBeTruthy();
    expect(within(detail()).getByText('飞书 openid')).toBeTruthy();
    expect(within(detail()).getByText('钉钉 userid')).toBeTruthy();
    expect(screen.queryByText('对话账号')).toBeNull();
    expect(within(detail()).queryByRole('button', { name: '新增成员' })).toBeNull();
  });

  it('lists a personal contact and saves an edit without sending an account id', async () => {
    const user = userEvent.setup();
    const calls: { url: string; body?: string }[] = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({ url, body: typeof init?.body === 'string' ? init.body : undefined });
      if (url.includes('/api/enterprise/agents')) return jsonResponse([ownedAgent]);
      if (url.includes('/api/enterprise/org/')) return jsonResponse({});
      if (url.includes('/api/enterprise/contacts/contact-1')) return jsonResponse({ ...sample, name: '张三丰' });
      if (url.includes('/api/enterprise/contacts')) {
        return jsonResponse({ agent_id: 'agent-1', can_write: true, contacts: [sample] });
      }
      return jsonResponse([]);
    }));

    renderContacts(ownerUser);

    expect(await screen.findByRole('button', { name: '张三' })).toBeTruthy();
    await user.click(screen.getByRole('button', { name: '张三' }));
    expect(within(detail()).getByText('已关联对话账号')).toBeTruthy();
    expect(within(detail()).getByText('企微')).toBeTruthy();
    expect(within(detail()).getByText('zhangsan')).toBeTruthy();
    expect(within(detail()).getByText('重点客户')).toBeTruthy();
    const name = within(detail()).getByPlaceholderText('例如：张三');
    await user.clear(name);
    await user.type(name, '张三丰');
    await user.click(within(detail()).getByRole('button', { name: '保存' }));

    const update = calls.find((call) => call.url.includes('/api/enterprise/contacts/contact-1'));
    expect(update?.body).toBeTruthy();
    const body = JSON.parse(update?.body || '{}') as Record<string, string>;
    expect(body.name).toBe('张三丰');
    expect(body.wecom_userid).toBe('zhangsan');
    expect(body.primary_user_id).toBeUndefined();
  });

  it('shows the enterprise tree and a read-only member, and creates a personal contact', async () => {
    const user = userEvent.setup();
    const calls: { url: string; method?: string; body?: string }[] = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({
        url,
        method: init?.method,
        body: typeof init?.body === 'string' ? init.body : undefined,
      });
      if (url.includes('/api/enterprise/agents')) return jsonResponse([ownedAgent]);
      if (url.includes('/api/enterprise/org/settings')) {
        return jsonResponse({ tenant_id: 'tenant_demo', enterprise_name: companyName });
      }
      if (url.includes('/api/enterprise/org/tree')) {
        return jsonResponse({
          departments: [rootDepartment, childDepartment],
          people: [treePerson],
          agents: [],
        });
      }
      if (url.includes('/api/enterprise/org/people')) return jsonResponse({ people: [orgPerson] });
      if (url.includes('/api/enterprise/contacts') && init?.method === 'POST') {
        return jsonResponse({ ...sample, id: 'contact-new', name: '赵六', notes: '' });
      }
      if (url.includes('/api/enterprise/contacts')) {
        return jsonResponse({ agent_id: 'agent-1', can_write: true, contacts: [] });
      }
      return jsonResponse([]);
    }));

    renderContacts(ownerUser);

    expect(await screen.findByRole('heading', { name: companyName })).toBeTruthy();
    expect(within(detail()).getByText('- 1人 -')).toBeTruthy();
    expect(screen.getByRole('button', { name: '个人通讯录' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '品牌发展中心' })).toBeTruthy();
    expect(screen.getAllByRole('button', { name: '未来创研中心' }).length).toBeGreaterThan(1);

    await user.click(screen.getAllByRole('button', { name: '未来创研中心' })[0]);
    expect(within(detail()).getByRole('heading', { name: '未来创研中心' })).toBeTruthy();
    expect(within(detail()).getByRole('button', { name: '王五' })).toBeTruthy();
    expect(within(detail()).getByRole('button', { name: '品牌发展中心' })).toBeTruthy();
    expect(within(detail()).queryByRole('button', { name: '新增联系人' })).toBeNull();

    await user.click(screen.getAllByRole('button', { name: '王五' })[0]);
    expect(within(detail()).getByRole('heading', { name: '王五' })).toBeTruthy();
    expect(within(detail()).getByText('13900000000')).toBeTruthy();
    expect(within(detail()).getByText('wangwu@example.com')).toBeTruthy();
    expect(within(detail()).getByText('未来创研中心')).toBeTruthy();
    expect(within(detail()).getByText('工程师')).toBeTruthy();
    expect(within(detail()).getByText('E100')).toBeTruthy();
    expect(within(detail()).queryByRole('button', { name: '保存' })).toBeNull();
    expect(within(detail()).queryByRole('button', { name: '删除 王五' })).toBeNull();

    await user.click(screen.getByRole('button', { name: '个人通讯录' }));
    expect(await within(detail()).findByText('还没有联系人，先新增一条吧。')).toBeTruthy();
    await user.click(await within(detail()).findByRole('button', { name: '新增联系人' }));
    await user.type(within(detail()).getByPlaceholderText('例如：张三'), '赵六');
    await user.click(within(detail()).getByRole('button', { name: '保存' }));

    const created = calls.find((call) => call.method === 'POST' && call.url.includes('/api/enterprise/contacts'));
    expect(created?.body).toBeTruthy();
    const body = JSON.parse(created?.body || '{}') as Record<string, string>;
    expect(body.name).toBe('赵六');
    expect(body.primary_user_id).toBeUndefined();
    expect(calls.some((call) => call.method === 'POST' && call.url.includes('/api/enterprise/org/'))).toBe(false);
  });

  it('hides departed people and account links in the company directory', async () => {
    const user = userEvent.setup();
    const departed: EnterprisePersonRead = {
      ...orgPerson,
      id: 'person-left',
      name: '离职甲',
      phone: '13700000000',
      email: 'left@example.com',
      employee_no: 'E900',
      status: 'left',
      primary_user_id: 'user_left',
      primary_user_name: '账号甲',
      primary_username: 'left_account',
    };
    const linked: EnterprisePersonRead = {
      ...orgPerson,
      primary_user_id: 'user_wang',
      primary_user_name: '账号王',
      primary_username: 'wang_account',
    };
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/enterprise/agents')) return jsonResponse([ownedAgent]);
      if (url.includes('/api/enterprise/org/settings')) {
        return jsonResponse({ tenant_id: 'tenant_demo', enterprise_name: companyName });
      }
      if (url.includes('/api/enterprise/org/tree')) {
        return jsonResponse({
          departments: [rootDepartment],
          people: [
            treePerson,
            {
              affiliation_id: 'aff-left',
              person_id: 'person-left',
              name: '离职甲',
              department_id: 'dept-1',
              position_title: '顾问',
              valid_from: '2026-01-01',
              valid_to: null,
            },
          ],
          agents: [{
            agent_id: 'agent-hidden',
            name: '不该出现的数字员工',
            department_id: 'dept-1',
            status: 'active',
            is_primary: true,
            assignment_role: 'owner_department',
            primary_owner_name: '负责人甲',
          }],
        });
      }
      if (url.includes('/api/enterprise/org/people')) {
        return jsonResponse({ people: [linked, departed] });
      }
      if (url.includes('/api/enterprise/contacts')) {
        return jsonResponse({ agent_id: 'agent-1', can_write: true, contacts: [] });
      }
      return jsonResponse([]);
    }));

    renderContacts(ownerUser);

    expect(await screen.findByRole('heading', { name: companyName })).toBeTruthy();
    expect(within(detail()).getByText('- 1人 -')).toBeTruthy();
    expect(screen.getAllByRole('button', { name: '王五' }).length).toBeGreaterThan(0);
    expect(screen.queryByRole('button', { name: '离职甲' })).toBeNull();
    expect(screen.queryByText('不该出现的数字员工')).toBeNull();
    expect(screen.queryByText('负责人甲')).toBeNull();
    expect(screen.queryByText('账号甲')).toBeNull();
    expect(screen.queryByText('账号王')).toBeNull();
    expect(screen.queryByText('left_account')).toBeNull();
    expect(screen.queryByText('wang_account')).toBeNull();
    expect(screen.queryByText('user_left')).toBeNull();
    expect(screen.queryByText('user_wang')).toBeNull();

    await user.click(screen.getAllByRole('button', { name: '未来创研中心' })[0]);
    expect(within(detail()).getByRole('button', { name: '王五' })).toBeTruthy();
    expect(within(detail()).queryByRole('button', { name: '离职甲' })).toBeNull();

    await user.click(screen.getAllByRole('button', { name: '王五' })[0]);
    expect(within(detail()).getByRole('heading', { name: '王五' })).toBeTruthy();
    expect(within(detail()).getByText('未来创研中心')).toBeTruthy();
    expect(within(detail()).getByText('工程师')).toBeTruthy();
    expect(within(detail()).getByText('13900000000')).toBeTruthy();
    expect(within(detail()).getByText('wangwu@example.com')).toBeTruthy();
    expect(within(detail()).getByText('E100')).toBeTruthy();
    expect(within(detail()).getByText('在职')).toBeTruthy();
    expect(within(detail()).queryByText('已离职')).toBeNull();
    expect(within(detail()).queryByText('账号王')).toBeNull();
    expect(within(detail()).queryByText('wang_account')).toBeNull();
    expect(within(detail()).queryByText('user_wang')).toBeNull();
  });

  it('hides personal write actions when an admin views another employee', async () => {
    const user = userEvent.setup();
    window.localStorage.setItem(ENTERPRISE_AGENT_STORAGE_KEY, 'agent-2');
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/enterprise/agents')) return jsonResponse([otherAgent]);
      if (url.includes('/api/enterprise/org/settings')) {
        return jsonResponse({ tenant_id: 'tenant_demo', enterprise_name: companyName });
      }
      if (url.includes('/api/enterprise/org/tree')) {
        return jsonResponse({ departments: [rootDepartment], people: [treePerson], agents: [] });
      }
      if (url.includes('/api/enterprise/org/people')) return jsonResponse({ people: [orgPerson] });
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
    expect(await screen.findByRole('button', { name: companyName })).toBeTruthy();
    expect(screen.queryByRole('button', { name: '新增联系人' })).toBeNull();
    await user.click(screen.getByRole('button', { name: '李四' }));
    expect(within(detail()).getByRole('heading', { name: '李四' })).toBeTruthy();
    expect(within(detail()).getByText('13800000000')).toBeTruthy();
    expect(within(detail()).queryByRole('button', { name: '保存' })).toBeNull();
    expect(within(detail()).queryByRole('button', { name: '删除 李四' })).toBeNull();
    expect(within(detail()).queryByRole('button', { name: '编辑 李四' })).toBeNull();
  });
});
