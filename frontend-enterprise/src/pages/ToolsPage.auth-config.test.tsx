// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { TooltipProvider } from '@/components/ui/tooltip';
import { I18nProvider } from '@/i18n';
import { ENTERPRISE_AGENT_STORAGE_KEY } from '@/lib/agent-scope-storage';
import type { EnterpriseAuthUser } from '@/auth';
import type { AgentProfileRead, LoginGuideListResponse } from '@/types';

import { ToolNewPage } from './ToolsPage';

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

const ownerUser: EnterpriseAuthUser = {
  id: 'user-owner',
  tenant_id: 'tenant_demo',
  username: 'owner',
  role: 'member',
};

const loginGuides: LoginGuideListResponse = {
  agent_id: 'agent-1',
  can_write: true,
  guides: [{
    id: 'elogin-gateway',
    agent_id: 'agent-1',
    name: '网关',
    url: 'https://api.example/login',
    username_selector: '',
    username_label: '用户名',
    password_selector: '',
    password_label: '密码',
    submit_selector: '',
    submit_label: '登录',
    guide_type: 'api',
    config: {},
    default_secret_name: '主账号',
    published: false,
    can_write: true,
    created_at: '2026-09-01T00:00:00Z',
    updated_at: '2026-09-01T00:00:00Z',
  }],
};

function jsonResponse(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    statusText: 'OK',
    text: async () => JSON.stringify(body ?? {}),
  } as Response;
}

function renderNewTool() {
  return render(
    <I18nProvider>
      <TooltipProvider>
        <MemoryRouter initialEntries={['/enterprise/tools/new']}>
          <Routes>
            <Route path="/enterprise/tools/new" element={<ToolNewPage currentUser={ownerUser} />} />
          </Routes>
        </MemoryRouter>
      </TooltipProvider>
    </I18nProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

describe('ToolNewPage auth config binding', () => {
  beforeEach(() => {
    window.localStorage.setItem(ENTERPRISE_AGENT_STORAGE_KEY, 'agent-1');
  });

  it('defaults to no auth config and saves without login_guide_id binding', async () => {
    const postBodies: unknown[] = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes('/api/enterprise/agents')) return jsonResponse([ownedAgent]);
      if (url.includes('/api/enterprise/login-guides') && !url.includes('/gallery')) {
        return jsonResponse(loginGuides);
      }
      if (url.includes('/api/enterprise/tools') && init?.method === 'POST') {
        postBodies.push(JSON.parse(String(init.body || '{}')));
        return jsonResponse({
          id: 'tool-new',
          tenant_id: 'tenant_demo',
          name: 'billing',
          bucket: '未分桶',
          tool_type: 'http',
          method: 'POST',
          url: 'https://api.example/billing',
          headers: {},
          auth: {},
          mcp_config: {},
          input_schema: {},
          output_schema: {},
          allowed_skills: [],
          enabled: true,
          login_guide_id: null,
          created_at: '2026-09-01T00:00:00Z',
          updated_at: '2026-09-01T00:00:00Z',
        });
      }
      if (url.includes('/api/enterprise/tools')) return jsonResponse([]);
      return jsonResponse([]);
    }));

    renderNewTool();
    expect(await screen.findByText('认证配置')).toBeTruthy();
    await userEvent.type(screen.getByLabelText('工具名称'), 'billing');
    await userEvent.type(screen.getByLabelText('URL'), 'https://api.example/billing');
    await userEvent.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() => expect(postBodies).toHaveLength(1));
    expect(postBodies[0]).toMatchObject({
      login_guide_id: null,
      tool_type: 'http',
    });
  });
});
