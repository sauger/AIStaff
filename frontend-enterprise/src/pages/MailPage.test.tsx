// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { I18nProvider } from '@/i18n';
import { ENTERPRISE_AGENT_STORAGE_KEY } from '@/lib/agent-scope-storage';
import type { EnterpriseAuthUser } from '@/auth';
import type { AgentProfileRead, MailListResponse, MailboxStatusRead } from '@/types';

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
};

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
});
