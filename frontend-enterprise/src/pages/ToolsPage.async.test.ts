import { describe, expect, it } from 'vitest';

import { buildToolPayload, TOOL_FORM_INITIAL_VALUES } from './ToolsPage';

function values() {
  return {
    ...TOOL_FORM_INITIAL_VALUES,
    name: 'orders.submit',
    display_name: '提交订单',
    description: '提交订单任务',
    allowed_skills: '',
    url: 'https://provider.test/tasks',
  };
}

describe('detached HTTP tool configuration', () => {
  it('keeps provider tracking fields for provider-managed tasks', () => {
    const payload = buildToolPayload({
      ...values(),
      execution_mode: 'detached',
      async_strategy: 'provider_task',
      status_url: 'https://provider.test/tasks/{taskId}',
    });

    expect(payload?.execution_policy).toMatchObject({
      execution_mode: 'detached',
      async_strategy: 'provider_task',
      status_url: 'https://provider.test/tasks/{taskId}',
      task_id_field: 'taskId',
    });
  });

  it('sends null login_guide_id when auth config is not selected', () => {
    const payload = buildToolPayload({
      ...values(),
      login_guide_id: '',
    });

    expect(payload?.login_guide_id).toBeNull();
  });

  it('sends login_guide_id when an auth config is selected', () => {
    const payload = buildToolPayload({
      ...values(),
      login_guide_id: 'elogin-gateway',
    });

    expect(payload?.login_guide_id).toBe('elogin-gateway');
  });

  it('does not persist provider status URLs for RapidStaff-managed tasks', () => {
    const payload = buildToolPayload({
      ...values(),
      execution_mode: 'detached',
      async_strategy: 'staffdeck_worker',
      status_url: 'https://stale.example/tasks/{taskId}',
    });

    expect(payload?.execution_policy).toMatchObject({
      execution_mode: 'detached',
      async_strategy: 'staffdeck_worker',
      status_url: null,
    });
  });
});
