import { useState } from 'react';

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import StaffdeckIcon from '@/components/StaffdeckIcon';
import { cn } from '@/lib/utils';
import { staffdeckDisplayText } from '@/employee';
import type { ChatSession } from '@/types';

export type DetailSessionStatus =
  | { kind: 'idle' }
  | { kind: 'running' }
  | { kind: 'failed' }
  | { kind: 'pending-answer' };

export function detailSessionStatus(session: ChatSession, active: boolean): DetailSessionStatus {
  if (session.status === 'running' || session.status === 'executing') return { kind: 'running' };
  if (session.status === 'failed' || session.status === 'error') return { kind: 'failed' };
  if (!active && session.last_agent_question) return { kind: 'pending-answer' };
  return { kind: 'idle' };
}

/** 详情栏会话行：标题 + 状态（待回答/进行中/失败）+ 未读点 + 当前高亮 + …菜单。 */
export default function DetailSessionRow({
  session,
  active,
  unread,
  onSelect,
  onRename,
  onDelete,
}: {
  session: ChatSession;
  active: boolean;
  unread: boolean;
  onSelect: (session: ChatSession) => void;
  onRename: (session: ChatSession) => void;
  onDelete: (session: ChatSession) => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const status = detailSessionStatus(session, active);
  const title = session.title ? staffdeckDisplayText(session.title) : session.id;

  return (
    <div className={cn('cw-history-item', active && 'active')}>
      <button type="button" className="cw-history-select" onClick={() => onSelect(session)}>
        <span>{title}</span>
        {status.kind === 'running' && (
          <span className="cw-history-status is-running" role="status">
            <svg className="cw-history-spinner" width="12" height="12" viewBox="0 0 16 16" aria-hidden="true">
              <circle cx="8" cy="8" r="5.5" fill="none" stroke="currentColor" strokeOpacity=".18" strokeWidth="1.5" />
              <path d="M8 2.5A5.5 5.5 0 0 1 13.5 8" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
            进行中
          </span>
        )}
        {status.kind === 'pending-answer' && <span className="cw-history-status is-pending">待回答</span>}
        {status.kind === 'failed' && <span className="cw-history-status is-failed">模型调用失败</span>}
        {unread && <span className="cw-unread-dot" aria-label="未读回复" />}
      </button>
      <DropdownMenu open={menuOpen} onOpenChange={setMenuOpen}>
        <DropdownMenuTrigger
          className="cw-history-more"
          aria-label={`更多操作：${title}`}
        >
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
            onSelect={() => onRename(session)}
          >
            编辑
          </DropdownMenuItem>
          <DropdownMenuItem
            className="h-[32px] cursor-pointer rounded-[6px] px-[12px] text-[14px] text-[#d83c43]"
            onSelect={() => onDelete(session)}
          >
            删除
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}
