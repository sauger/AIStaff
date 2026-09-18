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
import { SecretType } from '../enums/secretType';
import type { AgentProfileRead, EmployeeSecretListResponse, EmployeeSecretRead } from '../types';

type SecretsPageProps = {
  currentUser?: EnterpriseAuthUser;
  onLogout?: () => void;
};

type SecretDraft = {
  id?: string;
  name: string;
  description: string;
  secret_type: SecretType;
  username: string;
  value: string;
};

const EMPTY_DRAFT: SecretDraft = {
  name: '',
  description: '',
  secret_type: SecretType.Password,
  username: '',
  value: '',
};

function apiErrorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return '操作失败';
}

function typeLabel(type: string): string {
  return type === SecretType.SessionSnapshot ? '会话快照' : '密码';
}

export default function SecretsPage({ currentUser, onLogout }: SecretsPageProps = {}) {
  const [agentId, setAgentId] = useState(readEmployeeScope);
  const [agents, setAgents] = useState<AgentProfileRead[]>([]);
  const [agentScopeLoaded, setAgentScopeLoaded] = useState(false);
  const [listing, setListing] = useState<EmployeeSecretListResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [draft, setDraft] = useState<SecretDraft | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<EmployeeSecretRead | null>(null);
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
    void loadSecrets();
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

  async function loadSecrets() {
    if (!agentId || isTeamScope(agentId)) return;
    const seq = ++loadSeqRef.current;
    setLoading(true);
    try {
      const result = await api.get<EmployeeSecretListResponse>(
        `/api/enterprise/secrets?tenant_id=${encodeURIComponent(TENANT_ID)}&agent_id=${encodeURIComponent(agentId)}`,
      );
      if (seq !== loadSeqRef.current) return;
      setListing(result);
    } catch (error) {
      if (seq !== loadSeqRef.current) return;
      notify.error(apiErrorMessage(error));
      setListing(null);
    } finally {
      if (seq === loadSeqRef.current) setLoading(false);
    }
  }

  async function saveDraft() {
    if (!draft || !agentId) return;
    const name = draft.name.trim();
    if (!name) {
      notify.error('请填写名称');
      return;
    }
    try {
      if (draft.id) {
        const payload: Record<string, string> = {
          tenant_id: TENANT_ID,
          name,
          description: draft.description.trim(),
        };
        if (draft.username.trim()) payload.username = draft.username.trim();
        if (draft.value.trim()) payload.value = draft.value;
        await api.patch(
          `/api/enterprise/secrets/${encodeURIComponent(draft.id)}?agent_id=${encodeURIComponent(agentId)}`,
          payload,
        );
        notify.success('已更新密钥');
      } else {
        if (draft.secret_type === SecretType.Password && !draft.username.trim()) {
          notify.error('密码类型密钥需要填写用户名');
          return;
        }
        if (!draft.value.trim()) {
          notify.error('请填写密钥内容。保存后密文不会回显。');
          return;
        }
        await api.post(
          `/api/enterprise/secrets?agent_id=${encodeURIComponent(agentId)}`,
          {
            tenant_id: TENANT_ID,
            name,
            description: draft.description.trim(),
            secret_type: draft.secret_type,
            username: draft.username.trim() || undefined,
            value: draft.value,
          },
        );
        notify.success('已保存密钥');
      }
      setDraft(null);
      await loadSecrets();
    } catch (error) {
      notify.error(apiErrorMessage(error));
    }
  }

  async function confirmDelete() {
    if (!deleteTarget || !agentId) return;
    try {
      await api.delete(
        `/api/enterprise/secrets/${encodeURIComponent(deleteTarget.id)}?tenant_id=${encodeURIComponent(TENANT_ID)}&agent_id=${encodeURIComponent(agentId)}`,
      );
      notify.success('已删除密钥');
      setDeleteTarget(null);
      await loadSecrets();
    } catch (error) {
      notify.error(apiErrorMessage(error));
    }
  }

  if (!agentScopeLoaded) return <CapabilityScopeLoading />;

  if (isTeamScope(agentId) || !agentId) {
    return (
      <div className="min-h-full box-border px-[48px] pt-[32px] pb-[43px] max-[900px]:px-[16px]">
        <AppHeader
          onLogout={onLogout}
          userName={currentUser?.username}
          title="密钥"
          description="保存加密的登录凭证（密码 / 会话快照）。密文永不在任何界面回显，模型只能看到名称与说明。"
        />
        <EmptyState text="请先选择一个数字员工，再管理密钥。" />
      </div>
    );
  }

  const secrets = listing?.secrets || [];
  const empty = !loading && secrets.length === 0;

  return (
    <div className="min-h-full box-border px-[48px] pt-[32px] pb-[43px] max-[900px]:px-[16px]">
      <AppHeader
        onLogout={onLogout}
        userName={currentUser?.username}
        title="密钥"
        description="保存加密的登录凭证（密码 / 会话快照）。密文永不在任何界面回显，模型只能看到名称与说明。"
      />

      {!canWrite ? (
        <p className="mt-[16px] rounded-[10px] bg-[#f6f7fa] px-[12px] py-[8px] text-[12px] text-[#697085]">
          管理员正在查阅其他员工的密钥，只能看名称、说明和类型，不能新增、编辑或删除。
        </p>
      ) : (
        <p className="mt-[16px] rounded-[10px] bg-[#f6f7fa] px-[12px] py-[8px] text-[12px] text-[#697085]">
          密文一旦保存就不会再显示——编辑时密码框永远是空的，留空代表不改动。邮箱 IMAP/SMTP 密码请到邮件页保管。
        </p>
      )}

      <div className="mt-[20px] mb-[16px] flex justify-end">
        {canWrite ? (
          <UIButton
            className={cn(DIALOG_PRIMARY_BUTTON_CLASS, 'h-[34px]')}
            onClick={() => setDraft({ ...EMPTY_DRAFT })}
          >
            新增密钥
          </UIButton>
        ) : null}
      </div>

      {empty ? (
        <EmptyState text="还没有密钥，先新增一条吧。" />
      ) : (
        <div className="flex flex-col gap-[12px]">
          {secrets.map((secret) => (
            <div
              key={secret.id}
              className="flex items-start justify-between gap-[16px] rounded-[16px] border border-[#eceef1] bg-white p-[16px]"
            >
              <div className="min-w-0">
                <p className="m-0 text-[14px] font-medium text-[#17191f]">{secret.name}</p>
                <p className="mt-[4px] mb-0 text-[12px] text-[#858b9c]">
                  {secret.description || '（未填写说明）'}
                </p>
                <div className="mt-[8px] flex flex-wrap items-center gap-[6px] text-[11px]">
                  <span className="rounded-full bg-[#eef4f2] px-[9px] py-[3px] font-medium text-[#0f766e]">
                    {typeLabel(secret.secret_type)}
                  </span>
                  <span className="rounded-full bg-[#f6f7fa] px-[9px] py-[3px] text-[#697085]">
                    更新于 {formatDateTime(secret.updated_at)}
                  </span>
                  {secret.linked_login_guide_name ? (
                    <span className="rounded-full bg-[#fff6e6] px-[9px] py-[3px] text-[#b45309]">
                      关联登录说明：{secret.linked_login_guide_name}
                    </span>
                  ) : null}
                  <span className="rounded-full border border-dashed border-[#ded7cc] px-[9px] py-[3px] text-[#858b9c]">
                    密文不会显示
                  </span>
                </div>
              </div>
              {canWrite ? (
                <div className="flex shrink-0 gap-[8px]">
                  <UIButton
                    variant="outline"
                    className={OUTLINE_ACTION_BUTTON_CLASS}
                    onClick={() => setDraft({
                      id: secret.id,
                      name: secret.name,
                      description: secret.description,
                      secret_type: secret.secret_type as SecretType,
                      username: '',
                      value: '',
                    })}
                  >
                    编辑
                  </UIButton>
                  <UIButton
                    variant="outline"
                    className={OUTLINE_ACTION_BUTTON_CLASS}
                    onClick={() => setDeleteTarget(secret)}
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
        <DialogContent>
          <DialogTitle>{draft?.id ? '编辑密钥' : '新增密钥'}</DialogTitle>
          <label className="flex flex-col gap-[6px] text-[12px] font-medium text-[#464c5e]">
            名称
            <Input
              value={draft?.name || ''}
              onChange={(event) => setDraft((current) => current ? { ...current, name: event.target.value } : current)}
              placeholder="例如：Token Channel 主账号"
            />
          </label>
          <label className="flex flex-col gap-[6px] text-[12px] font-medium text-[#464c5e]">
            说明（推荐填写）
            <Textarea
              value={draft?.description || ''}
              onChange={(event) => setDraft((current) => current ? { ...current, description: event.target.value } : current)}
              placeholder="这条密钥是给谁用的、用来做什么"
            />
          </label>
          {!draft?.id ? (
            <div className="flex gap-[8px]">
              <button
                type="button"
                className={cn(
                  'flex-1 rounded-[12px] border-[1.5px] px-[12px] py-[10px] text-left',
                  draft?.secret_type === SecretType.Password ? 'border-[#0f766e] bg-[#e1f1ed]' : 'border-[#e3e7f1] bg-white',
                )}
                onClick={() => setDraft((current) => current ? { ...current, secret_type: SecretType.Password } : current)}
              >
                <strong className="block text-[13px]">密码</strong>
                <span className="text-[11px] text-[#858b9c]">用户名 + 密码登录</span>
              </button>
              <button
                type="button"
                className={cn(
                  'flex-1 rounded-[12px] border-[1.5px] px-[12px] py-[10px] text-left',
                  draft?.secret_type === SecretType.SessionSnapshot ? 'border-[#0f766e] bg-[#e1f1ed]' : 'border-[#e3e7f1] bg-white',
                )}
                onClick={() => setDraft((current) => current ? { ...current, secret_type: SecretType.SessionSnapshot } : current)}
              >
                <strong className="block text-[13px]">会话快照</strong>
                <span className="text-[11px] text-[#858b9c]">storage-state，跳过验证码/双因素</span>
              </button>
            </div>
          ) : null}
          {draft?.secret_type === SecretType.Password ? (
            <label className="flex flex-col gap-[6px] text-[12px] font-medium text-[#464c5e]">
              用户名{draft.id ? '（留空不改）' : ''}
              <Input
                value={draft?.username || ''}
                onChange={(event) => setDraft((current) => current ? { ...current, username: event.target.value } : current)}
                placeholder={draft.id ? '已加密 · 留空表示不修改' : '登录用户名'}
                autoComplete="off"
              />
            </label>
          ) : null}
          <label className="flex flex-col gap-[6px] text-[12px] font-medium text-[#464c5e]">
            {draft?.secret_type === SecretType.SessionSnapshot ? '会话快照内容' : '密码'}
            {draft?.id ? '（留空不改）' : ''}
            <Input
              type="password"
              value={draft?.value || ''}
              onChange={(event) => setDraft((current) => current ? { ...current, value: event.target.value } : current)}
              placeholder={draft?.id ? '已加密 · 留空表示不修改，重新输入即可更新' : '保存后密文不会再显示'}
              autoComplete="new-password"
            />
            <span className="font-normal text-[11px] text-[#858b9c]">保存后密文不会再显示，UI 和对话都只展示名称与说明。</span>
          </label>
          <div className={DIALOG_FOOTER_CLASS}>
            <UIButton variant="outline" className={DIALOG_CANCEL_BUTTON_CLASS} onClick={() => setDraft(null)}>
              取消
            </UIButton>
            <UIButton className={DIALOG_PRIMARY_BUTTON_CLASS} onClick={() => void saveDraft()}>
              保存
            </UIButton>
          </div>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={Boolean(deleteTarget)}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
        title={`删除「${deleteTarget?.name || ''}」？`}
        description="删除后无法恢复密文。绑定了这条密钥的登录说明会变成未绑定。"
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
