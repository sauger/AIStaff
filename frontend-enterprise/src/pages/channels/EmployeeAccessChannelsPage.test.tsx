/**
 * @vitest-environment jsdom
 */

import { cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { I18nProvider } from '@/i18n';

import type { EnterpriseAuthUser } from '../../auth';
import { ENTERPRISE_AGENT_STORAGE_KEY } from '../../lib/agent-scope-storage';
import EmployeeAccessChannelsPage from './EmployeeAccessChannelsPage';

const navigateMock = vi.fn();

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return {
    ...actual,
    useNavigate: () => navigateMock,
  };
});

vi.mock('../../api/client', () => ({
  TENANT_ID: 'tenant_demo',
  api: {
    get: vi.fn(async (url: string) => {
      if (url.includes('/api/enterprise/agents')) {
        return [
          {
            id: 'agent-1',
            name: '小艾',
            tenant_id: 'tenant_demo',
            status: 'active',
            is_overall: false,
            created_by: 'user-1',
          },
        ];
      }
      if (url.includes('/api/enterprise/channels')) {
        return [
          {
            id: 'binding-1',
            tenant_id: 'tenant_demo',
            agent_id: 'agent-1',
            channel: 'wecom',
            status: 'active',
            connected: true,
            name: '企微机器人',
            agents: [{ agent_id: 'agent-1', name: '小艾', is_default: true, sort_order: 0 }],
            auto_route: false,
            created_at: '2026-08-01T00:00:00Z',
            updated_at: '2026-08-01T00:00:00Z',
          },
        ];
      }
      if (url.includes('/api/enterprise/mail/mailbox')) {
        return { configured: false, enabled: false, address: null };
      }
      return [];
    }),
  },
}));

const currentUser = {
  id: 'user-1',
  username: 'admin',
  role: 'admin',
} as EnterpriseAuthUser;

describe('EmployeeAccessChannelsPage', () => {
  beforeEach(() => {
    navigateMock.mockReset();
    localStorage.setItem(ENTERPRISE_AGENT_STORAGE_KEY, 'agent-1');
  });

  afterEach(() => {
    cleanup();
    localStorage.removeItem(ENTERPRISE_AGENT_STORAGE_KEY);
  });

  it('renders 接入渠道 for the scoped employee', async () => {
    render(
      <I18nProvider>
        <MemoryRouter>
          <EmployeeAccessChannelsPage currentUser={currentUser} />
        </MemoryRouter>
      </I18nProvider>,
    );

    expect(await screen.findAllByText('接入渠道')).not.toHaveLength(0);
    expect(await screen.findByText('企微机器人')).toBeTruthy();
    expect(screen.getByRole('button', { name: '打开渠道管理' })).toBeTruthy();
  });
});
