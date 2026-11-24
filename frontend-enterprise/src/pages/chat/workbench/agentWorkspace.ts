import type { ChatSession } from '@/types';

/**
 * 员工个人工作区要打开的最近一次会话。
 * 团队群聊不属于某个员工的个人工作区，不参与比较。
 */
export function latestAgentSession(sessions: ChatSession[], agentId: string): ChatSession | null {
  if (!agentId) return null;
  let latest: ChatSession | null = null;
  let latestAt = Number.NEGATIVE_INFINITY;
  for (const session of sessions) {
    if (session.agent_id !== agentId || session.team_id) continue;
    const parsed = Date.parse(session.updated_at || '');
    const stamp = Number.isNaN(parsed) ? 0 : parsed;
    if (!latest || stamp >= latestAt) {
      latest = session;
      latestAt = stamp;
    }
  }
  return latest;
}
