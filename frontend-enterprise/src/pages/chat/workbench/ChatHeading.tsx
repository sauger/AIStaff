import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import StaffdeckIcon from '@/components/StaffdeckIcon';
import { cn } from '@/lib/utils';
import { staffdeckDisplayText } from '@/employee';

import { modelDisplayName } from '../chatHelpers';
import type { UseChatSession } from '../useChatSession';

/**
 * 64px 会话标题栏：标题 + 群聊徽标 + 重命名 + 「…」菜单
 * （模型选择、定时任务、重命名/删除）；移动端提供员工/会话抽屉按钮。
 */
export default function ChatHeading({
  chat,
  onOpenEmployees,
  onOpenDetails,
  onRequestDelete,
}: {
  chat: UseChatSession;
  onOpenEmployees?: () => void;
  onOpenDetails?: () => void;
  onRequestDelete?: () => void;
}) {
  const {
    currentSession,
    openRename,
    selectedModelConfig,
    setComposerIntent,
    setModelSetupOpen,
    teams,
  } = chat;
  const teamId = currentSession?.team_id || null;
  const rawName = currentSession?.title
    ? staffdeckDisplayText(currentSession.title)
    : '新对话';
  const name = teamId
    ? rawName.replace(/^团队\s*/, '').replace(/\s*·\s*TL 对话$/, '')
    : rawName;
  const team = teamId ? (teams ?? []).find((item) => item.id === teamId) || null : null;
  const memberCount = team?.members.length || 0;

  const menuContentClass = 'min-w-[240px] rounded-[14px] border-0 bg-white p-[6px] text-[#464C5E] shadow-[0px_16px_15px_rgba(0,0,0,0.1)] ring-0 [--accent:#F6F6F6] [--accent-foreground:#18181A]';
  const itemClass = 'h-[36px] cursor-pointer gap-[10px] rounded-[10px] px-[12px] text-[14px] text-[#464C5E]';

  return (
    <div className="cw-chat-heading">
      <button type="button" className="cw-mobile-employee-toggle" aria-label="打开员工列表" onClick={onOpenEmployees}>
        员工
      </button>
      <h1 className="cw-chat-heading-title" title={name}>{name}</h1>
      {teamId && (
        <span className="cw-member-chip">{memberCount ? `${memberCount}名成员` : '群聊'}</span>
      )}
      {currentSession && !teamId && (
        <button
          type="button"
          aria-label="重命名会话"
          onClick={() => openRename(currentSession)}
          className="cw-heading-icon"
        >
          <StaffdeckIcon name="edit" size={14} />
        </button>
      )}
      <button type="button" className="cw-mobile-detail-toggle" aria-label="打开员工详情和会话" onClick={onOpenDetails}>
        会话
      </button>
      <DropdownMenu>
        <DropdownMenuTrigger
          aria-label="会话信息"
          className="cw-heading-icon cw-chat-info"
        >
          <StaffdeckIcon name="more" size={16} />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className={menuContentClass}>
          <DropdownMenuItem
            className={cn('h-auto cursor-pointer items-start justify-between gap-[16px] rounded-[10px] px-[12px] py-[8px]')}
            onSelect={() => setModelSetupOpen(true)}
          >
            <span className="grid min-w-0 gap-px">
              <span className="truncate text-[13px] font-medium text-[#18181a]">
                {selectedModelConfig ? modelDisplayName(selectedModelConfig) : '默认模型'}
              </span>
              <span className="truncate text-[11px] text-[#858b9c]">模型设置</span>
            </span>
            <StaffdeckIcon name="model" size={15} />
          </DropdownMenuItem>
          <DropdownMenuSeparator className="my-[4px] bg-[#f0f1f4]" />
          <DropdownMenuItem className={itemClass} onSelect={() => setComposerIntent('scheduled_task')}>
            <StaffdeckIcon name="clock" size={16} />
            定时任务
          </DropdownMenuItem>
          {currentSession && !teamId && (
            <DropdownMenuItem className={itemClass} onSelect={() => openRename(currentSession)}>
              <StaffdeckIcon name="edit" size={16} />
              重命名会话
            </DropdownMenuItem>
          )}
          {currentSession && onRequestDelete && (
            <DropdownMenuItem className={cn(itemClass, 'text-[#d83c43]')} onSelect={onRequestDelete}>
              <StaffdeckIcon name="trash" size={16} />
              删除会话
            </DropdownMenuItem>
          )}
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}
