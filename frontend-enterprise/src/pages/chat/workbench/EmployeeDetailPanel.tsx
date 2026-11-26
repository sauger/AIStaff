import { useEffect, useMemo, useState } from 'react';

import { api, TENANT_ID } from '@/api/client';
import { getEnterpriseAuthSession } from '@/auth';

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import StaffdeckIcon from '@/components/StaffdeckIcon';
import { agentResourceCount, employeeDisplayName, employeeProfile } from '@/employee';
import type {
  AgentProfileRead,
  AgentResourceType,
  ChatSession,
  HarnessWorkspaceArtifact,
} from '@/types';

import { harnessWorkspaceArtifacts, sessionHasUnreadReply } from '../chatHelpers';
import type { UseChatSession } from '../useChatSession';
import DetailSessionRow from './DetailSessionRow';
import EmployeeIdleAvatar from './EmployeeIdleAvatar';
import EmployeeResourceDialog from './EmployeeResourceDialog';
import type { YardEmployee } from './EmployeeYardCard';
import { artForRole } from './employeeArt';

const HISTORY_PAGE_SIZE = 10;

type HistorySearchPage = {
  items: ChatSession[];
  total: number;
};

/** 300px 右侧详情栏：单个员工资料、历史会话、产出与资源入口。 */
export default function EmployeeDetailPanel({
  chat,
  detailAgent,
  yardEmployee,
  sessions,
  onOpenDocument,
}: {
  chat: UseChatSession;
  detailAgent: AgentProfileRead | null;
  yardEmployee: YardEmployee | null;
  sessions: ChatSession[];
  onOpenDocument: (artifact: HarnessWorkspaceArtifact, sessionId?: string) => void;
}) {
  const {
    displayedMessages,
    sessionReadTimes,
    sessionId,
    openSession,
    openRename,
    requestDelete,
    openDraftForAgent,
    openAgentConfig,
  } = chat;
  const [tab, setTab] = useState<'history' | 'outputs'>('history');
  const [searchOpen, setSearchOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(1);
  const [searchPage, setSearchPage] = useState<HistorySearchPage | null>(null);
  const [resourceType, setResourceType] = useState<AgentResourceType | null>(null);
  const [tenantId] = useState(() => getEnterpriseAuthSession()?.user.tenant_id || TENANT_ID);
  const historyScope = `${chat.displayedTeam?.id || ''}:${detailAgent?.id || ''}`;
  const historyResetKey = `${historyScope}\n${query}`;
  const [historyPageKey, setHistoryPageKey] = useState(historyResetKey);
  if (historyPageKey !== historyResetKey) {
    setHistoryPageKey(historyResetKey);
    setPage(1);
    setSearchPage(null);
  }

  const profile = detailAgent ? employeeProfile(detailAgent) : null;
  const artId = yardEmployee?.artId
    || (profile ? artForRole(profile.roleKey, profile.roleName) : 'pm');

  const stats = detailAgent
    ? [
      { label: '资料', value: agentResourceCount(detailAgent, 'knowledge_base'), type: 'knowledge_base' as AgentResourceType },
      { label: '技能', value: agentResourceCount(detailAgent, 'general_skill'), type: 'general_skill' as AgentResourceType },
      { label: 'SOP', value: agentResourceCount(detailAgent, 'skill'), type: 'skill' as AgentResourceType },
    ]
    : [];

  const outputs = useMemo(() => {
    const rows: Array<{ artifact: HarnessWorkspaceArtifact }> = [];
    for (const message of displayedMessages) {
      if (message.role !== 'assistant') continue;
      for (const artifact of harnessWorkspaceArtifacts(message)) {
        rows.push({ artifact });
      }
    }
    return rows;
  }, [displayedMessages]);

  const term = query.trim();
  const searching = term.length > 0;
  const historySessions = searching
    ? (searchPage?.items ?? [])
    : sessions.slice((page - 1) * HISTORY_PAGE_SIZE, page * HISTORY_PAGE_SIZE);
  const historyTotal = searching ? (searchPage?.total ?? 0) : sessions.length;
  const historyPages = Math.max(1, Math.ceil(historyTotal / HISTORY_PAGE_SIZE));
  const historyPending = searching && searchPage === null;

  useEffect(() => {
    if (!term) return undefined;
    const teamId = chat.displayedTeam?.id;
    const agentId = detailAgent?.id;
    if (!teamId && !agentId) return undefined;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      const params = new URLSearchParams({
        tenant_id: tenantId,
        q: term,
        limit: String(HISTORY_PAGE_SIZE),
        offset: String((page - 1) * HISTORY_PAGE_SIZE),
      });
      if (teamId) params.set('team_id', teamId);
      else if (agentId) params.set('agent_id', agentId);
      api.get<HistorySearchPage>(`/api/chat/sessions/search?${params}`)
        .then((result) => {
          if (!cancelled) setSearchPage(result);
        })
        .catch(() => {
          if (!cancelled) setSearchPage({ items: [], total: 0 });
        });
    }, 300);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [chat.displayedTeam?.id, detailAgent?.id, page, tenantId, term]);

  return (
    <aside className="cw-detail-panel">
      <button
        type="button"
        className="cw-profile-config"
        aria-label="配置员工"
        title="配置员工"
        disabled={!detailAgent}
        onClick={() => {
          if (detailAgent) openAgentConfig(detailAgent.id);
        }}
      >
        <StaffdeckIcon name="filter" size={14} />
      </button>
      <div className="cw-profile">
        <div className="cw-profile-avatar">
          <EmployeeIdleAvatar artId={artId} color={yardEmployee?.color} name={displayName()} />
        </div>
        <strong>{displayName()}</strong>
        <p>{introText()}</p>
        <div className="cw-profile-stats">
          {stats.map((item) => (
            <button key={item.label} type="button" onClick={() => setResourceType(item.type)}>
              <strong>{item.value}</strong>
              <small>{item.label}</small>
            </button>
          ))}
        </div>
        <button
          type="button"
          className="cw-new-chat"
          disabled={!detailAgent}
          onClick={() => {
            if (detailAgent) openDraftForAgent(detailAgent.id);
          }}
        >
          <StaffdeckIcon name="plus" size={12} />
          新建对话
        </button>
      </div>
      <div className="cw-detail-tabs" role="tablist">
        <button type="button" role="tab" aria-selected={tab === 'history'} onClick={() => setTab('history')}>
          历史对话
        </button>
        <button type="button" role="tab" aria-selected={tab === 'outputs'} onClick={() => setTab('outputs')}>
          产出
        </button>
        <button
          type="button"
          className="cw-search-button"
          aria-label="搜索历史对话"
          onClick={() => setSearchOpen(!searchOpen)}
        >
          <StaffdeckIcon name="search" size={14} />
        </button>
      </div>
      {searchOpen && (
        <input
          autoFocus
          className="cw-history-search"
          aria-label="搜索会话"
          placeholder="搜索对话..."
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      )}
      <div className="cw-history-list">
        {tab === 'history' ? (
          <>
            {historySessions.map((session) => (
              <DetailSessionRow
                key={session.id}
                session={session}
                active={session.id === sessionId}
                unread={sessionHasUnreadReply(session, sessionReadTimes, sessionId)}
                onSelect={(next) => openSession(next.id)}
                onRename={openRename}
                onDelete={requestDelete}
              />
            ))}
            {!historyPending && historySessions.length === 0 && (
              <p className="cw-empty-search">没有匹配的对话</p>
            )}
            {historyTotal > HISTORY_PAGE_SIZE && (
              <div className="cw-history-pager">
                <button
                  type="button"
                  disabled={page <= 1}
                  onClick={() => setPage((current) => current - 1)}
                >
                  上一页
                </button>
                <span>{`第 ${page} / ${historyPages} 页`}</span>
                <button
                  type="button"
                  disabled={page >= historyPages}
                  onClick={() => setPage((current) => current + 1)}
                >
                  下一页
                </button>
              </div>
            )}
          </>
        ) : (
          <>
            {outputs.map(({ artifact }) => {
              const badge = artifactBadge(artifact.display_name || artifact.path);
              return (
                <div key={`${artifact.task_frame_id}:${artifact.path}`} className="cw-output-item">
                  <button
                    type="button"
                    className="cw-output-open"
                    onClick={() => onOpenDocument(artifact)}
                  >
                    <span className="cw-output-badge" style={{ background: badge.color }}>{badge.label}</span>
                    <span className="cw-output-copy">
                      <strong>{artifact.display_name || artifact.path}</strong>
                      <small>{badge.typeName}</small>
                    </span>
                  </button>
                  <DropdownMenu>
                    <DropdownMenuTrigger className="cw-history-more" aria-label={`更多操作：${artifact.display_name || artifact.path}`}>
                      <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
                        <circle cx="3" cy="7" r="1" fill="currentColor" />
                        <circle cx="7" cy="7" r="1" fill="currentColor" />
                        <circle cx="11" cy="7" r="1" fill="currentColor" />
                      </svg>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent
                      align="end"
                      className="min-w-[128px] rounded-[10px] border-0 bg-white p-[6px] shadow-[0_8px_24px_rgba(0,0,0,0.08)] ring-0 [--accent:#F6F6F6] [--accent-foreground:#18181A]"
                    >
                      <DropdownMenuItem
                        className="h-[32px] cursor-pointer rounded-[6px] px-[12px] text-[14px]"
                        onSelect={() => onOpenDocument(artifact)}
                      >
                        打开文档
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </div>
              );
            })}
            {outputs.length === 0 && <p className="cw-empty-search">暂无产出，完成任务后展示在这里。</p>}
          </>
        )}
      </div>
      <EmployeeResourceDialog
        agent={detailAgent}
        openType={resourceType}
        onOpenChange={(open) => (!open ? setResourceType(null) : undefined)}
        onSaved={() => {
          void chat.refreshAgents();
        }}
      />
    </aside>
  );

  function displayName(): string {
    return detailAgent ? employeeDisplayName(detailAgent) : '数字员工';
  }

  function introText(): string {
    if (detailAgent?.description?.trim()) return detailAgent.description;
    const roleName = profile?.roleName || '数字员工';
    return `专注${roleName}工作，协同处理项目任务，让工作信息清晰可追溯。`;
  }
}

/** 产出文件类型徽标：颜色/字母按 Figma 图1 规范（docx 蓝/xlsx 绿/ppt 橙/pdf 红/jpg 红/html 金）。 */
export function artifactBadge(path: string): { color: string; label: string; typeName: string } {
  const ext = (path.split('.').pop() || '').toLowerCase();
  const table: Record<string, { color: string; label: string; typeName: string }> = {
    docx: { color: '#0088FF', label: 'W', typeName: 'Word 文档' },
    doc: { color: '#0088FF', label: 'W', typeName: 'Word 文档' },
    xlsx: { color: '#34C759', label: 'X', typeName: 'Excel 表格' },
    csv: { color: '#34C759', label: 'X', typeName: 'CSV 表格' },
    pptx: { color: '#FF8D28', label: 'P', typeName: 'PPT 演示' },
    pdf: { color: '#FF383C', label: 'PDF', typeName: 'PDF 文档' },
    jpg: { color: '#FF383C', label: 'JPG', typeName: '图片' },
    jpeg: { color: '#FF383C', label: 'JPG', typeName: '图片' },
    png: { color: '#FF383C', label: 'PNG', typeName: '图片' },
    html: { color: '#FFCC00', label: 'HTML', typeName: 'HTML 页面' },
    md: { color: '#FFCC00', label: 'MD', typeName: 'Markdown' },
  };
  return table[ext] || { color: '#8C9092', label: 'FILE', typeName: '文件' };
}
