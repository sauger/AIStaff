// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

import { I18nProvider } from '@/i18n';
import type { AgentProfileRead } from '@/types';

import EmployeeProfileEditor from './EmployeeProfileEditor';

const apiPut = vi.fn();

vi.mock('../api/client', () => ({
  TENANT_ID: 'tenant_demo',
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: (...args: unknown[]) => apiPut(...args),
    delete: vi.fn(),
  },
}));

vi.mock('../pages/channels/WecomOaAgentConfigPanel', () => ({
  default: () => null,
}));

const agent: AgentProfileRead = {
  id: 'agent_xyz',
  tenant_id: 'tenant_demo',
  name: '小艾',
  is_overall: false,
  status: 'active',
  harness_max_actions: 32,
  metadata: { owner_user_id: 'user_owner', honcho_enabled: false },
  resources: [],
  created_at: '2026-08-01T00:00:00Z',
  updated_at: '2026-08-01T00:00:00Z',
};

afterEach(() => {
  cleanup();
  apiPut.mockReset();
});

describe('EmployeeProfileEditor Honcho toggle', () => {
  it('writes honcho_enabled without dropping other metadata', async () => {
    apiPut.mockResolvedValue({ ...agent, metadata: { ...agent.metadata, honcho_enabled: true } });
    const user = userEvent.setup();

    render(
      <I18nProvider>
        <MemoryRouter>
          <EmployeeProfileEditor agent={agent} open onClose={() => undefined} />
        </MemoryRouter>
      </I18nProvider>,
    );

    await user.click(screen.getByRole('switch', { name: '长期用户画像' }));
    await user.click(screen.getByRole('button', { name: '保存' }));

    expect(apiPut).toHaveBeenCalledTimes(1);
    const body = apiPut.mock.calls[0][1] as { metadata: Record<string, unknown> };
    expect(body.metadata.honcho_enabled).toBe(true);
    expect(body.metadata.owner_user_id).toBe('user_owner');
  });
});
