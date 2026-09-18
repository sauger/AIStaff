// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { I18nProvider } from '@/i18n';
import { ENTERPRISE_AGENT_STORAGE_KEY } from '@/lib/agent-scope-storage';
import type { EnterpriseAuthUser } from '@/auth';
import type { AgentProfileRead, LoginGuideListResponse } from '@/types';

import LoginGuidesPage from './LoginGuidesPage';

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

const boundGuide: LoginGuideListResponse = {
  agent_id: 'agent-1',
  can_write: true,
  guides: [{
    id: 'elogin-1',
    agent_id: 'agent-1',
    name: 'Token Channel 登录',
    url: 'https://token-channel.example.com/login',
    username_selector: '#login-username',
    username_label: '用户名',
    password_selector: '#login-password',
    password_label: '密码',
    submit_selector: 'button[type=submit]',
    submit_label: '登录',
    default_secret_name: '主账号',
    published: false,
    can_write: true,
    created_at: '2026-09-01T00:00:00Z',
    updated_at: '2026-09-01T00:00:00Z',
  }],
};

function jsonResponse(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    statusText: 'OK',
    text: async () => JSON.stringify(body ?? {}),
  } as Response;
}

function renderGuides(currentUser: EnterpriseAuthUser) {
  return render(
    <I18nProvider>
      <MemoryRouter>
        <LoginGuidesPage currentUser={currentUser} />
      </MemoryRouter>
    </I18nProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

describe('LoginGuidesPage', () => {
  beforeEach(() => {
    window.localStorage.setItem(ENTERPRISE_AGENT_STORAGE_KEY, 'agent-1');
  });

  it('disables test login until a default secret is bound', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/enterprise/agents')) return jsonResponse([ownedAgent]);
      if (url.includes('/api/enterprise/secrets')) {
        return jsonResponse({ agent_id: 'agent-1', can_write: true, secrets: [] });
      }
      if (url.includes('/api/enterprise/login-guides/gallery')) return jsonResponse([]);
      if (url.includes('/api/enterprise/login-guides')) {
        return jsonResponse({
          ...boundGuide,
          guides: [{ ...boundGuide.guides[0], default_secret_name: null }],
        });
      }
      return jsonResponse([]);
    }));

    renderGuides(ownerUser);

    expect(await screen.findByText('请先绑定默认密钥后再测试登录。')).toBeTruthy();
    expect((screen.getByRole('button', { name: '测试登录' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('hides write and test-login actions for admin viewing another employee', async () => {
    window.localStorage.setItem(ENTERPRISE_AGENT_STORAGE_KEY, 'agent-2');
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/enterprise/agents')) return jsonResponse([otherAgent]);
      if (url.includes('/api/enterprise/secrets')) {
        return jsonResponse({ agent_id: 'agent-2', can_write: false, secrets: [] });
      }
      if (url.includes('/api/enterprise/login-guides/gallery')) return jsonResponse([]);
      if (url.includes('/api/enterprise/login-guides')) {
        return jsonResponse({
          agent_id: 'agent-2',
          can_write: false,
          guides: [{ ...boundGuide.guides[0], agent_id: 'agent-2', can_write: false }],
        });
      }
      return jsonResponse([]);
    }));

    renderGuides(adminUser);

    expect(await screen.findByText('管理员正在查阅其他员工的登录说明，只能看步骤元数据，不能编辑、删除或测试登录。')).toBeTruthy();
    expect(screen.queryByRole('button', { name: '新增登录说明' })).toBeNull();
    expect(screen.queryByRole('button', { name: '测试登录' })).toBeNull();
    expect(screen.getByText('只读模式：管理员查看其他员工时不可执行测试登录。')).toBeTruthy();
  });

  it('copies a plaza guide without showing source secrets', async () => {
    const copiedGuide = {
      ...boundGuide.guides[0],
      id: 'elogin-copied',
      name: '公开登录',
      default_secret_name: null,
      copied_from_id: 'elogin-pub',
    };
    let copied = false;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes('/api/enterprise/agents')) return jsonResponse([ownedAgent]);
      if (url.includes('/api/enterprise/secrets')) {
        return jsonResponse({ agent_id: 'agent-1', can_write: true, secrets: [] });
      }
      if (url.includes('/api/enterprise/login-guides/gallery')) {
        return jsonResponse([{
          id: 'elogin-pub',
          name: '公开登录',
          url: 'https://example.com/login',
          username_selector: '#u',
          username_label: '用户名',
          password_selector: '#p',
          password_label: '密码',
          submit_selector: '#go',
          submit_label: '登录',
          source_agent_id: 'agent-2',
          source_agent_name: '别人的员工',
          created_at: '2026-09-01T00:00:00Z',
          updated_at: '2026-09-01T00:00:00Z',
        }]);
      }
      if (url.includes('/copy')) {
        expect(init?.method).toBe('POST');
        copied = true;
        return jsonResponse(copiedGuide);
      }
      if (url.includes('/api/enterprise/login-guides')) {
        return jsonResponse({
          agent_id: 'agent-1',
          can_write: true,
          guides: copied ? [copiedGuide] : [],
        });
      }
      return jsonResponse([]);
    });
    vi.stubGlobal('fetch', fetchMock);

    renderGuides(ownerUser);
    await userEvent.click(await screen.findByRole('tab', { name: '广场' }));
    expect(await screen.findByText('公开登录')).toBeTruthy();
    expect(screen.getByText(/只复制步骤文字，不复制密钥/)).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: '复制到当前员工' }));
    expect(await screen.findByText(/密钥绑定为空/)).toBeTruthy();
  });
});
