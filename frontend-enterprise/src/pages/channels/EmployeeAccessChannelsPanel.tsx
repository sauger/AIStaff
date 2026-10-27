import { useCallback, useEffect, useState } from 'react';

import { Button as UIButton } from '@/components/ui/button';
import { notify } from '@/components/ui/app-toast';
import type { ChannelBindingRead } from '@/types';

import { api, TENANT_ID } from '../../api/client';

type MailboxStatus = {
  configured?: boolean;
  enabled?: boolean;
  address?: string | null;
};

type Props = {
  agentId: string;
  onOpenChannel?: (binding: ChannelBindingRead) => void;
  onCreateChannel?: () => void;
  onOpenMail?: () => void;
};

const CHANNEL_LABELS: Record<string, string> = {
  wechat: '微信',
  wecom: '企业微信',
  dingtalk: '钉钉',
  feishu: '飞书',
  wechat_kf: '微信客服',
};

export default function EmployeeAccessChannelsPanel({
  agentId,
  onOpenChannel,
  onCreateChannel,
  onOpenMail,
}: Props) {
  const [bindings, setBindings] = useState<ChannelBindingRead[]>([]);
  const [mailbox, setMailbox] = useState<MailboxStatus | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    if (!agentId) return;
    setLoading(true);
    try {
      const [rows, box] = await Promise.all([
        api.get<ChannelBindingRead[]>(
          `/api/enterprise/channels?tenant_id=${encodeURIComponent(TENANT_ID)}&agent_id=${encodeURIComponent(agentId)}`,
        ),
        api.get<MailboxStatus>(
          `/api/enterprise/mail/mailbox?tenant_id=${encodeURIComponent(TENANT_ID)}&agent_id=${encodeURIComponent(agentId)}`,
        ).catch(() => null),
      ]);
      setBindings((rows || []).filter((row) => !row.team_id));
      setMailbox(box);
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '加载接入渠道失败');
    } finally {
      setLoading(false);
    }
  }, [agentId]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div
      data-testid="employee-access-channels-panel"
      className="rounded-[14px] border border-[#e3e7f1] bg-[#fafbfc] p-[14px]"
    >
      <div className="mb-[10px] flex items-center justify-between gap-[8px]">
        <strong className="text-[13px] text-[#18181a]">接入渠道</strong>
        {onCreateChannel ? (
          <UIButton
            type="button"
            variant="outline"
            className="h-7 rounded-[8px] px-3 text-[12px]"
            onClick={onCreateChannel}
          >
            新建渠道
          </UIButton>
        ) : null}
      </div>
      <p className="m-0 mb-[10px] text-[11px] leading-[1.5] text-muted-foreground">
        该员工自己的企微 / 钉钉 / 飞书 / 微信客服 / 邮件。一通道只服务本员工；转移请到「渠道管理」。
      </p>
      {loading ? (
        <div className="text-[12px] text-muted-foreground">加载中…</div>
      ) : (
        <ul className="m-0 flex list-none flex-col gap-[8px] p-0">
          {bindings.map((binding) => (
            <li
              key={binding.id}
              className="flex items-center justify-between gap-[8px] rounded-[10px] border border-[#e3e7f1] bg-white px-[10px] py-[8px]"
            >
              <div className="min-w-0">
                <div className="truncate text-[13px] text-[#18181a]">
                  {binding.name || CHANNEL_LABELS[binding.channel] || binding.channel}
                </div>
                <div className="text-[11px] text-muted-foreground">
                  {CHANNEL_LABELS[binding.channel] || binding.channel}
                  {' · '}
                  {binding.connected ? '已连接' : binding.status}
                </div>
              </div>
              {onOpenChannel ? (
                <UIButton
                  type="button"
                  variant="outline"
                  className="h-7 shrink-0 rounded-[8px] px-3 text-[12px]"
                  onClick={() => onOpenChannel(binding)}
                >
                  管理
                </UIButton>
              ) : null}
            </li>
          ))}
          <li className="flex items-center justify-between gap-[8px] rounded-[10px] border border-[#e3e7f1] bg-white px-[10px] py-[8px]">
            <div className="min-w-0">
              <div className="text-[13px] text-[#18181a]">邮箱</div>
              <div className="text-[11px] text-muted-foreground">
                {mailbox?.configured
                  ? `${mailbox.address || '已配置'}${mailbox.enabled === false ? ' · 已停用' : ''}`
                  : '未配置'}
              </div>
            </div>
            {onOpenMail ? (
              <UIButton
                type="button"
                variant="outline"
                className="h-7 shrink-0 rounded-[8px] px-3 text-[12px]"
                onClick={onOpenMail}
              >
                配置
              </UIButton>
            ) : null}
          </li>
          {!bindings.length && !mailbox?.configured ? (
            <li className="text-[12px] text-muted-foreground">尚未接入任何渠道。</li>
          ) : null}
        </ul>
      )}
    </div>
  );
}
