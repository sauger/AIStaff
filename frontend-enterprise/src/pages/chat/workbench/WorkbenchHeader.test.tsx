// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { I18nProvider } from '@/i18n';

import type { UseChatSession } from '../useChatSession';
import WorkbenchHeader from './WorkbenchHeader';
import { EmployeeAppearanceProvider } from './useEmployeeAppearance';

afterEach(cleanup);

function renderHeader() {
  const chat = {
    auth: { user: { username: 'qianya' } },
    openGallery: vi.fn(),
    openWorkbench: vi.fn(),
    openWelcome: vi.fn(),
    openHandoffInbox: vi.fn(),
    openAdmin: vi.fn(),
    logout: vi.fn(),
    setModelSetupOpen: vi.fn(),
  } as unknown as UseChatSession;
  render(
    <I18nProvider>
      <EmployeeAppearanceProvider>
        <WorkbenchHeader chat={chat} />
      </EmployeeAppearanceProvider>
    </I18nProvider>,
  );
  return chat;
}

describe('WorkbenchHeader', () => {
  it('shows the account menu without a gallery entry', () => {
    renderHeader();
    expect(screen.queryByRole('button', { name: '数字员工广场' })).toBeNull();
    expect(screen.queryByRole('button', { name: '欢迎页' })).toBeNull();
    expect(screen.getByRole('button', { name: '用户菜单' })).toBeTruthy();
    expect(screen.queryByText('员工广场')).toBeNull();
  });
});
