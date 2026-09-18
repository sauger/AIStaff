// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { I18nProvider } from '@/i18n';
import { ENTERPRISE_AGENT_STORAGE_KEY } from '@/lib/agent-scope-storage';
import type { EnterpriseAuthUser } from '@/auth';
import type { AgentProfileRead, MailListResponse, MailMessageRead, MailboxStatusRead } from '@/types';

import MailPage from './MailPage';

const ownedAgent: AgentProfileRead = {
  id: 'agent-1',
  tenant_id: 'tenant_demo',
  name: '小艾',
  is_overall: false,
  status: 'active',
  metadata: { owner_user_id: 'user-owner' },
  resources: [],
  created_at: '2026-08-01T00:00:00Z',
  updated_at: '2026-08-01T00:00:00Z',
};

const otherAgent: AgentProfileRead = {
  ...ownedAgent,
  id: 'agent-2',
  name: '别人的员工',
  metadata: { owner_user_id: 'user-other' },
};

const unconfigured: MailboxStatusRead = {
  agent_id: 'agent-1',
  configured: false,
  password_configured: false,
  can_configure: true,
  can_send: true,
};

const emptyInbox: MailListResponse = {
  agent_id: 'agent-1',
  folder: 'inbox',
  configured: false,
  can_send: true,
  empty_reason: '这个员工还没有配置邮箱。请先在邮件页填写 IMAP 和 SMTP。',
  messages: [],
  page: 1,
  page_size: 20,
  total: 0,
};

const configuredMailbox: MailboxStatusRead = {
  agent_id: 'agent-1',
  configured: true,
  password_configured: true,
  can_configure: true,
  can_send: true,
  email_address: 'agent-a@example.com',
};

function mailMessage(overrides: Partial<MailMessageRead> = {}): MailMessageRead {
  return {
    id: 'mail-1',
    folder: 'inbox',
    status: 'unread',
    direction: 'inbound',
    source: 'imap',
    from_address: 'buyer@example.com',
    to: ['agent-a@example.com'],
    cc: [],
    bcc: [],
    subject: '询价',
    body_text: '',
    unread: true,
    attachments: [],
    confirm_required: false,
    created_at: '2026-09-18T08:00:00Z',
    ...overrides,
  };
}

const ownerUser: EnterpriseAuthUser = {
  id: 'user-owner',
  tenant_id: 'tenant_demo',
  username: 'owner',
  role: 'member',
};

const adminUser: EnterpriseAuthUser = {
  id: 'user-admin',
  tenant_id: 'tenant_demo',
  username: 'admin',
  role: 'admin',
};

function jsonResponse(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    statusText: 'OK',
    text: async () => JSON.stringify(body ?? {}),
  } as Response;
}

function renderMail(currentUser: EnterpriseAuthUser) {
  return render(
    <I18nProvider>
      <MemoryRouter>
        <MailPage currentUser={currentUser} />
      </MemoryRouter>
    </I18nProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

describe('MailPage', () => {
  beforeEach(() => {
    window.localStorage.setItem(ENTERPRISE_AGENT_STORAGE_KEY, 'agent-1');
  });

  it('shows an empty state that leads to mailbox setup', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/enterprise/agents')) return jsonResponse([ownedAgent]);
      if (url.includes('/api/enterprise/mail/mailbox')) return jsonResponse(unconfigured);
      if (url.includes('/api/enterprise/mail/inbox')) return jsonResponse(emptyInbox);
      return jsonResponse([]);
    }));

    renderMail(ownerUser);

    expect(await screen.findByText('邮件')).toBeTruthy();
    expect(screen.getByText('这个员工还没有配置邮箱。请先在「配置邮箱」填写 IMAP 和 SMTP。')).toBeTruthy();
    expect(screen.getByText('去配置邮箱')).toBeTruthy();
    expect(screen.getByText('收件箱')).toBeTruthy();
    expect(screen.getByText('写邮件')).toBeTruthy();
    expect(screen.getByText('已发送')).toBeTruthy();
    expect(screen.getByText('配置邮箱')).toBeTruthy();
  });

  it('hides send and config actions when an admin views another employee', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/enterprise/agents')) return jsonResponse([otherAgent]);
      if (url.includes('/api/enterprise/mail/mailbox')) {
        return jsonResponse({
          ...unconfigured,
          agent_id: 'agent-2',
          configured: true,
          email_address: 'other@example.com',
          can_configure: false,
          can_send: false,
          password_configured: true,
        });
      }
      if (url.includes('/api/enterprise/mail/inbox')) {
        return jsonResponse({
          agent_id: 'agent-2',
          folder: 'inbox',
          configured: true,
          can_send: false,
          messages: [],
          empty_reason: '收件箱是空的。',
        });
      }
      return jsonResponse([]);
    }));

    window.localStorage.setItem(ENTERPRISE_AGENT_STORAGE_KEY, 'agent-2');
    renderMail(adminUser);

    expect(await screen.findByText('管理员正在查阅其他员工的邮件，只能浏览，不能改凭证或代发。')).toBeTruthy();
    expect(screen.queryByText('保存配置')).toBeNull();
    expect(screen.queryByText('发送')).toBeNull();
  });

  it('requests one inbox page of 20 and paginates instead of rendering the whole mailbox', async () => {
    const inboxUrls: string[] = [];
    const pageOne = Array.from({ length: 20 }, (_, index) => mailMessage({
      id: `mail-${25 - index}`,
      subject: `询价 ${25 - index}`,
      unread: false,
    }));
    const pageTwo = Array.from({ length: 5 }, (_, index) => mailMessage({
      id: `mail-${5 - index}`,
      subject: `询价 ${5 - index}`,
      unread: false,
    }));
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/enterprise/agents')) return jsonResponse([ownedAgent]);
      if (url.includes('/api/enterprise/mail/mailbox')) return jsonResponse(configuredMailbox);
      if (url.includes('/api/enterprise/mail/inbox')) {
        inboxUrls.push(url);
        const page = new URL(url, 'http://localhost').searchParams.get('page');
        return jsonResponse({
          agent_id: 'agent-1',
          folder: 'inbox',
          configured: true,
          can_send: true,
          messages: page === '2' ? pageTwo : pageOne,
          page: page === '2' ? 2 : 1,
          page_size: 20,
          total: 25,
        });
      }
      return jsonResponse([]);
    }));

    const user = userEvent.setup();
    renderMail(ownerUser);

    expect(await screen.findByText('询价 25')).toBeTruthy();
    expect(screen.queryByText('询价 1')).toBeNull();
    expect(inboxUrls.some((url) => url.includes('page=1') && url.includes('page_size=20'))).toBe(true);
    expect(screen.getByLabelText('收件箱分页')).toBeTruthy();

    await user.click(screen.getByLabelText('下一页'));
    expect(await screen.findByText('询价 1')).toBeTruthy();
    expect(screen.queryByText('询价 25')).toBeNull();
    expect(inboxUrls.some((url) => url.includes('page=2') && url.includes('page_size=20'))).toBe(true);
  });

  it('opens sent-message detail with to, subject, time, body and status', async () => {
    const sentRow = mailMessage({
      id: 'sent-1',
      folder: 'sent',
      status: 'sent',
      direction: 'outbound',
      source: 'console',
      from_address: 'agent-a@example.com',
      to: ['sales@example.com'],
      subject: '报价',
      body_text: '',
      unread: false,
      sent_at: '2026-09-18T08:00:00Z',
    });
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/enterprise/agents')) return jsonResponse([ownedAgent]);
      if (url.includes('/api/enterprise/mail/mailbox')) return jsonResponse(configuredMailbox);
      if (url.includes('/api/enterprise/mail/inbox')) {
        return jsonResponse({
          agent_id: 'agent-1',
          folder: 'inbox',
          configured: true,
          can_send: true,
          messages: [],
          empty_reason: '收件箱是空的。',
          page: 1,
          page_size: 20,
          total: 0,
        });
      }
      if (url.includes('/api/enterprise/mail/sent')) {
        return jsonResponse({
          agent_id: 'agent-1',
          folder: 'sent',
          configured: true,
          can_send: true,
          messages: [sentRow],
          page: 1,
          page_size: 20,
          total: 1,
        });
      }
      if (url.includes('/api/enterprise/mail/messages/sent-1')) {
        return jsonResponse({
          ...sentRow,
          body_text: '请查收报价',
        });
      }
      return jsonResponse([]);
    }));

    const user = userEvent.setup();
    renderMail(ownerUser);
    await user.click(await screen.findByRole('tab', { name: '已发送' }));
    expect(await screen.findByText('报价')).toBeTruthy();
    await user.click(screen.getByText('报价'));
    expect(await screen.findByText('请查收报价')).toBeTruthy();
    expect(screen.getByText('收件人：sales@example.com')).toBeTruthy();
    expect(screen.getByText('状态：成功')).toBeTruthy();
    expect(screen.getByText(/^时间：/)).toBeTruthy();
  });

  it('keeps the cached inbox page when IMAP reports last_error', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/enterprise/agents')) return jsonResponse([ownedAgent]);
      if (url.includes('/api/enterprise/mail/mailbox')) return jsonResponse(configuredMailbox);
      if (url.includes('/api/enterprise/mail/inbox')) {
        return jsonResponse({
          agent_id: 'agent-1',
          folder: 'inbox',
          configured: true,
          can_send: true,
          messages: [mailMessage({ subject: '缓存来信', unread: false })],
          page: 1,
          page_size: 20,
          total: 1,
          last_error: 'IMAP 连接失败（主机 imap.example.com:993）：timed out',
        });
      }
      return jsonResponse([]);
    }));

    renderMail(ownerUser);
    expect(await screen.findByText('缓存来信')).toBeTruthy();
    expect(screen.getByText('IMAP 连接失败（主机 imap.example.com:993）：timed out')).toBeTruthy();
    expect(screen.queryByText('加载中…')).toBeNull();
  });
});
