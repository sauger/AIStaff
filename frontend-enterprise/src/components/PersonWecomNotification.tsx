import { useEffect, useState } from 'react';
import { api, TENANT_ID } from '@/api/client';
import { Button } from '@/components/ui/button';

interface NotificationStatus {
  binding_status: string;
  reachability: string;
  contact_name: string | null;
  member_userid: string | null;
  scope: string | null;
  scope_label?: string | null;
  channel_name: string | null;
  verified_at: string | null;
  reason: string | null;
  last_delivery: { status: string; error: string | null } | null;
}
interface Channel { id: string; name: string; scope: string; scope_label?: string; connected: boolean }
interface Contact { id: string; name: string; userid: string; scope: string; scope_label?: string }
const labels: Record<string, string> = {
  unbound: '未绑定', bound: '已绑定', conflict: '身份冲突', inactive: '人员停用',
  unverified: '未验证', test_succeeded: '最近测试成功', channel_offline: '通道离线',
  target_missing: '缺少有效投递目标', failed: '发送失败', pending: '等待发送',
  sending: '发送中', delivered: '渠道已接受',
};

export default function PersonWecomNotification({ personId, admin = false, self = false }: {
  personId: string; admin?: boolean; self?: boolean;
}) {
  const [status, setStatus] = useState<NotificationStatus | null>(null);
  const [channels, setChannels] = useState<Channel[]>([]);
  const [channelId, setChannelId] = useState('');
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [contactId, setContactId] = useState('');
  const [command, setCommand] = useState('');
  const [expiresAt, setExpiresAt] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [refresh, setRefresh] = useState(0);
  const base = `/api/enterprise/org/people/${encodeURIComponent(personId)}/wecom-notification`;
  const tenant = `tenant_id=${encodeURIComponent(TENANT_ID)}`;

  useEffect(() => {
    let live = true;
    setStatus(null);
    setCommand('');
    setError('');
    Promise.all([
      api.get<NotificationStatus>(`${base}?${tenant}`),
      api.get<{ channels: Channel[] }>(`/api/enterprise/org/notification-channels?${tenant}`),
    ]).then(([next, options]) => {
      if (!live) return;
      setStatus(next);
      setChannels(options.channels || []);
    }).catch((reason) => { if (live) setError(reason.message); });
    return () => { live = false; };
  }, [base, tenant, refresh]);

  useEffect(() => {
    let live = true;
    setContacts([]);
    setContactId('');
    if (admin && channelId) {
      api.get<{ contacts: Contact[] }>(`/api/enterprise/org/notification-contacts?${tenant}&binding_id=${encodeURIComponent(channelId)}`)
        .then((result) => { if (live) setContacts(result.contacts || []); })
        .catch((reason) => { if (live) setError(reason.message); });
    }
    return () => { live = false; };
  }, [admin, channelId, tenant]);

  useEffect(() => {
    if (!['pending', 'sending'].includes(status?.last_delivery?.status || '')) return;
    const timer = window.setTimeout(() => setRefresh((value) => value + 1), 2000);
    return () => window.clearTimeout(timer);
  }, [status]);

  async function act(action: () => Promise<unknown>) {
    setBusy(true);
    setError('');
    try { await action(); } catch (reason) {
      setError(reason instanceof Error ? reason.message : '操作失败');
    } finally { setBusy(false); }
  }

  return <section className="flex flex-col gap-3 rounded-xl border border-[#e3e7f1] p-4 text-sm" aria-label="企微通知">
    <h3 className="font-medium">企微通知</h3>
    <p className="text-xs text-muted-foreground">仅用于接收通知，登录账号与审批身份保持不变。</p>
    {error && <p role="alert" className="text-red-600" data-i18n-ignore>{error}</p>}
    {status && <>
      <p data-i18n-ignore>{labels[status.binding_status] || status.binding_status} · {labels[status.reachability] || status.reason || labels.unverified}</p>
      {status.contact_name && <p data-i18n-ignore>{[status.contact_name, status.member_userid, status.scope_label || status.scope, status.channel_name].filter(Boolean).join(' · ')}</p>}
      {status.verified_at && <p>验证时间：<span data-i18n-ignore>{new Date(status.verified_at).toLocaleString()}</span></p>}
      {status.reason && <p data-i18n-ignore>{status.reason}</p>}
      {status.last_delivery && <p>最近投递：<span data-i18n-ignore>{labels[status.last_delivery.status]} {status.last_delivery.error}</span></p>}
      <p className="text-xs text-muted-foreground">渠道接受消息不代表对方已阅读。</p>
    </>}
    <label className="flex flex-col gap-1">通知通道
      <select aria-label="通知通道" value={channelId} onChange={(event) => { setChannelId(event.target.value); setCommand(''); }} className="rounded border p-2" disabled={busy}>
        <option value="">请选择机器人及企业范围</option>
        {channels.map((channel) => <option key={channel.id} value={channel.id} data-i18n-ignore>{channel.name} · {channel.scope_label || channel.scope}{channel.connected ? '' : ' · ' + labels.channel_offline}</option>)}
      </select>
    </label>
    {admin && <label className="flex flex-col gap-1">已识别的企微成员
      <select aria-label="已识别的企微成员" value={contactId} onChange={(event) => setContactId(event.target.value)} className="rounded border p-2" disabled={busy || !channelId}>
        <option value="">请选择并核对成员身份</option>
        {contacts.map((contact) => <option key={contact.id} value={contact.id} data-i18n-ignore>{contact.name} · {contact.userid} · {contact.scope_label || contact.scope}</option>)}
      </select>
      {channelId && contacts.length === 0 && <span className="text-xs text-muted-foreground">没有已识别的私聊成员，请先私聊所选机器人。</span>}
    </label>}
    <div className="flex flex-wrap gap-2">
      {admin && <Button size="sm" disabled={busy || !contactId || status?.binding_status === 'inactive'} onClick={() => void act(async () => {
        await api.put(base, { tenant_id: TENANT_ID, contact_id: contactId, binding_id: channelId });
        setRefresh((value) => value + 1);
      })}>绑定所选成员</Button>}
      {self && <Button size="sm" variant="outline" disabled={busy || !channelId || status?.binding_status === 'inactive'} onClick={() => void act(async () => {
        const code = await api.post<{ command: string; expires_at: string }>(`${base}/code`, { tenant_id: TENANT_ID, binding_id: channelId });
        setCommand(code.command); setExpiresAt(code.expires_at);
      })}>生成通知认领码</Button>}
      {status?.binding_status === 'bound' && <Button size="sm" variant="outline" disabled={busy} onClick={() => void act(async () => {
        await api.post(`${base}/test`, { tenant_id: TENANT_ID, request_id: crypto.randomUUID() });
        setRefresh((value) => value + 1);
      })}>发送测试通知</Button>}
      {status && status.binding_status !== 'unbound' && <Button size="sm" variant="outline" disabled={busy} onClick={() => void act(async () => {
        await api.delete(`${base}?${tenant}`); setRefresh((value) => value + 1);
      })}>解除通知绑定</Button>}
      <Button size="sm" variant="outline" disabled={busy} onClick={() => setRefresh((value) => value + 1)}>刷新状态</Button>
    </div>
    {command && <div className="rounded bg-[#f6f7fa] p-3 text-[#17191f]">
      <p>将以下指令发送到所选机器人的企微私聊，十分钟内有效，仅可使用一次。</p>
      <code className="block break-all select-all" data-i18n-ignore>{command}</code>
      <p>到期时间：<span data-i18n-ignore>{new Date(expiresAt).toLocaleString()}</span></p>
    </div>}
  </section>;
}

export function MyWecomNotification() {
  const [personId, setPersonId] = useState<string | null>();
  const [error, setError] = useState('');
  useEffect(() => {
    let live = true;
    api.get<{ person_id: string | null }>(`/api/enterprise/org/notification-self?tenant_id=${encodeURIComponent(TENANT_ID)}`)
      .then((result) => { if (live) setPersonId(result.person_id); })
      .catch((reason) => { if (live) setError(reason.message); });
    return () => { live = false; };
  }, []);
  if (error) return <p role="alert" data-i18n-ignore>{error}</p>;
  if (personId === undefined) return <p>加载中…</p>;
  if (!personId) return <p>请联系管理员将你的登录账号关联到员工档案，再认领企微通知。</p>;
  return <PersonWecomNotification personId={personId} self />;
}
