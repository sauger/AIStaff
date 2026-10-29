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
const apiPost = vi.fn();

vi.mock('../api/client', () => ({
  TENANT_ID: 'tenant_demo',
  ApiError: class ApiError extends Error {},
  api: {
    get: (...args: unknown[]) => apiGet(...args),
    put: (...args: unknown[]) => apiPut(...args),
    post: (...args: unknown[]) => apiPost(...args),
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
  honcho_deriver_model: '',
  honcho_deriver_base_url: '',
  honcho_deriver_api_key_set: false,
  honcho_embedding_model: 'text-embedding-v4',
  honcho_embedding_base_url: '',
  honcho_embedding_api_key_set: true,
  honcho_embedding_dimensions: 1536,
  honcho_sidecar_apply_available: false,
  honcho_sidecar_applied: false,
  honcho_sidecar_apply_message: '',
  updated_at: '2026-10-29T00:00:00Z',
};

afterEach(() => {
  cleanup();
  apiGet.mockReset();
  apiPut.mockReset();
  apiPost.mockReset();
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

  it('saves deriver and embedding fields without echoing a stored key', async () => {
    apiGet.mockResolvedValue(config);
    apiPut.mockImplementation(async (_url: string, body: UIConfigRead) => ({
      ...config,
      ...body,
      honcho_deriver_api_key_set: true,
      honcho_sidecar_applied: false,
      honcho_sidecar_apply_message: '未配置 HONCHO_SIDECAR_ENV_FILE，尚未写入 Honcho。',
    }));
    const user = userEvent.setup();

    render(
      <I18nProvider>
        <RuntimeSettingsPage currentUser={admin} />
      </I18nProvider>,
    );

    const deriverModel = () => screen.getByRole('textbox', { name: /对话抽取模型 ID/ }) as HTMLInputElement;
    const embeddingModel = () => screen.getByRole('textbox', { name: /向量嵌入模型 ID/ }) as HTMLInputElement;
    await waitFor(() => {
      expect(embeddingModel().value).toBe('text-embedding-v4');
    });
    expect(screen.getByText(/不是分词/)).toBeTruthy();
    expect(screen.getByText(/不要随意更换嵌入模型或维度/)).toBeTruthy();
    expect(screen.getByLabelText(/向量嵌入 API 密钥/)).toHaveProperty(
      'placeholder',
      '已配置，留空则不修改',
    );

    await user.type(deriverModel(), 'deepseek-chat');
    await user.type(screen.getByLabelText(/对话抽取接口地址/), 'https://api.deepseek.com');
    await user.type(screen.getByLabelText(/对话抽取 API 密钥/), 'sk-deriver');
    await user.click(screen.getByRole('button', { name: '保存设置' }));

    await waitFor(() => expect(apiPut).toHaveBeenCalledTimes(1));
    const body = apiPut.mock.calls[0][1] as Record<string, unknown>;
    expect(body.honcho_deriver_model).toBe('deepseek-chat');
    expect(body.honcho_deriver_base_url).toBe('https://api.deepseek.com');
    expect(body.honcho_deriver_api_key).toBe('sk-deriver');
    expect(body.honcho_embedding_model).toBe('text-embedding-v4');
    expect(body.honcho_embedding_dimensions).toBe(1536);
    expect(body.honcho_embedding_api_key).toBe('');
    expect(body).not.toHaveProperty('honcho_embedding_api_key_set');
    expect(await screen.findByText(/尚未写入 Honcho/)).toBeTruthy();
  });

  it('reports a clear failure when applying to the sidecar is not configured', async () => {
    apiGet.mockResolvedValue(config);
    apiPost.mockRejectedValue(new Error('未配置 HONCHO_SIDECAR_ENV_FILE，无法把模型设置写入 Honcho。'));
    const user = userEvent.setup();

    render(
      <I18nProvider>
        <RuntimeSettingsPage currentUser={admin} />
      </I18nProvider>,
    );

    const apply = await screen.findByRole('button', { name: '应用到 sidecar' }) as HTMLButtonElement;
    await waitFor(() => expect(apply.disabled).toBe(false));
    await user.click(apply);
    await waitFor(() => expect(apiPost).toHaveBeenCalledTimes(1));
    expect(apiPost.mock.calls[0][0]).toBe('/api/enterprise/ui-config/honcho-sidecar');
    expect(apiPost.mock.calls[0][1]).toEqual({
      tenant_id: 'tenant_demo',
      honcho_embedding_dimensions_confirmed: false,
    });
    expect(await screen.findByText(/无法把模型设置写入 Honcho/)).toBeTruthy();
  });

  it('asks before saving a different embedding dimension', async () => {
    apiGet.mockResolvedValue({
      ...config,
      honcho_embedding_dimensions: 1536,
      honcho_embedding_dimensions_saved: true,
    });
    apiPut.mockImplementation(async (_url: string, body: Record<string, unknown>) => ({
      ...config,
      ...body,
      honcho_embedding_dimensions_saved: true,
      honcho_sidecar_applied: false,
      honcho_sidecar_apply_message: '',
    }));
    const user = userEvent.setup();

    render(
      <I18nProvider>
        <RuntimeSettingsPage currentUser={admin} />
      </I18nProvider>,
    );

    const dimensions = await screen.findByRole('spinbutton', { name: /向量维度/ });
    await user.clear(dimensions);
    await user.type(dimensions, '1024');
    await user.click(screen.getByRole('button', { name: '保存设置' }));

    expect(await screen.findByText('修改向量维度？')).toBeTruthy();
    expect(screen.getByText(/向量维度将从 1536 改为 1024/)).toBeTruthy();
    expect(screen.getByText(/已有向量数据需要重建向量库/)).toBeTruthy();
    expect(apiPut).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: '确认修改' }));
    await waitFor(() => expect(apiPut).toHaveBeenCalledTimes(1));
    const body = apiPut.mock.calls[0][1] as Record<string, unknown>;
    expect(body.honcho_embedding_dimensions).toBe(1024);
    expect(body.honcho_embedding_dimensions_confirmed).toBe(true);
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
