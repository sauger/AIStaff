import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useSearchParams } from 'react-router-dom';

import AppHeader from '@/components/AppHeader';
import CapabilityScopeLoading from '@/components/CapabilityScopeLoading';
import { DataTable, type DataTableColumn } from '@/components/DataTable';
import { Paginator } from '@/components/Paginator';
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
  GeneralSkillRead,
  MailListResponse,
  MailMessageRead,
  MailSendResult,
  MailboxStatusRead,
} from '../types';

type MailTab = 'inbox' | 'compose' | 'sent' | 'config';

const MAIL_PAGE_SIZE = 20;

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
  const [searchParams, setSearchParams] = useSearchParams();
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
  const [mailboxError, setMailboxError] = useState('');
  const [listError, setListError] = useState('');
  const [listPage, setListPage] = useState(1);
  const [sending, setSending] = useState(false);
  const [pendingOwner, setPendingOwner] = useState<MailListResponse | null>(null);
  const [inboundSkills, setInboundSkills] = useState<GeneralSkillRead[]>([]);
  const [teachSkillId, setTeachSkillId] = useState('');
  const [teaching, setTeaching] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const mailboxSeqRef = useRef(0);
  const listSeqRef = useRef(0);
  const openedQueryRef = useRef('');

  const currentAgent = useMemo(
    () => agents.find((item) => item.id === agentId) || null,
    [agents, agentId],
  );
  const canSend = Boolean(currentAgent && isEmployeeOwnedBy(currentAgent, currentUser));
  const mailboxEnabled = mailbox?.enabled !== false;

  useEffect(() => {
    void loadAgents();
  }, []);

  useEffect(() => {
    const onScopeChange = (event: Event) => {
      const next = (event as CustomEvent<{ agentId?: string }>).detail?.agentId || '';
      if (!next || isTeamScope(next)) return;
      setAgentId(next);
      setOpened(null);
      setListPage(1);
    };
    window.addEventListener('ultrarag-enterprise-agent-scope-change', onScopeChange);
    return () => window.removeEventListener('ultrarag-enterprise-agent-scope-change', onScopeChange);
  }, []);

  useEffect(() => {
    if (!agentScopeLoaded || !agentId || isTeamScope(agentId)) return;
    void loadMailbox();
    void loadInboundSkills();
  }, [agentId, agentScopeLoaded]);

  useEffect(() => {
    setListPage(1);
    setOpened(null);
  }, [agentId, tab]);

  useEffect(() => {
    if (!agentScopeLoaded || !agentId || isTeamScope(agentId)) return;
    if (tab === 'inbox') {
      void loadInbox();
      void loadPendingOwner();
    }
    if (tab === 'sent') void loadSent();
    if (tab === 'compose') void loadDrafts();
  }, [agentId, tab, agentScopeLoaded, mailbox?.configured, listPage]);

  async function loadAgents() {
    try {
      const rows = await api.get<AgentProfileRead[]>(
        `/api/enterprise/agents?tenant_id=${encodeURIComponent(TENANT_ID)}`,
      );
      const visible = visibleEmployeeAgents(rows, currentUser, { activeOnly: true });
      setAgents(visible);
      const stored = window.localStorage.getItem(ENTERPRISE_AGENT_STORAGE_KEY) || '';
      if (isTeamScope(agentId) || isTeamScope(stored)) {
        return;
      }
      const queryAgent = searchParams.get('agent_id') || '';
      const preferred = visible.find((item) => item.id === queryAgent && !item.is_overall)
        || visible.find((item) => item.id === agentId && !item.is_overall)
        || visible.find((item) => !item.is_overall);
      const nextId = preferred?.id || '';
      if (nextId && nextId !== agentId) {
        persistSharedAgentScope(nextId);
        setAgentId(nextId);
      }
    } catch (error) {
      notify.error(apiErrorMessage(error));
    } finally {
      setAgentScopeLoaded(true);
    }
  }

  async function loadMailbox() {
    if (!agentId || isTeamScope(agentId)) return;
    const seq = ++mailboxSeqRef.current;
    try {
      const result = await api.get<MailboxStatusRead>(
        `/api/enterprise/mail/mailbox?tenant_id=${encodeURIComponent(TENANT_ID)}&agent_id=${encodeURIComponent(agentId)}`,
      );
      if (seq !== mailboxSeqRef.current) return;
      setMailbox(result);
      setMailboxError('');
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
      if (seq !== mailboxSeqRef.current) return;
      notify.error(apiErrorMessage(error));
      setMailbox(null);
      setMailboxError(apiErrorMessage(error));
    }
  }

  async function loadInbox() {
    if (!agentId || isTeamScope(agentId)) return;
    const seq = ++listSeqRef.current;
    setLoading(true);
    setListError('');
    try {
      const result = await api.get<MailListResponse>(
        `/api/enterprise/mail/inbox?tenant_id=${encodeURIComponent(TENANT_ID)}&agent_id=${encodeURIComponent(agentId)}&page=${listPage}&page_size=${MAIL_PAGE_SIZE}`,
      );
      if (seq !== listSeqRef.current) return;
      setInbox(result);
      setListError(result.last_error || '');
      const target = searchParams.get('message_id');
      if (target && openedQueryRef.current !== target) {
        openedQueryRef.current = target;
        void openMessage(target);
      }
    } catch (error) {
      if (seq !== listSeqRef.current) return;
      notify.error(apiErrorMessage(error));
      setListError(apiErrorMessage(error));
    } finally {
      if (seq === listSeqRef.current) setLoading(false);
    }
  }

  async function loadSent() {
    if (!agentId || isTeamScope(agentId)) return;
    const seq = ++listSeqRef.current;
    setLoading(true);
    setListError('');
    try {
      const result = await api.get<MailListResponse>(
        `/api/enterprise/mail/sent?tenant_id=${encodeURIComponent(TENANT_ID)}&agent_id=${encodeURIComponent(agentId)}&page=${listPage}&page_size=${MAIL_PAGE_SIZE}`,
      );
      if (seq !== listSeqRef.current) return;
      setSent(result);
      setListError(result.last_error || '');
    } catch (error) {
      if (seq !== listSeqRef.current) return;
      notify.error(apiErrorMessage(error));
      setListError(apiErrorMessage(error));
    } finally {
      if (seq === listSeqRef.current) setLoading(false);
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

  async function loadPendingOwner() {
    if (!agentId || isTeamScope(agentId)) return;
    try {
      const result = await api.get<MailListResponse>(
        `/api/enterprise/mail/pending-owner?tenant_id=${encodeURIComponent(TENANT_ID)}&agent_id=${encodeURIComponent(agentId)}`,
      );
      setPendingOwner(result);
    } catch {
      setPendingOwner(null);
    }
  }

  async function loadInboundSkills() {
    if (!agentId || isTeamScope(agentId)) return;
    try {
      const rows = await api.get<GeneralSkillRead[]>(
        `/api/enterprise/general-skills?tenant_id=${encodeURIComponent(TENANT_ID)}&agent_id=${encodeURIComponent(agentId)}`,
      );
      setInboundSkills(rows.filter((row) => row.status === 'published' && row.inbound_auto_run === true));
    } catch {
      setInboundSkills([]);
    }
  }

  async function openMessage(id: string) {
    if (!agentId) return;
    try {
      const result = await api.get<MailMessageRead>(
        `/api/enterprise/mail/messages/${encodeURIComponent(id)}?tenant_id=${encodeURIComponent(TENANT_ID)}&agent_id=${encodeURIComponent(agentId)}`,
      );
      setOpened(result);
      if (tab === 'inbox') {
        void loadInbox();
        void loadPendingOwner();
      }
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
    if (!agentId || sending) return;
    setSending(true);
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
    } finally {
      setSending(false);
    }
  }

  async function sendDraft(id: string) {
    if (!agentId || sending) return;
    setSending(true);
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
    } finally {
      setSending(false);
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

  async function setMailboxEnabled(enabled: boolean) {
    if (!agentId) return;
    try {
      const result = await api.put<MailboxStatusRead>(
        `/api/enterprise/mail/mailbox/enabled?agent_id=${encodeURIComponent(agentId)}`,
        { tenant_id: TENANT_ID, enabled },
      );
      setMailbox(result);
      notify.success(enabled ? '邮箱已启用' : '邮箱已停用');
    } catch (error) {
      notify.error(apiErrorMessage(error));
    }
  }

  async function teachOpened(action: 'ignore' | 'skill' | 'ask_again') {
    if (!agentId || !opened) return;
    if (action === 'skill' && !teachSkillId) {
      notify.warning('请先选择一个已开「可被来信自动跑」的技能');
      return;
    }
    setTeaching(true);
    try {
      const result = await api.post<MailMessageRead>(
        `/api/enterprise/mail/messages/${encodeURIComponent(opened.id)}/teach?agent_id=${encodeURIComponent(agentId)}`,
        {
          tenant_id: TENANT_ID,
          action,
          skill_id: action === 'skill' ? teachSkillId : undefined,
          note: action === 'ignore'
            ? '这是垃圾，以后同类忽略'
            : action === 'ask_again'
              ? '下次仍问我'
              : `按技能处理`,
        },
      );
      setOpened(result);
      void loadInbox();
      void loadPendingOwner();
      notify.success(result.triage_label || '已记下教学');
    } catch (error) {
      notify.error(apiErrorMessage(error));
    } finally {
      setTeaching(false);
    }
  }

  if (!agentScopeLoaded) return <CapabilityScopeLoading />;

  if (isTeamScope(agentId) || !agentId) {
    return (
      <div className="min-h-full box-border px-[48px] pt-[32px] pb-[43px] max-[900px]:px-[16px]">
        <AppHeader
          onLogout={onLogout}
          userName={currentUser?.username}
          title="邮件"
          description="这个数字员工的岗位邮箱。收件箱、写信和已发送走同一套 IMAP + SMTP。"
        />
        <EmptyState text="请先选择一个数字员工，再打开岗位邮箱。" />
      </div>
    );
  }

  const configured = Boolean(mailbox?.configured);
  const mailboxReady = mailbox !== null || Boolean(mailboxError);
  const listing = tab === 'sent' ? sent : inbox;
  const emptyReason = mailboxError
    ? mailboxError
    : !configured
      ? '这个员工还没有配置邮箱。请先在「配置邮箱」填写 IMAP 和 SMTP。'
      : listError || listing?.empty_reason || (tab === 'sent' ? '还没有已发送的邮件。' : '收件箱是空的。');
  const showConfigCta = mailboxReady && !configured && !mailboxError && canSend;

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
      {configured && !mailboxEnabled ? (
        <p className="mt-[16px] rounded-[10px] bg-[#fff7e8] px-[12px] py-[8px] text-[12px] text-[#8a5a00]">
          邮箱已停用：不再拉新信、不再分流、也不能发出。历史来信和已发送仍可打开。
        </p>
      ) : null}

      <div className="mt-[20px]">
        <UnderlineTabs
          items={TABS}
          value={tab}
          onChange={(next) => {
            setTab(next);
            setOpened(null);
            setListPage(1);
          }}
          variant="line"
          aria-label="邮件分区"
        />
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
            <div className="mt-[16px] flex flex-wrap gap-[8px]">
              <UIButton onClick={() => void saveConfig()}>保存配置</UIButton>
              {configured ? (
                <UIButton
                  variant="outline"
                  className={OUTLINE_ACTION_BUTTON_CLASS}
                  onClick={() => void setMailboxEnabled(!mailboxEnabled)}
                >
                  {mailboxEnabled ? '停用邮箱' : '启用邮箱'}
                </UIButton>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}

      {tab === 'compose' ? (
        <div className="mt-[20px] max-w-[720px] rounded-[16px] border border-[#e3e7f1] bg-white p-[24px]">
          {!configured ? (
            <EmptyState
              text={emptyReason}
              onConfig={showConfigCta ? () => setTab('config') : undefined}
            />
          ) : (
            <>
              {(drafts?.messages || []).length ? (
                <div className="mb-[16px] rounded-[10px] bg-[#f6f7fa] p-[12px]">
                  <p className="m-0 text-[13px] font-medium text-[#17191f]">待确认草稿</p>
                  {drafts?.messages.map((item) => (
                    <div key={item.id} className="mt-[12px] grid gap-[4px] border-t border-[#e3e7f1] pt-[12px] first:mt-[8px] first:border-t-0 first:pt-0">
                      <p className="m-0 text-[12px] text-[#17191f]">收件人：{item.to.join(', ') || '（无）'}</p>
                      {item.cc.length ? <p className="m-0 text-[12px] text-[#697085]">抄送：{item.cc.join(', ')}</p> : null}
                      {item.bcc.length ? <p className="m-0 text-[12px] text-[#697085]">密送：{item.bcc.join(', ')}</p> : null}
                      <p className="m-0 text-[12px] text-[#17191f]">{item.subject || '（无主题）'}</p>
                      <p className="m-0 whitespace-pre-wrap text-[12px] text-[#464c5e]">{item.body_text || '（无正文）'}</p>
                      {item.attachments.length ? (
                        <p className="m-0 text-[12px] text-[#697085]">
                          附件：{item.attachments.map((file) => file.filename).join('、')}
                        </p>
                      ) : null}
                      {canSend ? (
                        <div>
                          <UIButton size="sm" disabled={sending || !mailboxEnabled} onClick={() => void sendDraft(item.id)}>发送草稿</UIButton>
                        </div>
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
                  <UIButton disabled={sending || !mailboxEnabled} onClick={() => void sendMail(false)}>发送</UIButton>
                  <UIButton variant="outline" className={OUTLINE_ACTION_BUTTON_CLASS} disabled={sending} onClick={() => void sendMail(true)}>
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
          canReply={tab === 'inbox' && canSend && mailboxEnabled}
          inboundSkills={inboundSkills}
          teachSkillId={teachSkillId}
          teaching={teaching}
          onTeachSkillId={setTeachSkillId}
          onTeach={canSend ? teachOpened : undefined}
          onBack={() => {
            setOpened(null);
            if (searchParams.get('message_id')) {
              const next = new URLSearchParams(searchParams);
              next.delete('message_id');
              setSearchParams(next, { replace: true });
            }
          }}
          onReply={() => void startReply(opened)}
        />
      ) : null}

      {(tab === 'inbox' || tab === 'sent') && !opened ? (
        <div className="mt-[20px]">
          {tab === 'inbox' && !opened && (pendingOwner?.total || 0) > 0 ? (
            <div className="mb-[16px] rounded-[12px] border border-[#f0d9a6] bg-[#fff9ee] p-[16px]">
              <p className="m-0 text-[13px] font-medium text-[#17191f]">
                {`待主人处理（${pendingOwner?.total}）`}
              </p>
              <p className="mt-[4px] mb-[8px] text-[12px] text-[#697085]">
                这些来信对不上已开开关的技能，也不像高置信垃圾。可在这封信上选去向。
              </p>
              <ul className="m-0 list-none p-0">
                {(pendingOwner?.messages || []).map((row) => (
                  <li key={row.id} className="border-t border-[#f3e6c8] py-[8px] first:border-t-0 first:pt-0">
                    <button
                      type="button"
                      className="w-full text-left text-[13px] text-[#17191f]"
                      onClick={() => void openMessage(row.id)}
                    >
                      {row.subject || '（无主题）'} · {row.from_address}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {listError && (listing?.messages || []).length > 0 ? (
            <p className="mb-[12px] rounded-[10px] bg-[#fff4f4] px-[12px] py-[8px] text-[12px] text-[#d20b0b]">
              {listError}
            </p>
          ) : null}
          {(!mailboxReady || (loading && !(listing?.messages || []).length)) ? (
            <DataTable
              columns={messageColumns(tab)}
              data={listing?.messages || []}
              rowKey={(row) => row.id}
              loading
              emptyText={emptyReason}
            />
          ) : !configured || (listing && listing.messages.length === 0) ? (
            <EmptyState
              text={emptyReason}
              onConfig={showConfigCta ? () => setTab('config') : undefined}
            />
          ) : (
            <>
              <DataTable
                columns={messageColumns(tab)}
                data={listing?.messages || []}
                rowKey={(row) => row.id}
                loading={loading}
                emptyText={emptyReason}
                onRowClick={(row) => void openMessage(row.id)}
              />
              {(listing?.total || 0) > MAIL_PAGE_SIZE ? (
                <Paginator
                  aria-label={tab === 'sent' ? '已发送分页' : '收件箱分页'}
                  page={listing?.page || listPage}
                  pageCount={Math.max(1, Math.ceil((listing?.total || 0) / MAIL_PAGE_SIZE))}
                  onChange={setListPage}
                />
              ) : null}
            </>
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

function messageStatusLabel(message: MailMessageRead): string {
  if (message.folder === 'inbox' && message.triage_label) {
    return message.triage_label;
  }
  if (message.folder === 'sent' || message.status === 'sent' || message.status === 'failed') {
    if (message.status === 'failed') return message.smtp_error || '失败';
    return '成功';
  }
  return message.unread ? '未读' : '已读';
}

function MessageDetail({
  message,
  canReply,
  inboundSkills,
  teachSkillId,
  teaching,
  onTeachSkillId,
  onTeach,
  onBack,
  onReply,
}: {
  message: MailMessageRead;
  canReply: boolean;
  inboundSkills: GeneralSkillRead[];
  teachSkillId: string;
  teaching: boolean;
  onTeachSkillId: (id: string) => void;
  onTeach?: (action: 'ignore' | 'skill' | 'ask_again') => void;
  onBack: () => void;
  onReply: () => void;
}) {
  const when = formatDateTime(message.sent_at || message.received_at || message.created_at);
  return (
    <div className="mt-[20px] rounded-[16px] border border-[#e3e7f1] bg-white p-[24px]">
      <div className="flex items-center justify-between gap-[12px]">
        <UIButton variant="outline" className={OUTLINE_ACTION_BUTTON_CLASS} onClick={onBack}>返回列表</UIButton>
        {canReply ? <UIButton onClick={onReply}>回复</UIButton> : null}
      </div>
      <h2 className="mt-[16px] text-[18px] font-medium text-[#17191f]">{message.subject || '（无主题）'}</h2>
      <div className="mt-[8px] grid gap-[4px] text-[12px] text-[#697085]">
        <p className="m-0">收件人：{message.to.join(', ') || '（无）'}</p>
        {message.from_address ? <p className="m-0">{`发件人：${message.from_address}`}</p> : null}
        {message.cc.length ? <p className="m-0">抄送：{message.cc.join(', ')}</p> : null}
        <p className="m-0">{`时间：${when}`}</p>
        <p className="m-0">状态：{messageStatusLabel(message)}</p>
        {message.triage_reason ? <p className="m-0">{`去向理由：${message.triage_reason}`}</p> : null}
        {message.teaching_notice ? <p className="m-0">{message.teaching_notice}</p> : null}
      </div>
      {onTeach && message.folder === 'inbox' && message.can_teach !== false ? (
        <div className="mt-[16px] rounded-[12px] border border-[#eceef1] bg-[#fafbfc] p-[12px]">
          <p className="m-0 text-[13px] font-medium text-[#17191f]">教员工以后怎么处理</p>
          <p className="mt-[4px] mb-[8px] text-[12px] text-[#697085]">
            与渠道回复同一套规矩：这是垃圾、按某技能处理、或下次仍问我。
          </p>
          <div className="flex flex-wrap items-center gap-[8px]">
            <UIButton variant="outline" className={OUTLINE_ACTION_BUTTON_CLASS} disabled={teaching} onClick={() => onTeach('ignore')}>
              这是垃圾
            </UIButton>
            <UIButton variant="outline" className={OUTLINE_ACTION_BUTTON_CLASS} disabled={teaching} onClick={() => onTeach('ask_again')}>
              下次仍问我
            </UIButton>
            <select
              aria-label="选择来信技能"
              className="h-[36px] rounded-[8px] border border-[#dfe3ee] bg-white px-[8px] text-[13px]"
              value={teachSkillId}
              onChange={(event) => onTeachSkillId(event.target.value)}
            >
              <option value="">选择已开开关的技能</option>
              {inboundSkills.map((skill) => (
                <option key={skill.id} value={skill.id}>{skill.name}</option>
              ))}
            </select>
            <UIButton disabled={teaching} onClick={() => onTeach('skill')}>按该技能处理</UIButton>
          </div>
        </div>
      ) : null}
      {message.imap_append_note ? (
        <p className="mt-[8px] text-[12px] text-[#697085]">{message.imap_append_note}</p>
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

function messageColumns(tab: MailTab): DataTableColumn<MailMessageRead>[] {
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
        <span className={cn(row.unread && 'font-medium')}>
          {row.subject || '（无主题）'}
          {row.unread ? ' · 未读' : ''}
        </span>
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
      render: (row) => messageStatusLabel(row),
    },
  ];
}
