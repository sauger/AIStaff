import { useCallback, useEffect, useState } from 'react';

import { ConfirmDialog } from '@/components/ConfirmDialog';
import { Input } from '@/components/ui';
import { Button as UIButton } from '@/components/ui/button';
import { notify } from '@/components/ui/app-toast';

import { api, TENANT_ID } from '../../api/client';
import type { EnterpriseAuthUser } from '../../auth';
import { CompanyShell } from './CompanyPage';
import WecomTempIdentitySection from './WecomTempIdentitySection';

const PRIMARY_BUTTON_CLASS =
  'h-8 gap-1 rounded-[10px] bg-[#18181a] px-5 text-[12px] font-normal text-white hover:bg-[#303030]';
const OUTLINE_BUTTON_CLASS =
  'h-8 gap-1 rounded-[10px] border-[#e3e7f1] px-5 text-[12px] font-normal text-[#464c5e] hover:bg-[#f6f6f6] hover:text-[#18181a]';

type WecomAppView = {
  id: string;
  name: string;
  corp_id: string;
  agentid: string;
  status: string;
  is_default: boolean;
  has_secret: boolean;
  has_callback_token: boolean;
  has_callback_aes_key: boolean;
  last_check_ok?: boolean | null;
  last_check_summary?: string | null;
  callback_url: string;
  callback_url_absolute: boolean;
  callback_url_hint?: string | null;
};

type AppForm = {
  name: string;
  corp_id: string;
  agentid: string;
  secret: string;
  callback_token: string;
  callback_aes_key: string;
  status: 'active' | 'disabled';
};

const EMPTY_FORM: AppForm = {
  name: '',
  corp_id: '',
  agentid: '',
  secret: '',
  callback_token: '',
  callback_aes_key: '',
  status: 'active',
};

function tenantQuery(): string {
  return `tenant_id=${encodeURIComponent(TENANT_ID)}`;
}

export default function WecomAppsPage({
  currentUser,
  onLogout,
}: {
  currentUser?: EnterpriseAuthUser;
  onLogout?: () => void;
}) {
  const [apps, setApps] = useState<WecomAppView[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState<AppForm>(EMPTY_FORM);
  const [pendingDelete, setPendingDelete] = useState<WecomAppView | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.get<{ apps: WecomAppView[] }>(
        `/api/enterprise/wecom-apps?${tenantQuery()}`,
      );
      setApps(res.apps || []);
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '加载企微应用失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  function startCreate() {
    setCreating(true);
    setEditingId(null);
    setForm(EMPTY_FORM);
  }

  function startEdit(app: WecomAppView) {
    setCreating(false);
    setEditingId(app.id);
    setForm({
      name: app.name,
      corp_id: app.corp_id,
      agentid: app.agentid,
      secret: '',
      callback_token: '',
      callback_aes_key: '',
      status: app.status === 'disabled' ? 'disabled' : 'active',
    });
  }

  function closeForm() {
    setCreating(false);
    setEditingId(null);
    setForm(EMPTY_FORM);
  }

  async function saveApp() {
    const name = form.name.trim();
    const corpId = form.corp_id.trim();
    const agentId = form.agentid.trim();
    const secret = form.secret.trim();
    const token = form.callback_token.trim();
    const aes = form.callback_aes_key.trim();
    if (!name || !corpId || !agentId) {
      notify.error('请填写名称、企业 ID 与 AgentId');
      return;
    }
    if (!editingId && !secret) {
      notify.error('请填写 Secret');
      return;
    }
    if (Boolean(token) !== Boolean(aes)) {
      notify.error('回调 Token 与 EncodingAESKey 必须成对填写');
      return;
    }
    setSaving(true);
    try {
      if (editingId) {
        const body: Record<string, string> = {
          name,
          corp_id: corpId,
          agentid: agentId,
          status: form.status,
        };
        if (secret) body.secret = secret;
        if (token || aes) {
          body.callback_token = token;
          body.callback_aes_key = aes;
        }
        await api.patch(`/api/enterprise/wecom-apps/${encodeURIComponent(editingId)}?${tenantQuery()}`, body);
        notify.success('企微应用已更新');
      } else {
        await api.post(`/api/enterprise/wecom-apps?${tenantQuery()}`, {
          name,
          corp_id: corpId,
          agentid: agentId,
          secret,
          callback_token: token || null,
          callback_aes_key: aes || null,
        });
        notify.success('企微应用已创建');
      }
      closeForm();
      await load();
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '保存企微应用失败');
    } finally {
      setSaving(false);
    }
  }

  async function makeDefault(app: WecomAppView) {
    try {
      await api.post(
        `/api/enterprise/wecom-apps/${encodeURIComponent(app.id)}/default?${tenantQuery()}`,
      );
      notify.success('已设为默认应用');
      await load();
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '设置默认应用失败');
    }
  }

  async function runCheck(app: WecomAppView) {
    try {
      const result = await api.post<{ ok: boolean; summary: string; agent_name?: string }>(
        `/api/enterprise/wecom-apps/${encodeURIComponent(app.id)}/check?${tenantQuery()}`,
      );
      const detail = result.agent_name ? `${result.summary}：${result.agent_name}` : result.summary;
      if (result.ok) notify.success(detail);
      else notify.error(detail);
      await load();
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '连通性自检失败');
    }
  }

  async function confirmDelete() {
    if (!pendingDelete) return;
    try {
      await api.delete(
        `/api/enterprise/wecom-apps/${encodeURIComponent(pendingDelete.id)}?${tenantQuery()}`,
      );
      notify.success('已删除企微应用');
      setPendingDelete(null);
      if (editingId === pendingDelete.id) closeForm();
      await load();
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '删除企微应用失败');
    }
  }

  async function copyCallback(app: WecomAppView) {
    try {
      await navigator.clipboard.writeText(app.callback_url);
      if (app.callback_url_absolute) {
        notify.success('回调地址已复制');
      } else {
        notify.success(app.callback_url_hint || '已复制回调路径。请先配置公网基地址，再贴进企微后台');
      }
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '复制回调地址失败');
    }
  }

  const editingApp = apps.find((app) => app.id === editingId) || null;
  const formOpen = creating || editingId !== null;

  return (
    <CompanyShell currentUser={currentUser} onLogout={onLogout}>
      <section className="flex flex-col gap-[16px]" data-testid="wecom-apps-page">
        <div className="flex flex-wrap items-start justify-between gap-[12px]">
          <div className="flex min-w-0 flex-col gap-[4px]">
            <span className="text-[14px] font-semibold text-[#18181a]">企微自建应用</span>
            <span className="max-w-[720px] text-[12px] leading-[1.6] text-[#858b9c]">
              自建应用是租户级凭证，和智能机器人不是同一套。Secret 只保存在租户上，员工侧不填写。
            </span>
          </div>
          <UIButton className={PRIMARY_BUTTON_CLASS} onClick={startCreate}>
            新建应用
          </UIButton>
        </div>

        {loading ? (
          <span className="text-[12px] text-[#858b9c]">正在加载企微应用</span>
        ) : apps.length === 0 && !formOpen ? (
          <div className="rounded-[12px] border border-dashed border-[#e3e7f1] bg-[#fafbfc] px-[16px] py-[28px] text-center">
            <p className="m-0 text-[13px] text-[#18181a]">还没有企微自建应用</p>
            <p className="mx-auto mt-[6px] max-w-[520px] text-[12px] leading-[1.6] text-[#858b9c]">
              自建应用是租户级凭证，和智能机器人不是同一套。创建后可复制回调地址并做连通性自检。
            </p>
            <UIButton className={`${PRIMARY_BUTTON_CLASS} mt-[12px]`} onClick={startCreate}>
              新建应用
            </UIButton>
          </div>
        ) : (
          <ul className="m-0 flex list-none flex-col gap-[10px] p-0">
            {apps.map((app) => (
              <li
                key={app.id}
                className="flex flex-col gap-[10px] rounded-[12px] border border-[#eef0f4] bg-white px-[16px] py-[12px]"
              >
                <div className="flex flex-wrap items-center gap-[8px] text-[13px] text-[#18181a]">
                  <strong className="font-medium">{app.name}</strong>
                  {app.is_default && (
                    <span className="rounded-[8px] bg-[#f3e8ff] px-[8px] py-[2px] text-[11px] text-[#6d28d9]">
                      默认
                    </span>
                  )}
                  {app.status === 'disabled' && (
                    <span className="rounded-[8px] bg-[#f6f6f6] px-[8px] py-[2px] text-[11px] text-[#858b9c]">
                      已停用
                    </span>
                  )}
                </div>
                <div className="flex flex-wrap gap-x-[16px] gap-y-[4px] text-[12px] text-[#464c5e]">
                  <span>企业 ID {app.corp_id}</span>
                  <span>AgentId {app.agentid || '未填写'}</span>
                  <span>
                    自检 {app.last_check_summary || '尚未自检'}
                  </span>
                  {app.has_secret && <span>Secret 已存</span>}
                  {app.has_callback_token && <span>回调 Token 已存</span>}
                  {app.has_callback_aes_key && <span>EncodingAESKey 已存</span>}
                </div>
                <div className="flex flex-wrap items-center gap-[8px] text-[12px] text-[#464c5e]">
                  <span className="min-w-0 break-all">{app.callback_url}</span>
                  {!app.callback_url_absolute && app.callback_url_hint && (
                    <span className="text-[#a0a6b8]">{app.callback_url_hint}</span>
                  )}
                  <UIButton
                    variant="outline"
                    className={OUTLINE_BUTTON_CLASS}
                    onClick={() => void copyCallback(app)}
                  >
                    复制回调地址
                  </UIButton>
                </div>
                <div className="flex flex-wrap gap-[8px]">
                  <UIButton variant="outline" className={OUTLINE_BUTTON_CLASS} onClick={() => startEdit(app)}>
                    编辑
                  </UIButton>
                  <UIButton
                    variant="outline"
                    className={OUTLINE_BUTTON_CLASS}
                    disabled={app.is_default || app.status === 'disabled'}
                    onClick={() => void makeDefault(app)}
                  >
                    设为默认
                  </UIButton>
                  <UIButton variant="outline" className={OUTLINE_BUTTON_CLASS} onClick={() => void runCheck(app)}>
                    连通性自检
                  </UIButton>
                  <UIButton variant="outline" className={OUTLINE_BUTTON_CLASS} onClick={() => setPendingDelete(app)}>
                    删除
                  </UIButton>
                </div>
              </li>
            ))}
          </ul>
        )}

        {formOpen && (
          <div className="flex flex-col gap-[12px] rounded-[12px] bg-[#fafbfc] p-[16px]">
            <span className="text-[12px] font-medium text-[#464c5e]">
              {editingId ? '编辑应用' : '新建应用'}
            </span>
            {editingApp && (
              <span className="text-[12px] text-[#858b9c]">
                {editingApp.has_secret ? 'Secret 已存' : ''}
                {editingApp.has_callback_token ? ' · 回调 Token 已存' : ''}
                {editingApp.has_callback_aes_key ? ' · EncodingAESKey 已存' : ''}
              </span>
            )}
            {(
              [
                ['name', '名称', false],
                ['corp_id', '企业 ID', false],
                ['agentid', 'AgentId', false],
                ['secret', 'Secret', true],
                ['callback_token', '回调 Token', true],
                ['callback_aes_key', 'EncodingAESKey', true],
              ] as const
            ).map(([key, label, secret]) => (
              <label key={key} className="flex flex-col gap-[6px] text-[12px] text-[#464c5e]">
                {label}
                <Input
                  type={secret ? 'password' : 'text'}
                  value={form[key]}
                  autoComplete="off"
                  placeholder={secret && editingId ? '重新输入以更新密钥' : ''}
                  onChange={(event) => setForm((prev) => ({ ...prev, [key]: event.target.value }))}
                  className="h-8 rounded-[10px] text-[12px]"
                />
              </label>
            ))}
            {editingId && (
              <label className="flex flex-col gap-[6px] text-[12px] text-[#464c5e]">
                状态
                <select
                  aria-label="应用状态"
                  value={form.status}
                  onChange={(event) =>
                    setForm((prev) => ({
                      ...prev,
                      status: event.target.value === 'disabled' ? 'disabled' : 'active',
                    }))
                  }
                  className="h-8 rounded-[10px] border border-[#e3e7f1] bg-white px-2 text-[12px]"
                >
                  <option value="active">启用</option>
                  <option value="disabled">停用</option>
                </select>
              </label>
            )}
            <div className="flex justify-end gap-[8px]">
              <UIButton variant="outline" className={OUTLINE_BUTTON_CLASS} onClick={closeForm}>
                取消
              </UIButton>
              <UIButton className={PRIMARY_BUTTON_CLASS} disabled={saving} onClick={() => void saveApp()}>
                保存
              </UIButton>
            </div>
          </div>
        )}
      </section>

      <WecomTempIdentitySection currentUser={currentUser} />

      <ConfirmDialog
        open={pendingDelete !== null}
        onOpenChange={(open) => {
          if (!open) setPendingDelete(null);
        }}
        title="删除企微应用"
        description="删除后该应用的回调立即失效。若它是默认应用且还有其他应用，请先改默认。"
        confirmText="删除"
        onConfirm={() => void confirmDelete()}
      />
    </CompanyShell>
  );
}
