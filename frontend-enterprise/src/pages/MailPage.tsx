import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';

import AppHeader from '@/components/AppHeader';
import CapabilityScopeLoading from '@/components/CapabilityScopeLoading';
import { DataTable, type DataTableColumn } from '@/components/DataTable';
import {
  Input,
  Textarea,
  UnderlineTabs,
  type UnderlineTabItem,
} from '@/components/ui';
import { Button as UIButton } from '@/components/ui/button';
import { notify } from '@/components/ui/app-toast';
import { cn } from '@/lib/utils';
import {
  formatDateTime,
  OUTLINE_ACTION_BUTTON_CLASS,
} from '@/lib/enterprise-ui';
import {
  ENTERPRISE_AGENT_STORAGE_KEY,
  isTeamScope,
  persistSharedAgentScope,
  readEmployeeScope,
} from '@/lib/agent-scope-storage';
import { api, ApiError, TENANT_ID } from '../api/client';
import { isEmployeeOwnedBy, type EnterpriseAuthUser } from '../auth';
import { visibleEmployeeAgents } from '../employee';
import type {
  AgentProfileRead,
  MailListResponse,
  MailMessageRead,
  MailSendResult,
  MailboxStatusRead,
} from '../types';

type MailTab = 'inbox' | 'compose' | 'sent' | 'config';

const TABS: UnderlineTabItem<MailTab>[] = [
  { value: 'inbox', label: '收件箱' },
  { value: 'compose', label: '写邮件' },
  { value: 'sent', label: '已发送' },
  { value: 'config', label: '配置邮箱' },
];

type MailPageProps = {
  currentUser?: EnterpriseAuthUser;
  onLogout?: () => void;
};

function apiErrorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return '操作失败';
}

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error('读取文件失败'));
    reader.onload = () => {
      const result = String(reader.result || '');
      resolve(result.includes(',') ? result.split(',').pop() || '' : result);
    };
    reader.readAsDataURL(file);
  });
}

export default function MailPage({ currentUser, onLogout }: MailPageProps = {}) {
  const [agentId, setAgentId] = useState(readEmployeeScope);
  const [agents, setAgents] = useState<AgentProfileRead[]>([]);
  const [agentScopeLoaded, setAgentScopeLoaded] = useState(false);
  const [tab, setTab] = useState<MailTab>('inbox');
  const [mailbox, setMailbox] = useState<MailboxStatusRead | null>(null);
  const [inbox, setInbox] = useState<MailListResponse | null>(null);
  const [sent, setSent] = useState<MailListResponse | null>(null);
  const [drafts, setDrafts] = useState<MailListResponse | null>(null);
  const [opened, setOpened] = useState<MailMessageRead | null>(null);
  const [loading, setLoading] = useState(false);
  const [to, setTo] = useState('');
  const [cc, setCc] = useState('');
  const [subject, setSubject] = useState('');
  const [body, setBody] = useState('');
  const [replyToId, setReplyToId] = useState('');
  const [files, setFiles] = useState<File[]>([]);
  const [emailAddress, setEmailAddress] = useState('');
  const [imapHost, setImapHost] = useState('');
  const [imapPort, setImapPort] = useState('993');
  const [imapEncryption, setImapEncryption] = useState('ssl');
  const [smtpHost, setSmtpHost] = useState('');
  const [smtpPort, setSmtpPort] = useState('587');
  const [smtpEncryption, setSmtpEncryption] = useState('starttls');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const currentAgent = useMemo(
    () => agents.find((item) => item.id === agentId) || null,
    [agents, agentId],
  );
  const canSend = Boolean(currentAgent && isEmployeeOwnedBy(currentAgent, currentUser));

  useEffect(() => {
    void loadAgents();
  }, []);

  useEffect(() => {
    const onScopeChange = (event: Event) => {
      const next = (event as CustomEvent<{ agentId?: string }>).detail?.agentId || '';
      if (!next || isTeamScope(next)) return;
      setAgentId(next);
      setOpened(null);
    };
    window.addEventListener('ultrarag-enterprise-agent-scope-change', onScopeChange);
    return () => window.removeEventListener('ultrarag-enterprise-agent-scope-change', onScopeChange);
  }, []);

  useEffect(() => {
    if (!agentScopeLoaded || !agentId || isTeamScope(agentId)) return;
    void loadMailbox();
  }, [agentId, agentScopeLoaded]);

  useEffect(() => {
    if (!agentScopeLoaded || !agentId || isTeamScope(agentId)) return;
    if (tab === 'inbox') void loadInbox();
    if (tab === 'sent') void loadSent();
    if (tab === 'compose') void loadDrafts();
  }, [agentId, tab, agentScopeLoaded, mailbox?.configured]);

  async function loadAgents() {
    try {
      const rows = await api.get<AgentProfileRead[]>(
        `/api/enterprise/agents?tenant_id=${encodeURIComponent(TENANT_ID)}`,
      );
      const visible = visibleEmployeeAgents(rows, currentUser, { activeOnly: true });
      setAgents(visible);
      const preferred = visible.find((item) => item.id === agentId && !item.is_overall)
        || visible.find((item) => !item.is_overall);
      const nextId = preferred?.id || '';
      if (nextId && nextId !== agentId) {
        const stored = window.localStorage.getItem(ENTERPRISE_AGENT_STORAGE_KEY) || '';
        if (!isTeamScope(stored)) persistSharedAgentScope(nextId);
        setAgentId(nextId);
      }
    } catch (error) {
      notify.error(apiErrorMessage(error));
    } finally {
      setAgentScopeLoaded(true);
    }
  }

  async function loadMailbox() {
    if (!agentId) return;
    try {
      const result = await api.get<MailboxStatusRead>(
        `/api/enterprise/mail/mailbox?tenant_id=${encodeURIComponent(TENANT_ID)}&agent_id=${encodeURIComponent(agentId)}`,
      );
      setMailbox(result);
      setEmailAddress(result.email_address || '');
      setImapHost(result.imap_host || '');
      setImapPort(String(result.imap_port || 993));
      setImapEncryption(result.imap_encryption || 'ssl');
      setSmtpHost(result.smtp_host || '');
      setSmtpPort(String(result.smtp_port || 587));
      setSmtpEncryption(result.smtp_encryption || 'starttls');
      setUsername(result.username || '');
      setPassword('');
    } catch (error) {
      notify.error(apiErrorMessage(error));
      setMailbox(null);
    }
  }

  async function loadInbox() {
    if (!agentId) return;
    setLoading(true);
    try {
      const result = await api.get<MailListResponse>(
        `/api/enterprise/mail/inbox?tenant_id=${encodeURIComponent(TENANT_ID)}&agent_id=${encodeURIComponent(agentId)}`,
      );
      setInbox(result);
    } catch (error) {
      notify.error(apiErrorMessage(error));
      setInbox(null);
    } finally {
      setLoading(false);
    }
  }

  async function loadSent() {
    if (!agentId) return;
    setLoading(true);
    try {
      const result = await api.get<MailListResponse>(
        `/api/enterprise/mail/sent?tenant_id=${encodeURIComponent(TENANT_ID)}&agent_id=${encodeURIComponent(agentId)}`,
      );
      setSent(result);
    } catch (error) {
      notify.error(apiErrorMessage(error));
      setSent(null);
    } finally {
      setLoading(false);
    }
  }

  async function loadDrafts() {
    if (!agentId) return;
    try {
      const result = await api.get<MailListResponse>(
        `/api/enterprise/mail/drafts?tenant_id=${encodeURIComponent(TENANT_ID)}&agent_id=${encodeURIComponent(agentId)}`,
      );
      setDrafts(result);
    } catch {
      setDrafts(null);
    }
  }

  async function openMessage(id: string) {
    if (!agentId) return;
    try {
      const result = await api.get<MailMessageRead>(
        `/api/enterprise/mail/messages/${encodeURIComponent(id)}?tenant_id=${encodeURIComponent(TENANT_ID)}&agent_id=${encodeURIComponent(agentId)}`,
      );
      setOpened(result);
      if (tab === 'inbox') void loadInbox();
    } catch (error) {
      notify.error(apiErrorMessage(error));
    }
  }

  async function startReply(message: MailMessageRead) {
    if (!agentId) return;
    try {
      const defaults = await api.get<{ to: string[]; subject: string; body: string; in_reply_to: string }>(
        `/api/enterprise/mail/messages/${encodeURIComponent(message.id)}/reply?tenant_id=${encodeURIComponent(TENANT_ID)}&agent_id=${encodeURIComponent(agentId)}`,
      );
      setTo((defaults.to || []).join(', '));
      setCc('');
      setSubject(defaults.subject || '');
      setBody(defaults.body || '');
      setReplyToId(defaults.in_reply_to || message.id);
      setOpened(null);
      setTab('compose');
    } catch (error) {
      notify.error(apiErrorMessage(error));
    }
  }

  async function sendMail(asDraft = false) {
    if (!agentId) return;
    const attachments = await Promise.all(
      files.map(async (file) => ({
        filename: file.name,
        content_base64: await fileToBase64(file),
        content_type: file.type || 'application/octet-stream',
      })),
    );
    try {
      const result = await api.post<MailSendResult>(
        `/api/enterprise/mail/messages?agent_id=${encodeURIComponent(agentId)}`,
        {
          tenant_id: TENANT_ID,
          to: splitAddresses(to),
          cc: splitAddresses(cc),
          subject,
          body,
          attachments,
          as_draft: asDraft,
          in_reply_to: replyToId || null,
        },
      );
      notify.success(result.notice);
      setFiles([]);
      if (result.delivered) {
        setTo('');
        setCc('');
        setSubject('');
        setBody('');
        setReplyToId('');
        setTab('sent');
        void loadSent();
      } else {
        void loadDrafts();
      }
    } catch (error) {
      notify.error(apiErrorMessage(error));
    }
  }

  async function sendDraft(id: string) {
    if (!agentId) return;
    try {
      const result = await api.post<MailSendResult>(
        `/api/enterprise/mail/drafts/${encodeURIComponent(id)}/send?tenant_id=${encodeURIComponent(TENANT_ID)}&agent_id=${encodeURIComponent(agentId)}`,
        {},
      );
      notify.success(result.notice);
      void loadDrafts();
      setTab('sent');
      void loadSent();
    } catch (error) {
      notify.error(apiErrorMessage(error));
    }
  }

  async function saveConfig() {
    if (!agentId) return;
    try {
      const result = await api.put<MailboxStatusRead>(
        `/api/enterprise/mail/mailbox?agent_id=${encodeURIComponent(agentId)}`,
        {
          tenant_id: TENANT_ID,
          email_address: emailAddress,
          imap_host: imapHost,
          imap_port: Number(imapPort) || 993,
          imap_encryption: imapEncryption,
          smtp_host: smtpHost,
          smtp_port: Number(smtpPort) || 587,
          smtp_encryption: smtpEncryption,
          username,
          password: password || null,
          probe: true,
        },
      );
      setMailbox(result);
      setPassword('');
      if (result.last_error) notify.error(result.last_error);
      else notify.success('邮箱已配置');
    } catch (error) {
      notify.error(apiErrorMessage(error));
    }
  }

  if (!agentScopeLoaded) return <CapabilityScopeLoading />;

  const configured = Boolean(mailbox?.configured);
  const listing = tab === 'sent' ? sent : inbox;
  const emptyReason = !configured
    ? '这个员工还没有配置邮箱。请先在「配置邮箱」填写 IMAP 和 SMTP。'
    : listing?.empty_reason || (tab === 'sent' ? '还没有已发送的邮件。' : '收件箱是空的。');

  return (
    <div className="min-h-full box-border px-[48px] pt-[32px] pb-[43px] max-[900px]:px-[16px]">
      <AppHeader
        onLogout={onLogout}
        userName={currentUser?.username}
        title="邮件"
        description="这个数字员工的岗位邮箱。收件箱、写信和已发送走同一套 IMAP + SMTP。"
      />

      {!canSend ? (
        <p className="mt-[16px] rounded-[10px] bg-[#f6f7fa] px-[12px] py-[8px] text-[12px] text-[#697085]">
          管理员正在查阅其他员工的邮件，只能浏览，不能改凭证或代发。
        </p>
      ) : null}

      <div className="mt-[20px]">
        <UnderlineTabs items={TABS} value={tab} onChange={setTab} variant="line" aria-label="邮件分区" />
      </div>

      {tab === 'config' ? (
        <div className="mt-[20px] max-w-[640px] rounded-[16px] border border-[#e3e7f1] bg-white p-[24px]">
          <p className="m-0 text-[13px] text-[#697085]">
            在这里单独登记 IMAP / SMTP 账号。不要写进技能、SOP 或网页登录密钥。
          </p>
          <div className="mt-[16px] grid gap-[12px]">
            <Field label="发件地址">
              <Input value={emailAddress} onChange={(event) => setEmailAddress(event.target.value)} disabled={!canSend} />
            </Field>
            <div className="grid grid-cols-2 gap-[12px] max-[640px]:grid-cols-1">
              <Field label="IMAP 主机">
                <Input value={imapHost} onChange={(event) => setImapHost(event.target.value)} disabled={!canSend} />
              </Field>
              <Field label="IMAP 端口">
                <Input value={imapPort} onChange={(event) => setImapPort(event.target.value)} disabled={!canSend} />
              </Field>
              <Field label="IMAP 加密">
                <Input value={imapEncryption} onChange={(event) => setImapEncryption(event.target.value)} disabled={!canSend} />
              </Field>
              <Field label="SMTP 主机">
                <Input value={smtpHost} onChange={(event) => setSmtpHost(event.target.value)} disabled={!canSend} />
              </Field>
              <Field label="SMTP 端口">
                <Input value={smtpPort} onChange={(event) => setSmtpPort(event.target.value)} disabled={!canSend} />
              </Field>
              <Field label="SMTP 加密">
                <Input value={smtpEncryption} onChange={(event) => setSmtpEncryption(event.target.value)} disabled={!canSend} />
              </Field>
            </div>
            <Field label="用户名">
              <Input value={username} onChange={(event) => setUsername(event.target.value)} disabled={!canSend} />
            </Field>
            <Field label={mailbox?.password_configured ? '密码（已配置，留空则不改）' : '密码'}>
              <Input
                type="password"
                value={password}
                autoComplete="new-password"
                placeholder={mailbox?.password_configured ? '已配置' : ''}
                onChange={(event) => setPassword(event.target.value)}
                disabled={!canSend}
              />
            </Field>
          </div>
          {canSend ? (
            <UIButton className="mt-[16px]" onClick={() => void saveConfig()}>保存配置</UIButton>
          ) : null}
        </div>
      ) : null}

      {tab === 'compose' ? (
        <div className="mt-[20px] max-w-[720px] rounded-[16px] border border-[#e3e7f1] bg-white p-[24px]">
          {!configured ? (
            <EmptyState text={emptyReason} onConfig={() => setTab('config')} />
          ) : (
            <>
              {(drafts?.messages || []).length ? (
                <div className="mb-[16px] rounded-[10px] bg-[#f6f7fa] p-[12px]">
                  <p className="m-0 text-[13px] font-medium text-[#17191f]">待确认草稿</p>
                  {drafts?.messages.map((item) => (
                    <div key={item.id} className="mt-[8px] flex items-center justify-between gap-[8px] text-[12px]">
                      <span>{item.to.join(', ')} · {item.subject || '（无主题）'}</span>
                      {canSend ? (
                        <UIButton size="sm" onClick={() => void sendDraft(item.id)}>发送草稿</UIButton>
                      ) : null}
                    </div>
                  ))}
                </div>
              ) : null}
              <div className="grid gap-[12px]">
                <Field label="收件人">
                  <Input value={to} onChange={(event) => setTo(event.target.value)} placeholder="name@example.com" disabled={!canSend} />
                </Field>
                <Field label="抄送">
                  <Input value={cc} onChange={(event) => setCc(event.target.value)} disabled={!canSend} />
                </Field>
                <Field label="主题">
                  <Input value={subject} onChange={(event) => setSubject(event.target.value)} disabled={!canSend} />
                </Field>
                <Field label="正文">
                  <Textarea value={body} onChange={(event) => setBody(event.target.value)} rows={10} disabled={!canSend} />
                </Field>
                <div>
                  <UIButton
                    variant="outline"
                    className={OUTLINE_ACTION_BUTTON_CLASS}
                    disabled={!canSend}
                    onClick={() => fileInputRef.current?.click()}
                  >
                    添加附件
                  </UIButton>
                  <input
                    ref={fileInputRef}
                    type="file"
                    multiple
                    className="hidden"
                    onChange={(event) => {
                      setFiles(Array.from(event.target.files || []));
                      event.target.value = '';
                    }}
                  />
                  {files.length ? (
                    <p className="mt-[8px] m-0 text-[12px] text-[#697085]">
                      {files.map((file) => file.name).join('、')}
                    </p>
                  ) : null}
                </div>
              </div>
              {canSend ? (
                <div className="mt-[16px] flex gap-[8px]">
                  <UIButton onClick={() => void sendMail(false)}>发送</UIButton>
                  <UIButton variant="outline" className={OUTLINE_ACTION_BUTTON_CLASS} onClick={() => void sendMail(true)}>
                    保存草稿
                  </UIButton>
                </div>
              ) : null}
            </>
          )}
        </div>
      ) : null}

      {(tab === 'inbox' || tab === 'sent') && opened ? (
        <MessageDetail
          message={opened}
          canReply={tab === 'inbox' && canSend}
          onBack={() => setOpened(null)}
          onReply={() => void startReply(opened)}
        />
      ) : null}

      {(tab === 'inbox' || tab === 'sent') && !opened ? (
        <div className="mt-[20px]">
          {!configured || (listing && listing.messages.length === 0 && !loading) ? (
            <EmptyState
              text={emptyReason}
              onConfig={!configured ? () => setTab('config') : undefined}
            />
          ) : (
            <DataTable
              columns={messageColumns(tab, (row) => void openMessage(row.id))}
              data={listing?.messages || []}
              rowKey={(row) => row.id}
              loading={loading}
              emptyText={emptyReason}
            />
          )}
        </div>
      ) : null}
    </div>
  );
}

function splitAddresses(value: string): string[] {
  return value.split(/[,;\s]+/).map((item) => item.trim()).filter(Boolean);
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="grid gap-[6px] text-[12px] text-[#697085]">
      <span>{label}</span>
      {children}
    </label>
  );
}

function EmptyState({ text, onConfig }: { text: string; onConfig?: () => void }) {
  return (
    <div className="flex min-h-[240px] flex-col items-center justify-center rounded-[16px] border border-dashed border-[#e3e7f1] bg-white px-[24px] text-center">
      <p className="m-0 text-[14px] font-medium text-[#17191f]">{text}</p>
      {onConfig ? (
        <UIButton className="mt-[12px]" onClick={onConfig}>去配置邮箱</UIButton>
      ) : null}
    </div>
  );
}

function MessageDetail({
  message,
  canReply,
  onBack,
  onReply,
}: {
  message: MailMessageRead;
  canReply: boolean;
  onBack: () => void;
  onReply: () => void;
}) {
  return (
    <div className="mt-[20px] rounded-[16px] border border-[#e3e7f1] bg-white p-[24px]">
      <div className="flex items-center justify-between gap-[12px]">
        <UIButton variant="outline" className={OUTLINE_ACTION_BUTTON_CLASS} onClick={onBack}>返回列表</UIButton>
        {canReply ? <UIButton onClick={onReply}>回复</UIButton> : null}
      </div>
      <h2 className="mt-[16px] text-[18px] font-medium text-[#17191f]">{message.subject || '（无主题）'}</h2>
      <p className="mt-[8px] m-0 text-[12px] text-[#697085]">
        {message.from_address} → {message.to.join(', ')}
      </p>
      {message.status === 'failed' && message.smtp_error ? (
        <p className="mt-[8px] text-[12px] text-[#d20b0b]">{message.smtp_error}</p>
      ) : null}
      <pre className="mt-[16px] whitespace-pre-wrap font-sans text-[14px] text-[#17191f]">{message.body_text}</pre>
      {message.attachments.length ? (
        <ul className="mt-[16px] m-0 list-disc pl-[18px] text-[12px] text-[#464c5e]">
          {message.attachments.map((item) => (
            <li key={`${item.filename}-${item.cabinet_path || item.error || ''}`}>
              {item.filename}
              {item.saved && item.cabinet_path ? ` · 已入柜 ${item.cabinet_path}` : ''}
              {!item.saved && item.error ? ` · ${item.error}` : ''}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function messageColumns(
  tab: MailTab,
  onOpen: (row: MailMessageRead) => void,
): DataTableColumn<MailMessageRead>[] {
  return [
    {
      key: 'from',
      title: tab === 'sent' ? '收件人' : '发件人',
      render: (row) => (tab === 'sent' ? row.to.join(', ') : row.from_address),
    },
    {
      key: 'subject',
      title: '主题',
      render: (row) => (
        <button type="button" className={cn('text-left', row.unread && 'font-medium')} onClick={() => onOpen(row)}>
          {row.subject || '（无主题）'}
          {row.unread ? ' · 未读' : ''}
        </button>
      ),
    },
    {
      key: 'time',
      title: tab === 'sent' ? '发送时间' : '时间',
      render: (row) => formatDateTime(row.sent_at || row.received_at || row.created_at),
    },
    {
      key: 'status',
      title: '状态',
      render: (row) => (row.status === 'failed' ? (row.smtp_error || '失败') : row.status === 'sent' ? '成功' : row.unread ? '未读' : '已读'),
    },
  ];
}
