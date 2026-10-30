// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { I18nProvider } from '@/i18n';
import type { EnterpriseAuthUser } from '@/auth';

import CompanyPage from './CompanyPage';

const admin: EnterpriseAuthUser = {
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

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('CompanyPage', () => {
  it('renders the members tab under the enterprise management shell', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/enterprise/org/people')) return jsonResponse({ people: [] });
      if (url.includes('/api/enterprise/org/tree')) {
        return jsonResponse({ departments: [], people: [], agents: [] });
      }
      if (url.includes('/api/auth/users')) return jsonResponse([]);
      return jsonResponse({});
    }));

    render(
      <I18nProvider>
        <MemoryRouter>
          <CompanyPage tab="members" currentUser={admin} />
        </MemoryRouter>
      </I18nProvider>,
    );

    expect(await screen.findByText('企业管理')).toBeTruthy();
    expect(screen.getByRole('tab', { name: '组织架构' })).toBeTruthy();
    expect(screen.getByRole('tab', { name: '成员' })).toBeTruthy();
    expect(screen.queryByRole('tab', { name: '企业人员' })).toBeNull();
    expect(screen.queryByRole('tab', { name: '数字员工' })).toBeNull();
    expect(screen.getByRole('button', { name: '新建人员' })).toBeTruthy();
  });
});
