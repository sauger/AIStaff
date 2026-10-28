import { useEffect, useMemo, useRef, useState } from 'react';

import AppHeader from '@/components/AppHeader';
import CapabilityScopeLoading from '@/components/CapabilityScopeLoading';
import { ConfirmDialog } from '@/components/ConfirmDialog';
import {
  Dialog,
  DialogContent,
  DialogTitle,
  Input,
  Textarea,
} from '@/components/ui';
import { Button as UIButton } from '@/components/ui/button';
import { notify } from '@/components/ui/app-toast';
import { cn } from '@/lib/utils';
import {
  DIALOG_CANCEL_BUTTON_CLASS,
  DIALOG_FOOTER_CLASS,
  DIALOG_PRIMARY_BUTTON_CLASS,
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
import type { AgentProfileRead, ContactListResponse, ContactRead } from '../types';

type ContactsPageProps = {
  currentUser?: EnterpriseAuthUser;
  onLogout?: () => void;
};

type ContactDraft = {
  id?: string;
  name: string;
  phone: string;
  email: string;
  title: string;
  city: string;
  notes: string;
  wecom_userid: string;
  feishu_openid: string;
  dingtalk_userid: string;
};

const EMPTY_DRAFT: ContactDraft = {
  name: '',
  phone: '',
  email: '',
  title: '',
  city: '',
  notes: '',
  wecom_userid: '',
  feishu_openid: '',
  dingtalk_userid: '',
};

const TEXT_FIELDS: { key: keyof ContactDraft; label: string; placeholder: string }[] = [
  { key: 'phone', label: '手机号', placeholder: '选填' },
  { key: 'email', label: '邮箱', placeholder: '选填' },
  { key: 'title', label: '职位', placeholder: '选填' },
  { key: 'city', label: '所在城市', placeholder: '选填' },
  { key: 'wecom_userid', label: '企微 userid', placeholder: '选填，可之后由消息补上' },
  { key: 'feishu_openid', label: '飞书 openid', placeholder: '选填，可之后由消息补上' },
  { key: 'dingtalk_userid', label: '钉钉 userid', placeholder: '选填，可之后由消息补上' },
];

function contactSummary(contact: ContactRead): string {
  return [contact.title, contact.city, contact.phone, contact.email].filter(Boolean).join(' · ');
}

function apiErrorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return '操作失败';
}

function draftFromContact(contact: ContactRead): ContactDraft {
  return {
    id: contact.id,
    name: contact.name,
    phone: contact.phone || '',
    email: contact.email || '',
    title: contact.title || '',
    city: contact.city || '',
    notes: contact.notes || '',
    wecom_userid: contact.wecom_userid || '',
    feishu_openid: contact.feishu_openid || '',
    dingtalk_userid: contact.dingtalk_userid || '',
  };
}

function payloadFromDraft(draft: ContactDraft) {
  return {
    tenant_id: TENANT_ID,
    name: draft.name.trim(),
    phone: draft.phone.trim(),
    email: draft.email.trim(),
    title: draft.title.trim(),
    city: draft.city.trim(),
    notes: draft.notes.trim(),
    wecom_userid: draft.wecom_userid.trim(),
    feishu_openid: draft.feishu_openid.trim(),
    dingtalk_userid: draft.dingtalk_userid.trim(),
  };
}

export default function ContactsPage({ currentUser, onLogout }: ContactsPageProps = {}) {
  const [agentId, setAgentId] = useState(readEmployeeScope);
  const [agents, setAgents] = useState<AgentProfileRead[]>([]);
  const [agentScopeLoaded, setAgentScopeLoaded] = useState(false);
  const [listing, setListing] = useState<ContactListResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [draft, setDraft] = useState<ContactDraft | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ContactRead | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const savingRef = useRef(false);
  const loadSeqRef = useRef(0);

  const currentAgent = useMemo(
    () => agents.find((item) => item.id === agentId) || null,
    [agents, agentId],
  );
  const canWrite = Boolean(listing?.can_write && currentAgent && isEmployeeOwnedBy(currentAgent, currentUser));

  useEffect(() => {
    void loadAgents();
  }, []);

  useEffect(() => {
    const onScopeChange = (event: Event) => {
      const next = (event as CustomEvent<{ agentId?: string }>).detail?.agentId || '';
      if (!next || isTeamScope(next)) return;
      setAgentId(next);
    };
    window.addEventListener('ultrarag-enterprise-agent-scope-change', onScopeChange);
    return () => window.removeEventListener('ultrarag-enterprise-agent-scope-change', onScopeChange);
  }, []);

  useEffect(() => {
    if (!agentScopeLoaded || !agentId || isTeamScope(agentId)) return;
    void loadContacts();
  }, [agentId, agentScopeLoaded]);

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
        if (!isTeamScope(stored)) {
          persistSharedAgentScope(nextId);
        }
        setAgentId(nextId);
      }
    } catch (error) {
      notify.error(apiErrorMessage(error));
    } finally {
      setAgentScopeLoaded(true);
    }
  }

  async function loadContacts() {
    if (!agentId || isTeamScope(agentId)) return;
    const seq = ++loadSeqRef.current;
    setLoading(true);
    setLoadError(null);
    try {
      const result = await api.get<ContactListResponse>(
        `/api/enterprise/contacts?tenant_id=${encodeURIComponent(TENANT_ID)}&agent_id=${encodeURIComponent(agentId)}`,
      );
      if (seq !== loadSeqRef.current) return;
      setListing(result);
    } catch (error) {
      if (seq !== loadSeqRef.current) return;
      const message = apiErrorMessage(error);
      setLoadError(message);
      setListing(null);
      notify.error(message);
    } finally {
      if (seq === loadSeqRef.current) setLoading(false);
    }
  }

  async function saveDraft() {
    if (savingRef.current || !draft || !agentId) return;
    if (!draft.name.trim()) {
      notify.error('请填写姓名');
      return;
    }
    const payload = payloadFromDraft(draft);
    savingRef.current = true;
    setSaving(true);
    try {
      if (draft.id) {
        await api.patch(
          `/api/enterprise/contacts/${encodeURIComponent(draft.id)}?agent_id=${encodeURIComponent(agentId)}`,
          payload,
        );
        notify.success('已更新联系人');
      } else {
        await api.post(
          `/api/enterprise/contacts?agent_id=${encodeURIComponent(agentId)}`,
          payload,
        );
        notify.success('已保存联系人');
      }
      setDraft(null);
      await loadContacts();
    } catch (error) {
      notify.error(apiErrorMessage(error));
    } finally {
      savingRef.current = false;
      setSaving(false);
    }
  }

  async function confirmDelete() {
    if (savingRef.current || !deleteTarget || !agentId) return;
    savingRef.current = true;
    setSaving(true);
    try {
      await api.delete(
        `/api/enterprise/contacts/${encodeURIComponent(deleteTarget.id)}?tenant_id=${encodeURIComponent(TENANT_ID)}&agent_id=${encodeURIComponent(agentId)}`,
      );
      notify.success('已删除联系人');
      setDeleteTarget(null);
      await loadContacts();
    } catch (error) {
      notify.error(apiErrorMessage(error));
    } finally {
      savingRef.current = false;
      setSaving(false);
    }
  }

  if (!agentScopeLoaded) return <CapabilityScopeLoading />;

  if (isTeamScope(agentId) || !agentId) {
    return (
      <div className="min-h-full box-border px-[48px] pt-[32px] pb-[43px] max-[900px]:px-[16px]">
        <AppHeader
          onLogout={onLogout}
          userName={currentUser?.username}
          title="通讯录"
          description="维护真人的姓名和联系方式。渠道账号可以手填，没有账号也可以先建联系人。"
        />
        <EmptyState text="请先选择一个数字员工，再管理通讯录。" />
      </div>
    );
  }

  const contacts = listing?.contacts || [];
  const empty = !loading && !loadError && contacts.length === 0;

  return (
    <div className="min-h-full box-border px-[48px] pt-[32px] pb-[43px] max-[900px]:px-[16px]">
      <AppHeader
        onLogout={onLogout}
        userName={currentUser?.username}
        title="通讯录"
        description="维护真人的姓名和联系方式。渠道账号可以手填，没有账号也可以先建联系人。"
      />

      {listing && !canWrite ? (
        <p className="mt-[16px] rounded-[10px] bg-[#f6f7fa] px-[12px] py-[8px] text-[12px] text-[#697085]">
          管理员正在查阅其他员工的通讯录，只能查看，不能新增、编辑或删除。
        </p>
      ) : null}
      {canWrite ? (
        <p className="mt-[16px] rounded-[10px] bg-[#f6f7fa] px-[12px] py-[8px] text-[12px] text-[#697085]">
          联系人不必已有对话账号。企微、飞书、钉钉的外部 id 可留空，之后由入站消息补上尚未填写的值。
        </p>
      ) : null}

      <div className="mt-[20px] mb-[16px] flex justify-end">
        {canWrite ? (
          <UIButton
            className={cn(DIALOG_PRIMARY_BUTTON_CLASS, 'h-[34px]')}
            onClick={() => setDraft({ ...EMPTY_DRAFT })}
          >
            新增联系人
          </UIButton>
        ) : null}
      </div>

      {loading && !listing ? (
        <p className="mb-[16px] text-[13px] text-[#697085]">正在加载通讯录</p>
      ) : null}
      {loadError ? (
        <p className="mb-[16px] text-[13px] text-[#b45309]" role="alert">{loadError}</p>
      ) : null}
      {empty ? (
        <EmptyState text="还没有联系人，先新增一条吧。" />
      ) : (
        <div className="flex flex-col gap-[12px]">
          {contacts.map((contact) => (
            <div
              key={contact.id}
              className="flex items-start justify-between gap-[16px] rounded-[16px] border border-[#eceef1] bg-white p-[16px]"
            >
              <div className="min-w-0">
                <p className="m-0 text-[14px] font-medium text-[#17191f]" data-i18n-ignore>{contact.name}</p>
                <p className="mt-[4px] mb-0 text-[12px] text-[#858b9c]">
                  {contactSummary(contact)
                    ? <span data-i18n-ignore>{contactSummary(contact)}</span>
                    : '未填写联系方式'}
                </p>
                {contact.notes ? (
                  <p className="mt-[6px] mb-0 text-[12px] text-[#464c5e]" data-i18n-ignore>{contact.notes}</p>
                ) : null}
                <div className="mt-[8px] flex flex-wrap items-center gap-[6px] text-[11px]">
                  {contact.wecom_userid ? (
                    <span className="rounded-full bg-[#eef4f2] px-[9px] py-[3px] text-[#0f766e]">
                      企微 <span data-i18n-ignore>{contact.wecom_userid}</span>
                    </span>
                  ) : null}
                  {contact.feishu_openid ? (
                    <span className="rounded-full bg-[#eef4f2] px-[9px] py-[3px] text-[#0f766e]">
                      飞书 <span data-i18n-ignore>{contact.feishu_openid}</span>
                    </span>
                  ) : null}
                  {contact.dingtalk_userid ? (
                    <span className="rounded-full bg-[#eef4f2] px-[9px] py-[3px] text-[#0f766e]">
                      钉钉 <span data-i18n-ignore>{contact.dingtalk_userid}</span>
                    </span>
                  ) : null}
                  {contact.primary_user_id ? (
                    <span className="rounded-full bg-[#f6f7fa] px-[9px] py-[3px] text-[#697085]">
                      已关联对话账号
                    </span>
                  ) : null}
                </div>
              </div>
              {canWrite ? (
                <div className="flex shrink-0 gap-[8px]">
                  <UIButton
                    variant="outline"
                    className={OUTLINE_ACTION_BUTTON_CLASS}
                    aria-label={`编辑 ${contact.name}`}
                    onClick={() => setDraft(draftFromContact(contact))}
                  >
                    编辑
                  </UIButton>
                  <UIButton
                    variant="outline"
                    className={OUTLINE_ACTION_BUTTON_CLASS}
                    aria-label={`删除 ${contact.name}`}
                    onClick={() => setDeleteTarget(contact)}
                  >
                    删除
                  </UIButton>
                </div>
              ) : null}
            </div>
          ))}
        </div>
      )}

      <Dialog open={Boolean(draft)} onOpenChange={(open) => !open && setDraft(null)}>
        <DialogContent className="max-h-[88vh] overflow-y-auto">
          <DialogTitle>{draft?.id ? '编辑联系人' : '新增联系人'}</DialogTitle>
          <label className="flex flex-col gap-[6px] text-[12px] font-medium text-[#464c5e]">
            姓名
            <Input
              value={draft?.name || ''}
              onChange={(event) => setDraft((current) => current ? { ...current, name: event.target.value } : current)}
              placeholder="例如：张三"
            />
          </label>
          {TEXT_FIELDS.map((field) => (
            <label key={field.key} className="flex flex-col gap-[6px] text-[12px] font-medium text-[#464c5e]">
              {field.label}
              <Input
                value={draft?.[field.key] || ''}
                onChange={(event) => setDraft((current) => (
                  current ? { ...current, [field.key]: event.target.value } : current
                ))}
                placeholder={field.placeholder}
              />
            </label>
          ))}
          <label className="flex flex-col gap-[6px] text-[12px] font-medium text-[#464c5e]">
            备注
            <Textarea
              value={draft?.notes || ''}
              onChange={(event) => setDraft((current) => current ? { ...current, notes: event.target.value } : current)}
              placeholder="选填"
            />
          </label>
          <div className={DIALOG_FOOTER_CLASS}>
            <UIButton variant="outline" className={DIALOG_CANCEL_BUTTON_CLASS} onClick={() => setDraft(null)}>
              取消
            </UIButton>
            <UIButton className={DIALOG_PRIMARY_BUTTON_CLASS} disabled={saving} onClick={() => void saveDraft()}>
              保存
            </UIButton>
          </div>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={Boolean(deleteTarget)}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
        title={`删除「${deleteTarget?.name || ''}」？`}
        description="删除后这条联系人记录无法恢复。"
        onConfirm={() => void confirmDelete()}
      />
    </div>
  );
}

function EmptyState({ text }: { text: string }) {
  return (
    <div className="flex min-h-[240px] flex-col items-center justify-center rounded-[16px] border border-dashed border-[#e3e7f1] bg-white px-[24px] text-center">
      <p className="m-0 text-[14px] font-medium text-[#17191f]">{text}</p>
    </div>
  );
}
