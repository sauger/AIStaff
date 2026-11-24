import { useState } from 'react';

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import LanguageSwitcher from '@/components/LanguageSwitcher';
import StaffdeckIcon from '@/components/StaffdeckIcon';
import { cn } from '@/lib/utils';

import { ART_ASSETS } from './employeeArt';
import './workbench.css';
import { useEmployeeAppearance } from './useEmployeeAppearance';
import type { UseChatSession } from '../useChatSession';

/**
 * 74px 工作台顶栏：品牌、外观切换、账户菜单。
 * 对话端直接进入员工工作台，不再提供员工广场入口。
 */
export default function WorkbenchHeader({ chat }: { chat: UseChatSession }) {
  const { appearance, toggle } = useEmployeeAppearance();
  const {
    auth,
    openHandoffInbox,
    openAdmin,
    logout,
    setModelSetupOpen,
  } = chat;
  const username = auth?.user?.username || '';
  const menuContentClass = 'min-w-[200px] rounded-[14px] border-0 bg-white p-[6px] text-[#464C5E] shadow-[0px_16px_15px_rgba(0,0,0,0.1)] ring-0 [--accent:#F6F6F6] [--accent-foreground:#18181A]';
  const itemClass = 'h-[36px] cursor-pointer gap-[10px] rounded-[10px] px-[12px] text-[14px] text-[#464C5E]';

  return (
    <header className="cw-workbench-header">
      <img className="cw-brand" src={ART_ASSETS.brand} alt="RapidStaff" />
      <div className="cw-account">
        <button
          type="button"
          className="cw-appearance-toggle cw-icon-button"
          onClick={toggle}
          aria-label="切换员工形象"
          title={appearance === 'original' ? '切换为小墨团' : '切换为原画形象'}
          aria-pressed={appearance === 'ink'}
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M4 8h15m-3-3 3 3-3 3M20 16H5m3-3-3 3 3 3" />
          </svg>
        </button>
        <DropdownMenu>
          <DropdownMenuTrigger
            aria-label="用户菜单"
            className="flex items-center rounded-full outline-none"
          >
            <AccountAvatar user={auth?.user} size={40} />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className={menuContentClass}>
            <div className="flex items-center gap-[10px] px-[12px] py-[8px]">
              <AccountAvatar user={auth?.user} size={36} />
              <span className="grid">
                <b className="text-[14px] font-semibold text-[#18181a]">{username || '未登录'}</b>
                <span className="text-[12px] text-[#8b8e9c]">{username ? `@${username}` : ''}</span>
              </span>
            </div>
            <DropdownMenuSeparator className="my-[4px] bg-[#f0f1f4]" />
            <div className="px-[6px] py-[4px]">
              <LanguageSwitcher className="w-full" />
            </div>
            <DropdownMenuItem className={itemClass} onSelect={() => openHandoffInbox()}>
              <StaffdeckIcon name="chat" size={16} />
              待回答
            </DropdownMenuItem>
            <DropdownMenuItem className={itemClass} onSelect={() => openAdmin()}>
              <StaffdeckIcon name="grid" size={16} />
              管理端
            </DropdownMenuItem>
            <DropdownMenuItem className={itemClass} onSelect={() => setModelSetupOpen(true)}>
              <StaffdeckIcon name="model" size={16} />
              模型设置
            </DropdownMenuItem>
            <DropdownMenuSeparator className="my-[4px] bg-[#f0f1f4]" />
            <DropdownMenuItem className={cn(itemClass, 'text-[#d83c43]')} onSelect={logout}>
              <StaffdeckIcon name="logout" size={16} />
              退出登录
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  );
}

function AccountAvatar({
  user,
  size,
}: {
  user?: { username?: string; avatar_url?: string } | null;
  size: number;
}) {
  const [failed, setFailed] = useState(false);
  const url = user?.avatar_url || '';
  const renderable = /^(https?:|blob:|data:image\/)/i.test(url);
  const initial = user?.username ? user.username.slice(0, 1).toUpperCase() : '--';
  if (renderable && !failed) {
    return (
      <img
        src={url}
        alt=""
        width={size}
        height={size}
        className="shrink-0 rounded-full object-cover"
        style={{ width: size, height: size }}
        onError={() => setFailed(true)}
      />
    );
  }
  return (
    <span
      className="grid shrink-0 place-items-center overflow-hidden rounded-full bg-[#eef1fb] font-medium text-[#7e96dc]"
      style={{ width: size, height: size, fontSize: size >= 40 ? 14 : 13 }}
    >
      {initial}
    </span>
  );
}
