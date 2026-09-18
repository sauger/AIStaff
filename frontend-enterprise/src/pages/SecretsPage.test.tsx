// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { I18nProvider } from '@/i18n';
import { ENTERPRISE_AGENT_STORAGE_KEY } from '@/lib/agent-scope-storage';
import type { EnterpriseAuthUser } from '@/auth';
import type { AgentProfileRead, EmployeeSecretListResponse } from '@/types';

import SecretsPage from './SecretsPage';

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

function jsonResponse(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    statusText: 'OK',
    text: async () => JSON.stringify(body ?? {}),
  } as Response;
}

function renderSecrets(currentUser: EnterpriseAuthUser) {
  return render(
    <I18nProvider>
      <MemoryRouter>
        <SecretsPage currentUser={currentUser} />
      </MemoryRouter>
    </I18nProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

describe('SecretsPage', () => {
  beforeEach(() => {
    window.localStorage.setItem(ENTERPRISE_AGENT_STORAGE_KEY, 'agent-1');
  });

  it('shows the empty-state copy instead of a blank page', async () => {
    const empty: EmployeeSecretListResponse = {
      agent_id: 'agent-1',
      can_write: true,
      secrets: [],
    };
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/enterprise/agents')) return jsonResponse([ownedAgent]);
      if (url.includes('/api/enterprise/secrets')) return jsonResponse(empty);
      return jsonResponse([]);
    }));

    renderSecrets(ownerUser);

    expect(await screen.findByText('还没有密钥，先新增一条吧。')).toBeTruthy();
    expect(screen.getByText('密钥')).toBeTruthy();
    expect(screen.getByRole('button', { name: '新增密钥' })).toBeTruthy();
  });

  it('never renders ciphertext on a saved secret card', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/enterprise/agents')) return jsonResponse([ownedAgent]);
      if (url.includes('/api/enterprise/secrets')) {
        return jsonResponse({
          agent_id: 'agent-1',
          can_write: true,
          secrets: [{
            id: 'esec-1',
            agent_id: 'agent-1',
            name: 'Token Channel 主账号',
            description: '日常操作',
            secret_type: 'password',
            value_configured: true,
            created_at: '2026-09-01T00:00:00Z',
            updated_at: '2026-09-01T00:00:00Z',
          }],
        });
      }
      return jsonResponse([]);
    }));

    renderSecrets(ownerUser);

    expect(await screen.findByText('Token Channel 主账号')).toBeTruthy();
    expect(screen.getByText('密文不会显示')).toBeTruthy();
    expect(screen.queryByText('super-secret-password')).toBeNull();
    expect(screen.getByRole('button', { name: '编辑' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '删除' })).toBeTruthy();
  });

  it('hides write actions when an admin views another employee', async () => {
    window.localStorage.setItem(ENTERPRISE_AGENT_STORAGE_KEY, 'agent-2');
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/enterprise/agents')) return jsonResponse([otherAgent]);
      if (url.includes('/api/enterprise/secrets')) {
        return jsonResponse({
          agent_id: 'agent-2',
          can_write: false,
          secrets: [{
            id: 'esec-2',
            agent_id: 'agent-2',
            name: '别人的密钥',
            description: '不可编辑',
            secret_type: 'password',
            value_configured: true,
            created_at: '2026-09-01T00:00:00Z',
            updated_at: '2026-09-01T00:00:00Z',
          }],
        });
      }
      return jsonResponse([]);
    }));

    renderSecrets(adminUser);

    expect(await screen.findByText('管理员正在查阅其他员工的密钥，只能看名称、说明和类型，不能新增、编辑或删除。')).toBeTruthy();
    expect(screen.queryByRole('button', { name: '新增密钥' })).toBeNull();
    expect(screen.queryByRole('button', { name: '编辑' })).toBeNull();
    expect(screen.queryByRole('button', { name: '删除' })).toBeNull();
  });

  it('opens the create dialog for the owner', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/enterprise/agents')) return jsonResponse([ownedAgent]);
      if (url.includes('/api/enterprise/secrets')) {
        return jsonResponse({ agent_id: 'agent-1', can_write: true, secrets: [] });
      }
      return jsonResponse([]);
    }));

    renderSecrets(ownerUser);
    await screen.findByRole('button', { name: '新增密钥' });
    await userEvent.click(screen.getByRole('button', { name: '新增密钥' }));
    expect(await screen.findByText('新增密钥', { selector: 'h2,h1,[data-slot="dialog-title"]' }).catch(() => screen.getByRole('heading', { name: '新增密钥' }))).toBeTruthy();
  });
});
