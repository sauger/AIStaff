import { describe, expect, it } from 'vitest';

import type { ChatSession } from '@/types';

import { latestAgentSession } from './agentWorkspace';

function session(partial: Partial<ChatSession>): ChatSession {
  return {
    id: partial.id || 'session-1',
    tenant_id: 'tenant_demo',
    status: 'active',
    updated_at: '2026-08-01T00:00:00Z',
    ...partial,
  };
}

describe('latestAgentSession', () => {
  it('returns the newest personal session for the employee', () => {
    const older = session({ id: 'old', agent_id: 'agent-1', updated_at: '2026-08-01T00:00:00Z' });
    const newer = session({ id: 'new', agent_id: 'agent-1', updated_at: '2026-08-03T00:00:00Z' });
    const other = session({ id: 'other', agent_id: 'agent-2', updated_at: '2026-08-04T00:00:00Z' });
    expect(latestAgentSession([older, other, newer], 'agent-1')?.id).toBe('new');
  });

  it('ignores team conversations', () => {
    const team = session({
      id: 'team',
      agent_id: 'agent-1',
      team_id: 'team-1',
      updated_at: '2026-08-05T00:00:00Z',
    });
    const personal = session({ id: 'personal', agent_id: 'agent-1', updated_at: '2026-08-01T00:00:00Z' });
    expect(latestAgentSession([team, personal], 'agent-1')?.id).toBe('personal');
  });

  it('returns null when the employee has no personal session', () => {
    expect(latestAgentSession([], 'agent-1')).toBeNull();
    expect(latestAgentSession([session({ id: 'other', agent_id: 'agent-2' })], 'agent-1')).toBeNull();
  });
});
