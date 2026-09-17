// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { I18nProvider } from '@/i18n';
import { ENTERPRISE_AGENT_STORAGE_KEY } from '@/lib/agent-scope-storage';
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

function jsonResponse(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    statusText: 'OK',
    text: async () => JSON.stringify(body ?? {}),
  } as Response;
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

    render(
      <I18nProvider>
        <MemoryRouter>
          <CabinetPage
            currentUser={{ id: 'user-owner', tenant_id: 'tenant_demo', username: 'owner', role: 'member' }}
          />
        </MemoryRouter>
      </I18nProvider>,
    );

    expect(await screen.findByText('先上传模板，再在对话里说以某某为模板生产')).toBeTruthy();
    expect(screen.getByText('文件柜')).toBeTruthy();
    expect(screen.getByText('上传')).toBeTruthy();
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
          entries: [{
            id: 'cab-1',
            kind: 'file',
            name: '报价模板.xlsx',
            path: '报价模板.xlsx',
            parent_path: '',
            size_bytes: 12,
            source: 'console',
            created_at: '2026-08-01T00:00:00Z',
            updated_at: '2026-08-01T00:00:00Z',
          }],
        });
      }
      return jsonResponse([]);
    }));

    render(
      <I18nProvider>
        <MemoryRouter>
          <CabinetPage
            currentUser={{ id: 'user-admin', tenant_id: 'tenant_demo', username: 'admin', role: 'admin' }}
          />
        </MemoryRouter>
      </I18nProvider>,
    );

    expect(await screen.findByText('管理员正在查阅其他员工的文件柜，只能浏览和下载。')).toBeTruthy();
    expect(screen.getByText('下载')).toBeTruthy();
    expect(screen.queryByText('上传')).toBeNull();
    expect(screen.queryByText('删除')).toBeNull();
    expect(screen.queryByText('重命名')).toBeNull();
    expect(screen.queryByText('新建文件夹')).toBeNull();
  });
});
