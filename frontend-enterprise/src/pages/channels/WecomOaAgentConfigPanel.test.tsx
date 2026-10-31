// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { I18nProvider } from '@/i18n';

import WecomOaAgentConfigPanel from './WecomOaAgentConfigPanel';

function jsonResponse(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    statusText: 'OK',
    text: async () => JSON.stringify(body ?? {}),
  } as Response;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('WecomOaAgentConfigPanel applicant binding', () => {
  it('saves one WeCom user as the agent approval identity and can clear it', async () => {
    const calls: Array<{ url: string; method: string; body?: string }> = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method || 'GET';
      calls.push({ url, method, body: typeof init?.body === 'string' ? init.body : undefined });
      if (url.includes('/directory/departments/') && url.includes('/users')) {
        return jsonResponse({
          users: [
            { userid: 'zhangsan', name: '张三', email: 'zhangsan@example.com', department_ids: [16] },
            { userid: 'lisi', name: '李四', email: '', department_ids: [16] },
          ],
        });
      }
      if (url.includes('/directory/departments')) {
        return jsonResponse({
          app_name: '审批',
          departments: [{ id: 16, name: '数字化赋能中心', parent_id: 0, order: 1 }],
        });
      }
      if (method === 'PUT' && url.includes('/wecom-applicant')) {
        return jsonResponse({
          applicant: { wecom_userid: 'zhangsan', display_name: '张三', applicant_mode: 'fixed' },
        });
      }
      if (method === 'DELETE' && url.includes('/wecom-applicant')) {
        return jsonResponse({ applicant: { wecom_userid: null, display_name: null } });
      }
      if (url.includes('/approval-config')) return jsonResponse({ config: null });
      return jsonResponse({});
    }));

    const user = userEvent.setup();
    render(
      <I18nProvider>
        <WecomOaAgentConfigPanel agentId="agent-1" />
      </I18nProvider>,
    );
    expect(await screen.findByText('尚未绑定企微用户。')).toBeTruthy();
    await user.click(screen.getByRole('button', { name: '选择绑定用户' }));
    await user.click(await screen.findByRole('radio', { name: '张三 zhangsan' }));
    await user.click(screen.getByRole('button', { name: '保存绑定' }));

    const current = await screen.findByTestId('wecom-agent-applicant-current');
    expect(current.textContent).toContain('张三');
    expect(current.textContent).toContain('zhangsan');
    const saved = calls.find((call) => call.method === 'PUT' && call.url.includes('/wecom-applicant'));
    expect(saved?.body).toContain('zhangsan');
    expect(saved?.body).toContain('张三');

    await user.click(screen.getByRole('button', { name: '清空绑定' }));
    expect(await screen.findByText('尚未绑定企微用户。')).toBeTruthy();
    expect(calls.some((call) => call.method === 'DELETE' && call.url.includes('/wecom-applicant'))).toBe(true);
  });
});