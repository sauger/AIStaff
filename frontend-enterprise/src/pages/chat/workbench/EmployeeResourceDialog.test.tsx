// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { api } from '@/api/client';
import type { AgentProfileRead } from '@/types';

import EmployeeResourceDialog from './EmployeeResourceDialog';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const agent: AgentProfileRead = {
  id: 'agent-1',
  tenant_id: 'tenant_demo',
  name: '项目经理',
  description: '统筹项目目标与团队分工。',
  is_overall: false,
  status: 'active',
  metadata: {},
  resources: [
    {
      id: 'r1',
      tenant_id: 't',
      agent_id: 'agent-1',
      resource_type: 'knowledge_base',
      resource_id: 'kb-1',
      status: 'active',
      metadata: { name: '项目手册' },
      created_at: '',
      updated_at: '',
    },
    {
      id: 'r2',
      tenant_id: 't',
      agent_id: 'agent-1',
      resource_type: 'general_skill',
      resource_id: 'sk-1',
      status: 'active',
      metadata: {},
      created_at: '',
      updated_at: '',
    },
  ],
  created_at: '',
  updated_at: '',
};

const knowledgeBases = [
  { id: 'kb-1', name: '项目手册' },
  { id: 'kb-2', name: '客户拜访手册' },
];

function renderDialog() {
  const onSaved = vi.fn();
  const onOpenChange = vi.fn();
  render(
    <EmployeeResourceDialog
      agent={agent}
      openType="knowledge_base"
      onOpenChange={onOpenChange}
      onSaved={onSaved}
    />,
  );
  return { onSaved, onOpenChange };
}

describe('EmployeeResourceDialog', () => {
  it('saves the selected knowledge bases and keeps other resource types', async () => {
    const get = vi.spyOn(api, 'get').mockResolvedValue(knowledgeBases);
    const put = vi.spyOn(api, 'put').mockResolvedValue([]);
    const { onSaved, onOpenChange } = renderDialog();

    expect(String(get.mock.calls[0]?.[0])).toContain('/api/enterprise/knowledge-bases?tenant_id=');
    await screen.findByText('客户拜访手册');

    fireEvent.click(screen.getByLabelText('客户拜访手册'));
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '保存' }));
    });

    expect(put).toHaveBeenCalledTimes(1);
    const [url, body] = put.mock.calls[0] as unknown as [string, { tenant_id: string; resources: unknown[] }];
    expect(url).toBe('/api/enterprise/agents/agent-1/resources');
    expect(body.tenant_id).toBe('tenant_demo');
    const resources = body.resources as { resource_type: string; resource_id: string }[];
    expect(resources).toContainEqual(expect.objectContaining({ resource_type: 'general_skill', resource_id: 'sk-1' }));
    expect(resources.filter((item) => item.resource_type === 'knowledge_base').map((item) => item.resource_id))
      .toEqual(['kb-1', 'kb-2']);
    expect(onSaved).toHaveBeenCalled();
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it('removes a bound resource when it is unchecked', async () => {
    vi.spyOn(api, 'get').mockResolvedValue(knowledgeBases);
    const put = vi.spyOn(api, 'put').mockResolvedValue([]);
    renderDialog();

    await screen.findByText('客户拜访手册');
    fireEvent.click(screen.getByLabelText('项目手册'));
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '保存' }));
    });

    const resources = (put.mock.calls[0]?.[1] as { resources: { resource_type: string }[] }).resources;
    expect(resources.filter((item) => item.resource_type === 'knowledge_base')).toHaveLength(0);
    expect(resources).toHaveLength(1);
  });

  it('reports a failed save without closing the dialog', async () => {
    vi.spyOn(api, 'get').mockResolvedValue(knowledgeBases);
    vi.spyOn(api, 'put').mockRejectedValue(new Error('boom'));
    const { onOpenChange } = renderDialog();

    await screen.findByText('客户拜访手册');
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '保存' }));
    });

    expect(screen.getByText('保存失败，请稍后重试')).toBeTruthy();
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
  });
});
