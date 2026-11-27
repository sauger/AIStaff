// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { I18nProvider } from '@/i18n';

import type { UseChatSession } from '../useChatSession';
import WorkbenchAccount from './WorkbenchAccount';
import { APPEARANCE_STORAGE_KEY, EmployeeAppearanceProvider } from './useEmployeeAppearance';

afterEach(() => {
  cleanup();
  window.localStorage.removeItem(APPEARANCE_STORAGE_KEY);
});

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

async function openAppearanceItem() {
  const user = userEvent.setup();
  await user.click(screen.getByRole('button', { name: '用户菜单' }));
  const item = screen.getByRole('menuitem', { name: '切换形象' });
  return { user, item };
}

function shownIcon(item: HTMLElement) {
  const icons = Array.from(item.querySelectorAll('svg'));
  return icons.length === 1 ? icons[0].getAttribute('data-icon') : `${icons.length} icons`;
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

  it('shows only the selected appearance icon in the switch item', async () => {
    window.localStorage.setItem(APPEARANCE_STORAGE_KEY, 'original');
    renderAccount();
    const { item } = await openAppearanceItem();
    expect(shownIcon(item)).toBe('person');
  });

  it('swaps to the other single icon after switching appearance', async () => {
    renderAccount();
    const { user, item } = await openAppearanceItem();
    expect(shownIcon(item)).toBe('ghost');
    await user.click(item);
    expect(window.localStorage.getItem(APPEARANCE_STORAGE_KEY)).toBe('original');
    const { item: reopened } = await openAppearanceItem();
    expect(shownIcon(reopened)).toBe('person');
  });
});
