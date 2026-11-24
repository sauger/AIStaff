// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { I18nProvider } from '@/i18n';
import type { ChatSession } from '@/types';

import type { UseChatSession } from '../useChatSession';
import ChatHeading from './ChatHeading';

afterEach(cleanup);

function buildChat(session: Partial<ChatSession>, extras: Partial<UseChatSession> = {}): UseChatSession {
  return {
    currentSession: {
      id: 'session-1',
      tenant_id: 'tenant_demo',
      status: 'active',
      updated_at: '2026-08-01T00:00:00Z',
      ...session,
    } as ChatSession,
    openRename: vi.fn(),
    enabledModelConfigs: [],
    selectedModelConfig: null,
    changeModelConfig: vi.fn(),
    setComposerIntent: vi.fn(),
    setModelSetupOpen: vi.fn(),
    teams: [],
    ...extras,
  } as unknown as UseChatSession;
}

function renderHeading(chat: UseChatSession) {
  return render(
    <I18nProvider>
      <ChatHeading chat={chat} />
    </I18nProvider>,
  );
}

describe('ChatHeading', () => {
  it('shows the session title with rename access', () => {
    renderHeading(buildChat({ title: '活动需求梳理' }));
    expect(screen.getByText('活动需求梳理')).toBeTruthy();
    expect(screen.getByRole('button', { name: '重命名会话' })).toBeTruthy();
  });

  it('shows the member count for team sessions', () => {
    renderHeading(buildChat(
      { title: '增长团队 · TL 对话', team_id: 'team-1' },
      {
        teams: [{
          id: 'team-1',
          name: '增长团队',
          members: Array.from({ length: 7 }, (_, index) => ({ id: `member-${index}` })),
        }],
      } as unknown as Partial<UseChatSession>,
    ));
    expect(screen.getByText('7名成员')).toBeTruthy();
    expect(screen.getByText('增长团队')).toBeTruthy();
  });

  it('exposes the model and scheduled task entries in the info menu', () => {
    renderHeading(buildChat({ title: 'x' }));
    expect(screen.getByRole('button', { name: '会话信息' })).toBeTruthy();
  });

  it('falls back to 新对话 when the session has no title', () => {
    renderHeading(buildChat({ id: 'session-untitled', title: '' }));
    expect(screen.getByText('新对话')).toBeTruthy();
    expect(screen.queryByText('session-untitled')).toBeNull();
  });
});
