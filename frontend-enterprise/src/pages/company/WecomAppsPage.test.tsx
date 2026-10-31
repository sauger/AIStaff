// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { I18nProvider } from '@/i18n';

import WecomAppsPage from './WecomAppsPage';

const api = {
  get: vi.fn(),
  put: vi.fn(),
  post: vi.fn(),
  patch: vi.fn(),
  delete: vi.fn(),
};

vi.mock('../../api/client', () => ({
  TENANT_ID: 'tenant_demo',
  api: {
    get: (...args: unknown[]) => api.get(...args),
    put: (...args: unknown[]) => api.put(...args),
    post: (...args: unknown[]) => api.post(...args),
    patch: (...args: unknown[]) => api.patch(...args),
    delete: (...args: unknown[]) => api.delete(...args),
  },
}));

afterEach(() => {
  cleanup();
  api.get.mockReset();
});

describe('WecomAppsPage', () => {
  it('shows an empty state and the mode B binding section', async () => {
    api.get.mockImplementation(async (path: string) => {
      if (String(path).includes('/wecom-apps')) return { apps: [] };
      if (String(path).includes('/temp-identity-bindings')) return { bindings: [] };
      if (String(path).includes('/api/auth/users')) return [];
      return {};
    });

    render(
      <I18nProvider>
        <MemoryRouter>
          <WecomAppsPage
            currentUser={{ id: 'u1', username: 'alice', role: 'admin', tenant_id: 'tenant_demo' }}
          />
        </MemoryRouter>
      </I18nProvider>,
    );

    expect(await screen.findByText('还没有企微自建应用')).toBeTruthy();
    expect(screen.getByText('临时身份绑定（Mode B）')).toBeTruthy();
    expect(screen.getByText('新增/更新绑定')).toBeTruthy();
    expect(screen.queryByText('保存 OA 凭证')).toBeNull();
  });

  it('lists a saved app without echoing secrets', async () => {
    api.get.mockImplementation(async (path: string) => {
      if (String(path).includes('/wecom-apps')) {
        return {
          apps: [
            {
              id: 'wapp_1',
              name: '审批',
              corp_id: 'ww_demo',
              agentid: '100',
              status: 'active',
              is_default: true,
              has_secret: true,
              has_callback_token: true,
              has_callback_aes_key: false,
              last_check_summary: '已连通',
              callback_url: '/api/public/wecom-app/callback/tenant_demo/wapp_1',
              callback_url_absolute: false,
              callback_url_hint: '请先配置公网基地址，再把回调地址贴进企微后台',
            },
          ],
        };
      }
      if (String(path).includes('/temp-identity-bindings')) return { bindings: [] };
      if (String(path).includes('/api/auth/users')) return [];
      return {};
    });

    render(
      <I18nProvider>
        <MemoryRouter>
          <WecomAppsPage
            currentUser={{ id: 'u1', username: 'alice', role: 'admin', tenant_id: 'tenant_demo' }}
          />
        </MemoryRouter>
      </I18nProvider>,
    );

    expect(await screen.findByText('审批')).toBeTruthy();
    expect(screen.getByText('默认')).toBeTruthy();
    expect(screen.getByText('Secret 已存')).toBeTruthy();
    expect(screen.getByText('复制回调地址')).toBeTruthy();
    expect(screen.queryByDisplayValue('plain-secret')).toBeNull();
  });
});
