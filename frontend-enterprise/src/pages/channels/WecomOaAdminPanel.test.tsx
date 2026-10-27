// @vitest-environment jsdom

import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { I18nProvider } from '@/i18n';

import WecomOaAdminPanel from './WecomOaAdminPanel';

vi.mock('../../api/client', () => ({
  TENANT_ID: 'tenant_demo',
  api: {
    get: vi.fn(async () => ({ credential: null, bindings: [] })),
    put: vi.fn(),
    post: vi.fn(),
    delete: vi.fn(),
  },
}));

describe('WecomOaAdminPanel', () => {
  it('hides write UI for non-admin users', () => {
    const { container } = render(
      <I18nProvider>
        <WecomOaAdminPanel currentUser={{ id: 'u1', username: 'bob', role: 'member', tenant_id: 'tenant_demo' }} />
      </I18nProvider>,
    );
    expect(container.querySelector('[data-testid="wecom-oa-admin-panel"]')).toBeNull();
    expect(screen.queryByText('保存 OA 凭证')).toBeNull();
  });

  it('shows credential and binding forms for admin', async () => {
    render(
      <I18nProvider>
        <WecomOaAdminPanel currentUser={{ id: 'u1', username: 'alice', role: 'admin', tenant_id: 'tenant_demo' }} />
      </I18nProvider>,
    );
    expect(await screen.findByTestId('wecom-oa-admin-panel')).toBeTruthy();
    expect(screen.getByText('保存 OA 凭证')).toBeTruthy();
    expect(screen.getByText('新增/更新绑定')).toBeTruthy();
    expect(screen.getByLabelText('StaffDeck 用户')).toBeTruthy();
  });
});
