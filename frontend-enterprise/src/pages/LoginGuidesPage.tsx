import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';

import AppHeader from '@/components/AppHeader';
import CapabilityScopeLoading from '@/components/CapabilityScopeLoading';
import { ConfirmDialog } from '@/components/ConfirmDialog';
import {
  Dialog,
  DialogContent,
  DialogTitle,
  Input,
  Switch,
  UnderlineTabs,
  type UnderlineTabItem,
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
import type {
  AgentProfileRead,
  EmployeeSecretListResponse,
  EmployeeSecretRead,
  LoginGuideGalleryItem,
  LoginGuideListResponse,
  LoginGuideRead,
  LoginResult,
} from '../types';

type LoginGuidesPageProps = {
  currentUser?: EnterpriseAuthUser;
  onLogout?: () => void;
};

type GuideTab = 'mine' | 'gallery';

const TABS: UnderlineTabItem<GuideTab>[] = [
  { value: 'mine', label: '我的说明' },
  { value: 'gallery', label: '广场' },
];

type GuideDraft = {
  id?: string;
  name: string;
  url: string;
  username_selector: string;
  username_label: string;
  password_selector: string;
  password_label: string;
  submit_selector: string;
  submit_label: string;
  default_secret_name: string;
  published: boolean;
};

const EMPTY_DRAFT: GuideDraft = {
  name: '',
  url: '',
  username_selector: '',
  username_label: '用户名',
  password_selector: '',
  password_label: '密码',
  submit_selector: '',
  submit_label: '登录',
  default_secret_name: '',
  published: false,
};

function apiErrorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return '操作失败';
}

function toDraft(guide: LoginGuideRead): GuideDraft {
  return {
    id: guide.id,
    name: guide.name,
    url: guide.url,
    username_selector: guide.username_selector,
    username_label: guide.username_label,
    password_selector: guide.password_selector,
    password_label: guide.password_label,
    submit_selector: guide.submit_selector,
    submit_label: guide.submit_label,
    default_secret_name: guide.default_secret_name || '',
    published: guide.published,
  };
}

export default function LoginGuidesPage({ currentUser, onLogout }: LoginGuidesPageProps = {}) {
  const [agentId, setAgentId] = useState(readEmployeeScope);
  const [agents, setAgents] = useState<AgentProfileRead[]>([]);
  const [agentScopeLoaded, setAgentScopeLoaded] = useState(false);
  const [tab, setTab] = useState<GuideTab>('mine');
  const [listing, setListing] = useState<LoginGuideListResponse | null>(null);
  const [secrets, setSecrets] = useState<EmployeeSecretRead[]>([]);
  const [gallery, setGallery] = useState<LoginGuideGalleryItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [draft, setDraft] = useState<GuideDraft | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<LoginGuideRead | null>(null);
  const [results, setResults] = useState<Record<string, LoginResult>>({});
  const [testingId, setTestingId] = useState('');
  const [copiedId, setCopiedId] = useState('');
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
    void loadMine();
  }, [agentId, agentScopeLoaded]);

  useEffect(() => {
    if (!agentScopeLoaded) return;
    void loadGallery();
  }, [agentScopeLoaded]);

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

  async function loadMine() {
    if (!agentId || isTeamScope(agentId)) return;
    const seq = ++loadSeqRef.current;
    setLoading(true);
    try {
      const [guides, secretList] = await Promise.all([
        api.get<LoginGuideListResponse>(
          `/api/enterprise/login-guides?tenant_id=${encodeURIComponent(TENANT_ID)}&agent_id=${encodeURIComponent(agentId)}`,
        ),
        api.get<EmployeeSecretListResponse>(
          `/api/enterprise/secrets?tenant_id=${encodeURIComponent(TENANT_ID)}&agent_id=${encodeURIComponent(agentId)}`,
        ).catch(() => ({ agent_id: agentId, can_write: false, secrets: [] as EmployeeSecretRead[] })),
      ]);
      if (seq !== loadSeqRef.current) return;
      setListing(guides);
      setSecrets(secretList.secrets);
    } catch (error) {
      if (seq !== loadSeqRef.current) return;
      notify.error(apiErrorMessage(error));
      setListing(null);
    } finally {
      if (seq === loadSeqRef.current) setLoading(false);
    }
  }

  async function loadGallery() {
    try {
      const rows = await api.get<LoginGuideGalleryItem[]>(
        `/api/enterprise/login-guides/gallery?tenant_id=${encodeURIComponent(TENANT_ID)}`,
      );
      setGallery(rows);
    } catch (error) {
      notify.error(apiErrorMessage(error));
    }
  }

  async function saveDraft() {
    if (!draft || !agentId) return;
    if (!draft.name.trim() || !draft.url.trim()) {
      notify.error('请填写名称和登录地址');
      return;
    }
    const payload = {
      tenant_id: TENANT_ID,
      name: draft.name.trim(),
      url: draft.url.trim(),
      username_selector: draft.username_selector.trim(),
      username_label: draft.username_label.trim() || '用户名',
      password_selector: draft.password_selector.trim(),
      password_label: draft.password_label.trim() || '密码',
      submit_selector: draft.submit_selector.trim(),
      submit_label: draft.submit_label.trim() || '登录',
      default_secret_name: draft.default_secret_name.trim() || null,
      published: draft.published,
    };
    try {
      if (draft.id) {
        await api.patch(
          `/api/enterprise/login-guides/${encodeURIComponent(draft.id)}?agent_id=${encodeURIComponent(agentId)}`,
          payload,
        );
        notify.success('已更新登录说明');
      } else {
        await api.post(
          `/api/enterprise/login-guides?agent_id=${encodeURIComponent(agentId)}`,
          payload,
        );
        notify.success('已保存登录说明');
      }
      setDraft(null);
      await loadMine();
      await loadGallery();
    } catch (error) {
      notify.error(apiErrorMessage(error));
    }
  }

  async function confirmDelete() {
    if (!deleteTarget || !agentId) return;
    try {
      await api.delete(
        `/api/enterprise/login-guides/${encodeURIComponent(deleteTarget.id)}?tenant_id=${encodeURIComponent(TENANT_ID)}&agent_id=${encodeURIComponent(agentId)}`,
      );
      notify.success('已删除登录说明');
      setDeleteTarget(null);
      await loadMine();
    } catch (error) {
      notify.error(apiErrorMessage(error));
    }
  }

  async function runTest(guide: LoginGuideRead) {
    if (!agentId) return;
    setTestingId(guide.id);
    try {
      const result = await api.post<LoginResult>(
        `/api/enterprise/login-guides/${encodeURIComponent(guide.id)}/test-login?tenant_id=${encodeURIComponent(TENANT_ID)}&agent_id=${encodeURIComponent(agentId)}`,
        {},
      );
      setResults((current) => ({ ...current, [guide.id]: result }));
      if (result.success) {
        notify.success(result.message);
        await loadMine();
      } else {
        notify.error(result.message);
      }
    } catch (error) {
      notify.error(apiErrorMessage(error));
    } finally {
      setTestingId('');
    }
  }

  async function copyGuide(item: LoginGuideGalleryItem) {
    if (!agentId || isTeamScope(agentId)) {
      notify.error('请先选择要复制到的数字员工');
      return;
    }
    if (!currentAgent || !isEmployeeOwnedBy(currentAgent, currentUser)) {
      notify.error('只能复制到自己拥有的数字员工');
      return;
    }
    try {
      const copied = await api.post<LoginGuideRead>(
        `/api/enterprise/login-guides/${encodeURIComponent(item.id)}/copy`,
        { tenant_id: TENANT_ID, target_agent_id: agentId },
      );
      setCopiedId(copied.id);
      setTab('mine');
      notify.success('已复制登录说明，请重新绑定默认密钥');
      await loadMine();
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
          title="登录说明"
          description="结构化记录登录步骤，并绑定一条默认密钥。登录说明可以分享，密钥永远不跟着走。"
        />
        <EmptyState text="请先选择一个数字员工，再管理登录说明。" />
      </div>
    );
  }

  const guides = listing?.guides || [];
  const empty = !loading && guides.length === 0;

  return (
    <div className="min-h-full box-border px-[48px] pt-[32px] pb-[43px] max-[900px]:px-[16px]">
      <AppHeader
        onLogout={onLogout}
        userName={currentUser?.username}
        title="登录说明"
        description="结构化记录登录步骤：地址、用户名/密码字段选择器、提交按钮，并绑定一条默认密钥。登录说明可以分享，密钥永远不跟着走。"
      />

      {!canWrite ? (
        <p className="mt-[16px] rounded-[10px] bg-[#f6f7fa] px-[12px] py-[8px] text-[12px] text-[#697085]">
          管理员正在查阅其他员工的登录说明，只能看步骤元数据，不能编辑、删除或测试登录。
        </p>
      ) : null}

      <div className="mt-[20px] mb-[16px] flex flex-wrap items-center justify-between gap-[12px]">
        <UnderlineTabs items={TABS} value={tab} onChange={setTab} aria-label="登录说明分区" />
        {tab === 'mine' && canWrite ? (
          <UIButton
            className={cn(DIALOG_PRIMARY_BUTTON_CLASS, 'h-[34px]')}
            onClick={() => setDraft({ ...EMPTY_DRAFT })}
          >
            新增登录说明
          </UIButton>
        ) : null}
      </div>

      {tab === 'gallery' ? (
        gallery.length === 0 ? (
          <EmptyState text="广场还没有已发布的登录说明。" />
        ) : (
          <div className="flex flex-col gap-[12px]">
            {gallery.map((item) => (
              <div key={item.id} className="rounded-[16px] border border-[#eceef1] bg-white p-[16px]">
                <p className="m-0 text-[14px] font-medium text-[#17191f]">{item.name}</p>
                <p className="mt-[4px] mb-[10px] text-[12px] text-[#858b9c]">
                  来自 {item.source_agent_name} · 只复制步骤文字，不复制密钥
                </p>
                <dl className="m-0 grid grid-cols-[100px_1fr] gap-x-[12px] gap-y-[6px] text-[12px]">
                  <dt className="text-[#858b9c]">登录地址</dt>
                  <dd className="m-0 break-all font-mono text-[12px]">{item.url}</dd>
                </dl>
                <div className="mt-[12px]">
                  <UIButton variant="outline" className={OUTLINE_ACTION_BUTTON_CLASS} onClick={() => void copyGuide(item)}>
                    复制到当前员工
                  </UIButton>
                </div>
              </div>
            ))}
          </div>
        )
      ) : empty ? (
        <EmptyState text="还没有登录说明。" />
      ) : (
        <div className="flex flex-col gap-[12px]">
          {guides.map((guide) => {
            const result = results[guide.id];
            return (
              <div key={guide.id} className="rounded-[16px] border border-[#eceef1] bg-white p-[16px]">
                <div className="flex items-start justify-between gap-[12px]">
                  <p className="m-0 flex flex-wrap items-center gap-[8px] text-[14px] font-medium text-[#17191f]">
                    {guide.name}
                    {guide.published ? (
                      <span className="rounded-full bg-[#e1f1ed] px-[9px] py-[2px] text-[11px] text-[#0f766e]">已发布到广场</span>
                    ) : null}
                    {guide.copied_from_id ? (
                      <span className="rounded-full bg-[#f6f7fa] px-[9px] py-[2px] text-[11px] text-[#697085]">来自广场复制</span>
                    ) : null}
                  </p>
                  {canWrite ? (
                    <div className="flex shrink-0 gap-[8px]">
                      <UIButton variant="outline" className={OUTLINE_ACTION_BUTTON_CLASS} onClick={() => setDraft(toDraft(guide))}>
                        编辑
                      </UIButton>
                      <UIButton variant="outline" className={OUTLINE_ACTION_BUTTON_CLASS} onClick={() => setDeleteTarget(guide)}>
                        删除
                      </UIButton>
                    </div>
                  ) : null}
                </div>
                {copiedId === guide.id ? (
                  <p className="mt-[10px] rounded-[10px] bg-[#fff7e8] px-[12px] py-[8px] text-[12px] text-[#8a5a00]">
                    密钥绑定为空。从广场复制而来，只带来了步骤文字；请先选择一条默认密钥，才能测试登录。
                  </p>
                ) : null}
                <dl className="mt-[12px] mb-0 grid grid-cols-[100px_1fr] gap-x-[12px] gap-y-[6px] text-[12px]">
                  <dt className="text-[#858b9c]">登录地址</dt>
                  <dd className="m-0 break-all font-mono">{guide.url}</dd>
                  <dt className="text-[#858b9c]">用户名字段</dt>
                  <dd className="m-0 font-mono">{guide.username_selector || '—'} · {guide.username_label}</dd>
                  <dt className="text-[#858b9c]">密码字段</dt>
                  <dd className="m-0 font-mono">{guide.password_selector || '—'} · {guide.password_label}</dd>
                  <dt className="text-[#858b9c]">提交按钮</dt>
                  <dd className="m-0 font-mono">{guide.submit_selector || '—'} · {guide.submit_label}</dd>
                  <dt className="text-[#858b9c]">默认密钥</dt>
                  <dd className={cn('m-0', guide.default_secret_name ? '' : 'font-semibold text-[#dc2626]')}>
                    {guide.default_secret_name || '未绑定'}
                  </dd>
                </dl>
                <div className="mt-[14px] border-t border-dashed border-[#e3e7f1] pt-[14px]">
                  {canWrite ? (
                    <>
                      <UIButton
                        className={cn(DIALOG_PRIMARY_BUTTON_CLASS, 'h-[34px]')}
                        disabled={!guide.default_secret_name || testingId === guide.id}
                        onClick={() => void runTest(guide)}
                      >
                        {testingId === guide.id ? '正在测试登录…' : '测试登录'}
                      </UIButton>
                      {!guide.default_secret_name ? (
                        <p className="mt-[8px] mb-0 text-[12px] text-[#dc2626]">请先绑定默认密钥后再测试登录。</p>
                      ) : null}
                    </>
                  ) : (
                    <p className="m-0 text-[12px] text-[#858b9c]">只读模式：管理员查看其他员工时不可执行测试登录。</p>
                  )}
                  {result ? (
                    <p className={cn(
                      'mt-[10px] rounded-[10px] px-[12px] py-[8px] text-[12px]',
                      result.success ? 'bg-[#e7f6ee] text-[#138a55]' : 'bg-[#fdecec] text-[#dc2626]',
                    )}>
                      {result.success
                        ? `登录成功。本次使用密钥「${result.secret_name_used || ''}」${result.switched_to_snapshot ? '（已从密码密钥切换为会话快照）' : ''}。${result.snapshot_created ? `已自动创建会话快照「${result.snapshot_name || ''}」。` : ''}${result.snapshot_updated ? `已自动更新会话快照「${result.snapshot_name || ''}」。` : ''}密码密钥未改动。`
                        : result.message}
                    </p>
                  ) : null}
                </div>
              </div>
            );
          })}
        </div>
      )}

      <Dialog open={Boolean(draft)} onOpenChange={(open) => !open && setDraft(null)}>
        <DialogContent className="max-h-[88vh] overflow-y-auto">
          <DialogTitle>{draft?.id ? '编辑登录说明' : '新增登录说明'}</DialogTitle>
          <Field label="名称">
            <Input value={draft?.name || ''} onChange={(event) => setDraft((current) => current ? { ...current, name: event.target.value } : current)} />
          </Field>
          <Field label="登录地址">
            <Input value={draft?.url || ''} onChange={(event) => setDraft((current) => current ? { ...current, url: event.target.value } : current)} placeholder="https://" />
          </Field>
          <div className="grid grid-cols-2 gap-[8px] max-[640px]:grid-cols-1">
            <Field label="用户名选择器">
              <Input value={draft?.username_selector || ''} onChange={(event) => setDraft((current) => current ? { ...current, username_selector: event.target.value } : current)} placeholder="#username" />
            </Field>
            <Field label="用户名标签">
              <Input value={draft?.username_label || ''} onChange={(event) => setDraft((current) => current ? { ...current, username_label: event.target.value } : current)} />
            </Field>
            <Field label="密码选择器">
              <Input value={draft?.password_selector || ''} onChange={(event) => setDraft((current) => current ? { ...current, password_selector: event.target.value } : current)} placeholder="#password" />
            </Field>
            <Field label="密码标签">
              <Input value={draft?.password_label || ''} onChange={(event) => setDraft((current) => current ? { ...current, password_label: event.target.value } : current)} />
            </Field>
            <Field label="提交按钮选择器">
              <Input value={draft?.submit_selector || ''} onChange={(event) => setDraft((current) => current ? { ...current, submit_selector: event.target.value } : current)} placeholder="button[type=submit]" />
            </Field>
            <Field label="提交按钮标签">
              <Input value={draft?.submit_label || ''} onChange={(event) => setDraft((current) => current ? { ...current, submit_label: event.target.value } : current)} />
            </Field>
          </div>
          <Field label="默认密钥">
            <select
              className="h-[40px] w-full rounded-[8px] border border-[#e3e7f1] bg-white px-[12px] text-[14px]"
              value={draft?.default_secret_name || ''}
              onChange={(event) => setDraft((current) => current ? { ...current, default_secret_name: event.target.value } : current)}
            >
              <option value="">未绑定</option>
              {secrets.map((secret) => (
                <option key={secret.id} value={secret.name}>
                  {secret.name} · {secret.description || (secret.secret_type === 'session_snapshot' ? '会话快照' : '密码')}
                </option>
              ))}
            </select>
          </Field>
          <label className="flex items-center justify-between gap-[12px] text-[12px] font-medium text-[#464c5e]">
            发布到广场（只分享步骤，不分享密钥）
            <Switch
              checked={Boolean(draft?.published)}
              onCheckedChange={(checked) => setDraft((current) => current ? { ...current, published: checked } : current)}
            />
          </label>
          <div className={DIALOG_FOOTER_CLASS}>
            <UIButton variant="outline" className={DIALOG_CANCEL_BUTTON_CLASS} onClick={() => setDraft(null)}>取消</UIButton>
            <UIButton className={DIALOG_PRIMARY_BUTTON_CLASS} onClick={() => void saveDraft()}>保存</UIButton>
          </div>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={Boolean(deleteTarget)}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
        title={`删除「${deleteTarget?.name || ''}」？`}
        description="只删除登录步骤。关联的会话快照密钥会保留，但不再绑定这条说明。"
        onConfirm={() => void confirmDelete()}
      />
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="flex flex-col gap-[6px] text-[12px] font-medium text-[#464c5e]">
      {label}
      {children}
    </label>
  );
}

function EmptyState({ text }: { text: string }) {
  return (
    <div className="flex min-h-[240px] flex-col items-center justify-center rounded-[16px] border border-dashed border-[#e3e7f1] bg-white px-[24px] text-center">
      <p className="m-0 text-[14px] font-medium text-[#17191f]">{text}</p>
    </div>
  );
}
