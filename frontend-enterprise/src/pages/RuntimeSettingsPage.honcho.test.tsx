// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { I18nProvider } from '@/i18n';

import type { EnterpriseAuthUser } from '../auth';
import type { UIConfigRead } from '../types';
import RuntimeSettingsPage from './RuntimeSettingsPage';

const apiGet = vi.fn();
const apiPut = vi.fn();

vi.mock('../api/client', () => ({
  TENANT_ID: 'tenant_demo',
  ApiError: class ApiError extends Error {},
  api: {
    get: (...args: unknown[]) => apiGet(...args),
    put: (...args: unknown[]) => apiPut(...args),
  },
}));

vi.mock('sonner', () => ({
  toast: {
    custom: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
    loading: vi.fn(),
    dismiss: vi.fn(),
  },
}));

const admin: EnterpriseAuthUser = {
  id: 'user_admin',
  tenant_id: 'tenant_demo',
  username: 'admin',
  role: 'admin',
};

const config: UIConfigRead = {
  tenant_id: 'tenant_demo',
  show_thinking_trace: true,
  show_skill_trace: true,
  show_tool_trace: true,
  reflection_max_rounds: 1,
  agent_loop_max_actions: 32,
  context_token_budget: 32000,
  context_compaction_trigger_ratio: 0.7,
  context_recent_round_limit: 6,
  context_long_summary_token_budget: 4000,
  context_medium_summary_token_budget: 4000,
  context_allowed_roles: ['user', 'assistant'],
  context_long_summary_prefix: '历史的信息可以被总结为：',
  context_medium_summary_prefix: '近期的历史信息总结为：',
  sandbox_enabled: false,
  harness_storage_path: '',
  effective_harness_storage_path: '/data/staffdeck-files',
  sandbox_network_mode: 'all',
  sandbox_allowed_domains: [],
  honcho_enabled: true,
  honcho_base_url: 'http://honcho.env',
  honcho_timeout_ms: 200,
  updated_at: '2026-10-29T00:00:00Z',
};

afterEach(() => {
  cleanup();
  apiGet.mockReset();
  apiPut.mockReset();
});

describe('runtime settings Honcho master switch', () => {
  it('loads the effective switch and saves an override', async () => {
    let resolveGet: (value: UIConfigRead) => void = () => undefined;
    apiGet.mockReturnValue(new Promise<UIConfigRead>((resolve) => {
      resolveGet = resolve;
    }));
    apiPut.mockImplementation(async (_url: string, body: UIConfigRead) => ({
      ...config,
      ...body,
      restart_scheduled: false,
    }));
    const user = userEvent.setup();

    render(
      <I18nProvider>
        <RuntimeSettingsPage currentUser={admin} />
      </I18nProvider>,
    );

    const save = screen.getByRole('button', { name: '保存设置' }) as HTMLButtonElement;
    const toggle = screen.getByRole('switch', { name: '长期记忆 / Honcho 总开关' });
    expect(save.disabled).toBe(true);
    expect(toggle).toHaveProperty('disabled', true);
    expect(apiPut).not.toHaveBeenCalled();

    resolveGet(config);
    const address = () => screen.getByRole('textbox', { name: /Honcho 服务地址/ }) as HTMLInputElement;
    await waitFor(() => {
      expect(address().value).toBe('http://honcho.env');
      expect(toggle.getAttribute('aria-checked')).toBe('true');
      expect(save.disabled).toBe(false);
    });
    expect(screen.getByText(/员工也需要打开「长期用户画像」/)).toBeTruthy();
    expect(screen.getByText(/HONCHO_API_KEY/)).toBeTruthy();

    await user.click(toggle);
    await user.clear(address());
    await user.type(address(), 'http://honcho.ui');
    await user.click(save);

    await waitFor(() => expect(apiPut).toHaveBeenCalledTimes(1));
    const body = apiPut.mock.calls[0][1] as {
      honcho_enabled: boolean;
      honcho_base_url: string;
      honcho_timeout_ms: number;
    };
    expect(body.honcho_enabled).toBe(false);
    expect(body.honcho_base_url).toBe('http://honcho.ui');
    expect(body.honcho_timeout_ms).toBe(200);
    expect(body).not.toHaveProperty('honcho_api_key');
  });

  it('keeps save disabled when runtime settings fail to load', async () => {
    apiGet.mockRejectedValue(new Error('加载失败'));

    render(
      <I18nProvider>
        <RuntimeSettingsPage currentUser={admin} />
      </I18nProvider>,
    );

    expect(await screen.findByText('运行设置加载失败，暂不能修改 Honcho 总开关。')).toBeTruthy();
    const save = screen.getByRole('button', { name: '保存设置' }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    expect(apiPut).not.toHaveBeenCalled();
  });
});
