import { useState } from 'react';

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { useI18n } from '@/i18n';
import StaffdeckIcon from '@/components/StaffdeckIcon';
import { cn } from '@/lib/utils';

import './workbench.css';
import { useEmployeeAppearance } from './useEmployeeAppearance';
import type { UseChatSession } from '../useChatSession';

const MENU_CONTENT_CLASS = 'w-[320px] rounded-[20px] border-0 bg-white p-[8px] text-[#464C5E] shadow-[0_16px_40px_rgba(0,0,0,0.14)] ring-0 [--accent:#F6F6F6] [--accent-foreground:#18181A]';
const ITEM_CLASS = 'h-[46px] cursor-pointer gap-[12px] rounded-[12px] px-[12px] text-[15px] text-[#464C5E]';

/**
 * 左侧员工栏底部的个人中心。点击整行打开账户菜单；
 * 折叠态只保留头像，仍可打开同一菜单。
 */
export default function WorkbenchAccount({
  chat,
  collapsed = false,
}: {
  chat: UseChatSession;
  collapsed?: boolean;
}) {
  const { appearance, toggle } = useEmployeeAppearance();
  const { t, locale, setLocale } = useI18n();
  const {
    auth,
    openHandoffInbox,
    openAdmin,
    logout,
    setModelSetupOpen,
  } = chat;
  const username = auth?.user?.username || '';
  const displayName = auth?.user?.display_name || username || '未登录';

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        aria-label="用户菜单"
        className={cn('cw-account-trigger', collapsed && 'cw-account-trigger-collapsed')}
      >
        <AccountAvatar user={auth?.user} size={48} />
        {!collapsed && (
          <>
            <span className="cw-account-name">{displayName}</span>
            <svg
              className="cw-account-chevron"
              width="16"
              height="16"
              viewBox="0 0 16 16"
              fill="none"
              aria-hidden="true"
            >
              <circle cx="8" cy="3.2" r="1.2" fill="currentColor" />
              <circle cx="8" cy="8" r="1.2" fill="currentColor" />
              <circle cx="8" cy="12.8" r="1.2" fill="currentColor" />
            </svg>
          </>
        )}
      </DropdownMenuTrigger>
      <DropdownMenuContent side="right" align="end" sideOffset={12} className={MENU_CONTENT_CLASS}>
        <div className="flex items-center gap-[14px] px-[12px] py-[10px]">
          <AccountAvatar user={auth?.user} size={48} />
          <span className="grid min-w-0 gap-[2px]">
            <b className="truncate text-[18px] font-semibold leading-[24px] text-[#18181a]">{displayName}</b>
            <span className="truncate text-[14px] leading-[20px] text-[#9aa0ad]">{username}</span>
          </span>
        </div>
        <DropdownMenuSeparator className="my-[6px] bg-[#f0f1f4]" />
        <DropdownMenuItem className={ITEM_CLASS} onSelect={() => openHandoffInbox()}>
          <StaffdeckIcon name="chat" size={18} />
          {t('待回答')}
        </DropdownMenuItem>
        <DropdownMenuItem className={ITEM_CLASS} onSelect={() => openAdmin()}>
          <StaffdeckIcon name="grid" size={18} />
          {t('管理端')}
        </DropdownMenuItem>
        <DropdownMenuItem className={ITEM_CLASS} onSelect={() => setModelSetupOpen(true)}>
          <StaffdeckIcon name="model" size={18} />
          {t('模型设置')}
        </DropdownMenuItem>
        <DropdownMenuItem className={ITEM_CLASS} onSelect={() => toggle()}>
          <span className="flex w-[40px] shrink-0 items-center gap-[4px]">
            <PersonIcon active={appearance === 'original'} />
            <GhostIcon active={appearance === 'ink'} />
          </span>
          {t('切换形象')}
        </DropdownMenuItem>
        <div className="flex h-[46px] items-center gap-[12px] px-[12px]">
          <GlobeIcon />
          <span className="text-[15px] text-[#464C5E]">{t('切换语言')}</span>
          <span className="ml-auto inline-flex h-[30px] items-center rounded-full bg-[#f4f5f7] p-[3px]" data-i18n-ignore>
            <button
              type="button"
              data-i18n-ignore
              aria-pressed={locale === 'zh-CN'}
              onClick={() => setLocale('zh-CN')}
              className={cn(
                'h-[24px] rounded-full px-[12px] text-[13px] leading-[24px] transition-colors',
                locale === 'zh-CN' ? 'bg-white text-[#18181a] shadow-[0_1px_4px_rgba(0,0,0,0.12)]' : 'text-[#9aa0ad]',
              )}
            >
              中文
            </button>
            <button
              type="button"
              aria-pressed={locale === 'en-US'}
              onClick={() => setLocale('en-US')}
              className={cn(
                'h-[24px] rounded-full px-[12px] text-[13px] leading-[24px] transition-colors',
                locale === 'en-US' ? 'bg-white text-[#18181a] shadow-[0_1px_4px_rgba(0,0,0,0.12)]' : 'text-[#9aa0ad]',
              )}
            >
              EN
            </button>
          </span>
        </div>
        <DropdownMenuSeparator className="my-[6px] bg-[#f0f1f4]" />
        <DropdownMenuItem className={cn(ITEM_CLASS, 'text-[#e5484d]')} onSelect={logout}>
          <StaffdeckIcon name="logout" size={18} />
          {t('退出登录')}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function PersonIcon({ active }: { active: boolean }) {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 16 16"
      fill="none"
      className={active ? 'text-[#18181a]' : 'text-[#b9bec9]'}
      aria-hidden="true"
    >
      <circle cx="8" cy="5" r="2.5" stroke="currentColor" strokeWidth="1.3" />
      <path d="M3.3 13.6c0-2.6 2.1-4.5 4.7-4.5s4.7 1.9 4.7 4.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
    </svg>
  );
}

function GhostIcon({ active }: { active: boolean }) {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 16 16"
      fill="none"
      className={active ? 'text-[#18181a]' : 'text-[#b9bec9]'}
      aria-hidden="true"
    >
      <path d="M3.2 13.2V7.4a4.8 4.8 0 0 1 9.6 0v5.8l-2-1.2-1.8 1.2-1.8-1.2-2 1.2Z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
      <circle cx="6.2" cy="7.2" r=".7" fill="currentColor" />
      <circle cx="9.8" cy="7.2" r=".7" fill="currentColor" />
    </svg>
  );
}

function GlobeIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" fill="none" className="shrink-0 text-[#464C5E]" aria-hidden="true">
      <circle cx="9" cy="9" r="7" stroke="currentColor" strokeWidth="1.3" />
      <path d="M2.3 9h13.4M9 2c2 2 2.8 4.3 2.8 7s-.8 5-2.8 7c-2-2-2.8-4.3-2.8-7S7 4 9 2Z" stroke="currentColor" strokeWidth="1.2" />
    </svg>
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
