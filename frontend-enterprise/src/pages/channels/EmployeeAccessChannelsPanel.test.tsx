// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { I18nProvider } from '@/i18n';

import EmployeeAccessChannelsPanel from './EmployeeAccessChannelsPanel';

const apiGet = vi.fn();

vi.mock('../../api/client', () => ({
  TENANT_ID: 'tenant_demo',
  api: {
    get: (...args: unknown[]) => apiGet(...args),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}));

afterEach(() => {
  cleanup();
  apiGet.mockReset();
});

describe('EmployeeAccessChannelsPanel', () => {
  it('renders 接入渠道 with binding and mailbox rows for this agent only', async () => {
    apiGet.mockImplementation(async (url: string) => {
      if (url.includes('/api/enterprise/channels')) {
        return [
          {
            id: 'chan_1',
            agent_id: 'agent-1',
            channel: 'wecom',
            name: '小艾企微',
            status: 'active',
            connected: true,
            agents: [{ agent_id: 'agent-1', name: '小艾', is_default: true }],
          },
        ];
      }
      if (url.includes('/api/enterprise/mail/mailbox')) {
        return { configured: true, enabled: true, address: 'xiaoi@example.com' };
      }
      return {};
    });

    render(
      <I18nProvider>
        <EmployeeAccessChannelsPanel agentId="agent-1" />
      </I18nProvider>,
    );

    expect(await screen.findByText('接入渠道')).toBeTruthy();
    expect(await screen.findByText('小艾企微')).toBeTruthy();
    expect(await screen.findByText(/邮箱/)).toBeTruthy();
    await waitFor(() => {
      const channelsCall = apiGet.mock.calls.find((c) => String(c[0]).includes('/channels'));
      expect(String(channelsCall?.[0])).toContain('agent_id=agent-1');
    });
    expect(screen.queryByText('智能分发')).toBeNull();
    expect(screen.queryByText('转移')).toBeNull();
  });
});
