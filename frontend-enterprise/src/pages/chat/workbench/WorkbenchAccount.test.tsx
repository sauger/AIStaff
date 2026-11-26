// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { I18nProvider } from '@/i18n';

import type { UseChatSession } from '../useChatSession';
import WorkbenchAccount from './WorkbenchAccount';
import { EmployeeAppearanceProvider } from './useEmployeeAppearance';

afterEach(cleanup);

function renderAccount(collapsed = false) {
  const chat = {
    auth: { user: { username: 'admin', display_name: 'Administrator' } },
    openHandoffInbox: vi.fn(),
    openAdmin: vi.fn(),
    logout: vi.fn(),
    setModelSetupOpen: vi.fn(),
  } as unknown as UseChatSession;
  render(
    <I18nProvider>
      <EmployeeAppearanceProvider>
        <WorkbenchAccount chat={chat} collapsed={collapsed} />
      </EmployeeAppearanceProvider>
    </I18nProvider>,
  );
  return chat;
}

describe('WorkbenchAccount', () => {
  it('shows the account trigger with the username', () => {
    renderAccount();
    expect(screen.getByRole('button', { name: '用户菜单' })).toBeTruthy();
    expect(screen.getAllByText('Administrator').length).toBeGreaterThan(0);
  });

  it('keeps the avatar-only trigger when collapsed', () => {
    renderAccount(true);
    expect(screen.getByRole('button', { name: '用户菜单' })).toBeTruthy();
    expect(screen.queryByText('admin')).toBeNull();
  });
});
