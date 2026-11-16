// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import PersonWecomNotification, { MyWecomNotification } from './PersonWecomNotification';

const empty = { binding_status: 'unbound', reachability: 'unverified', last_delivery: null };
function setup(initial = empty) {
  let status = initial;
  const fetch = vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
    const url = String(input);
    let result: unknown = status;
    if (url.includes('notification-self')) result = { person_id: 'p' };
    else if (url.includes('notification-channels')) result = { channels: [{ id: 'bot', name: '财务机器人', scope: 'corp', connected: true }] };
    else if (url.includes('notification-contacts')) result = { contacts: [{ id: 'c', name: '张三', userid: 'wx123', scope: 'corp' }] };
    else if (url.endsWith('/code')) result = { command: '/通知绑定 TEST', expires_at: '2026-11-16T14:00:00Z' };
    else if (options?.method === 'PUT') status = { ...empty, binding_status: 'bound' };
    else if (options?.method === 'DELETE') status = empty;
    return { ok: true, text: async () => JSON.stringify(result) } as Response;
  });
  vi.stubGlobal('fetch', fetch);
  return fetch;
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it('requires explicit channel and contact selection and shows unverified after binding', async () => {
  const fetch = setup(); const user = userEvent.setup();
  render(<PersonWecomNotification personId="p" admin />);
  await screen.findByText('未绑定 · 未验证');
  expect((screen.getByRole('button', { name: '绑定所选成员' }) as HTMLButtonElement).disabled).toBe(true);
  await user.selectOptions(screen.getByLabelText('通知通道'), 'bot');
  await screen.findByText('张三 · wx123 · corp');
  await user.selectOptions(screen.getByLabelText('已识别的企微成员'), 'c');
  await user.click(screen.getByRole('button', { name: '绑定所选成员' }));
  await screen.findByText('已绑定 · 未验证');
  expect(fetch.mock.calls.some(([, options]) => options?.method === 'PUT' && JSON.parse(String(options.body)).contact_id === 'c')).toBe(true);
  await user.click(screen.getByRole('button', { name: '解除通知绑定' }));
  await screen.findByText('未绑定 · 未验证');
});

it('allows self claim but never exposes administrator contact selection', async () => {
  setup(); const user = userEvent.setup();
  render(<MyWecomNotification />);
  await screen.findByText('未绑定 · 未验证');
  expect(screen.queryByLabelText('已识别的企微成员')).toBeNull();
  expect(screen.queryByRole('button', { name: '绑定所选成员' })).toBeNull();
  await user.selectOptions(screen.getByLabelText('通知通道'), 'bot');
  await user.click(screen.getByRole('button', { name: '生成通知认领码' }));
  await screen.findByText('/通知绑定 TEST');
});

it('shows authorization failures instead of presenting another employee controls', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 403, statusText: 'Forbidden', text: async () => JSON.stringify({ detail: '只能管理本人的企微通知。' }) })));
  render(<PersonWecomNotification personId="other" self />);
  await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('只能管理本人'));
  expect(screen.queryByRole('button', { name: '发送测试通知' })).toBeNull();
});
