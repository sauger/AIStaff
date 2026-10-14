// @vitest-environment jsdom

import { afterEach, describe, expect, it } from 'vitest';

import { ENTERPRISE_AGENT_STORAGE_KEY } from '@/lib/agent-scope-storage';

import { buildToolProbeRequestUrl } from './ToolsPage';

afterEach(() => {
  window.localStorage.clear();
});

describe('buildToolProbeRequestUrl', () => {
  it('includes agent_id query for bound-tool probe', () => {
    window.localStorage.setItem(ENTERPRISE_AGENT_STORAGE_KEY, 'agent-1');
    expect(buildToolProbeRequestUrl()).toBe('/api/enterprise/tools/probe?agent_id=agent-1');
  });

  it('omits agent_id when employee scope is unset', () => {
    window.localStorage.removeItem(ENTERPRISE_AGENT_STORAGE_KEY);
    expect(buildToolProbeRequestUrl()).toBe('/api/enterprise/tools/probe');
  });
});
