// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { I18nProvider } from '@/i18n';
import { ENTERPRISE_AGENT_STORAGE_KEY } from '@/lib/agent-scope-storage';
import type { EnterpriseAuthUser } from '@/auth';
import type { AgentProfileRead, CabinetListResponse } from '@/types';

import CabinetPage from './CabinetPage';

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

const emptyListing: CabinetListResponse = {
  agent_id: 'agent-1',
  path: '',
  can_write: true,
  used_bytes: 0,
  max_file_bytes: 10 * 1024 * 1024,
  max_cabinet_bytes: 200 * 1024 * 1024,
  entries: [],
};

const fileEntry = {
  id: 'cab-1',
  kind: 'file' as const,
  name: '报价模板.xlsx',
  path: '报价模板.xlsx',
  parent_path: '',
  size_bytes: 12,
  source: 'console',
  created_at: '2026-08-01T00:00:00Z',
  updated_at: '2026-08-01T00:00:00Z',
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

function renderCabinet(currentUser: EnterpriseAuthUser) {
  return render(
    <I18nProvider>
      <MemoryRouter>
        <CabinetPage currentUser={currentUser} />
      </MemoryRouter>
    </I18nProvider>,
  );
}

function expectIconAction(label: string) {
  const button = screen.getByRole('button', { name: label });
  expect(button.getAttribute('title')).toBe(label);
  expect(button.textContent?.replace(/\s+/g, '')).toBe('');
  return button;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

describe('CabinetPage', () => {
  beforeEach(() => {
    window.localStorage.setItem(ENTERPRISE_AGENT_STORAGE_KEY, 'agent-1');
  });

  it('shows the empty-state copy instead of a blank page', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/enterprise/agents')) return jsonResponse([ownedAgent]);
      if (url.includes('/api/enterprise/cabinet')) return jsonResponse(emptyListing);
      return jsonResponse([]);
    }));

    renderCabinet(ownerUser);

    expect(await screen.findByText('先上传模板，再在对话里说以某某为模板生产')).toBeTruthy();
    expect(screen.getByText('文件柜')).toBeTruthy();
    expectIconAction('上传');
    expectIconAction('新建文件夹');
  });

  it('keeps owner write actions as titled icon buttons on a listing', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/enterprise/agents')) return jsonResponse([ownedAgent]);
      if (url.includes('/api/enterprise/cabinet')) {
        return jsonResponse({ ...emptyListing, entries: [fileEntry] });
      }
      return jsonResponse([]);
    }));

    renderCabinet(ownerUser);

    expect(await screen.findByText('报价模板.xlsx')).toBeTruthy();
    expectIconAction('上传');
    expectIconAction('新建文件夹');
    expectIconAction('下载');
    expectIconAction('重命名');
    expectIconAction('删除');
  });

  it('hides write actions when an admin views another employee cabinet', async () => {
    window.localStorage.setItem(ENTERPRISE_AGENT_STORAGE_KEY, 'agent-2');
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/enterprise/agents')) return jsonResponse([otherAgent]);
      if (url.includes('/api/enterprise/cabinet')) {
        return jsonResponse({
          ...emptyListing,
          agent_id: 'agent-2',
          can_write: false,
          entries: [fileEntry],
        });
      }
      return jsonResponse([]);
    }));

    renderCabinet(adminUser);

    expect(await screen.findByText('管理员正在查阅其他员工的文件柜，只能浏览和下载。')).toBeTruthy();
    expectIconAction('下载');
    expect(screen.queryByRole('button', { name: '上传' })).toBeNull();
    expect(screen.queryByRole('button', { name: '删除' })).toBeNull();
    expect(screen.queryByRole('button', { name: '重命名' })).toBeNull();
    expect(screen.queryByRole('button', { name: '新建文件夹' })).toBeNull();
  });
});
