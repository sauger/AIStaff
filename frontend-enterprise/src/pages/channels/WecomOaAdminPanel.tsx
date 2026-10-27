import { useCallback, useEffect, useState } from 'react';

import { Input } from '@/components/ui';
import { Button as UIButton } from '@/components/ui/button';
import { notify } from '@/components/ui/app-toast';

import { api, TENANT_ID } from '../../api/client';
import { isEnterpriseAdmin, type EnterpriseAuthUser } from '../../auth';

const PRIMARY_BUTTON_CLASS =
  'h-8 gap-1 rounded-[10px] bg-[#18181a] px-5 text-[12px] font-normal text-white hover:bg-[#303030]';
const OUTLINE_BUTTON_CLASS =
  'h-8 gap-1 rounded-[10px] border-[#e3e7f1] px-5 text-[12px] font-normal text-[#464c5e] hover:bg-[#f6f6f6] hover:text-[#18181a]';

type OaCredentialView = {
  id: string;
  corp_id: string;
  agentid?: string | null;
  status?: string;
  has_secret?: boolean;
  has_callback_token?: boolean;
  has_callback_aes_key?: boolean;
};

type TempBinding = {
  id: string;
  corp_scope: string;
  staffdeck_user_id: string;
  wecom_userid: string;
  display_name?: string | null;
};

type StaffUser = {
  id: string;
  username: string;
  display_name?: string | null;
};

type Props = {
  currentUser?: EnterpriseAuthUser | null;
};

export default function WecomOaAdminPanel({ currentUser }: Props) {
  const isAdmin = isEnterpriseAdmin(currentUser);
  const [credential, setCredential] = useState<OaCredentialView | null>(null);
  const [bindings, setBindings] = useState<TempBinding[]>([]);
  const [users, setUsers] = useState<StaffUser[]>([]);
  const [loading, setLoading] = useState(false);
  const [savingCred, setSavingCred] = useState(false);
  const [savingBinding, setSavingBinding] = useState(false);
  const [credForm, setCredForm] = useState({
    corp_id: '',
    secret: '',
    agentid: '',
    callback_token: '',
    callback_aes_key: '',
  });
  const [bindingForm, setBindingForm] = useState({
    corp_scope: '',
    staffdeck_user_id: '',
    wecom_userid: '',
    display_name: '',
  });

  const load = useCallback(async () => {
    if (!isAdmin) return;
    setLoading(true);
    try {
      const [credRes, bindRes, userRes] = await Promise.all([
        api.get<{ credential: OaCredentialView | null }>(
          `/api/enterprise/wecom-oa/credentials?tenant_id=${encodeURIComponent(TENANT_ID)}`,
        ),
        api.get<{ bindings: TempBinding[] }>(
          `/api/enterprise/wecom-oa/temp-identity-bindings?tenant_id=${encodeURIComponent(TENANT_ID)}`,
        ),
        api.get<StaffUser[]>(`/api/auth/users?tenant_id=${encodeURIComponent(TENANT_ID)}`),
      ]);
      setCredential(credRes.credential);
      setBindings(bindRes.bindings || []);
      setUsers(Array.isArray(userRes) ? userRes : []);
      const savedCorpId = credRes.credential?.corp_id || '';
      if (credRes.credential) {
        setCredForm((prev) => ({
          ...prev,
          corp_id: savedCorpId,
          agentid: credRes.credential?.agentid || '',
          secret: '',
          callback_token: '',
          callback_aes_key: '',
        }));
      }
      if (savedCorpId) {
        setBindingForm((prev) => ({ ...prev, corp_scope: prev.corp_scope || savedCorpId }));
      }
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '加载企微 OA 配置失败');
    } finally {
      setLoading(false);
    }
  }, [isAdmin]);

  useEffect(() => {
    void load();
  }, [load]);

  if (!isAdmin) {
    return null;
  }

  async function saveCredential() {
    if (!credForm.corp_id.trim() || !credForm.secret.trim()) {
      notify.error('请填写企业 ID 与 OA Secret');
      return;
    }
    setSavingCred(true);
    try {
      const res = await api.put<{ credential: OaCredentialView }>(
        `/api/enterprise/wecom-oa/credentials?tenant_id=${encodeURIComponent(TENANT_ID)}`,
        {
          corp_id: credForm.corp_id.trim(),
          secret: credForm.secret.trim(),
          agentid: credForm.agentid.trim() || null,
          callback_token: credForm.callback_token.trim() || null,
          callback_aes_key: credForm.callback_aes_key.trim() || null,
        },
      );
      setCredential(res.credential);
      setCredForm((prev) => ({ ...prev, secret: '', callback_token: '', callback_aes_key: '' }));
      const savedCorpId = res.credential?.corp_id || '';
      if (savedCorpId) {
        setBindingForm((prev) => ({ ...prev, corp_scope: prev.corp_scope || savedCorpId }));
      }
      notify.success('OA 应用凭证已保存');
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '保存 OA 凭证失败');
    } finally {
      setSavingCred(false);
    }
  }

  async function saveBinding() {
    if (
      !bindingForm.corp_scope.trim()
      || !bindingForm.staffdeck_user_id.trim()
      || !bindingForm.wecom_userid.trim()
    ) {
      notify.error('请填写企业范围、StaffDeck 用户与企微 userid');
      return;
    }
    setSavingBinding(true);
    try {
      await api.post(
        `/api/enterprise/wecom-oa/temp-identity-bindings?tenant_id=${encodeURIComponent(TENANT_ID)}`,
        {
          corp_scope: bindingForm.corp_scope.trim(),
          staffdeck_user_id: bindingForm.staffdeck_user_id.trim(),
          wecom_userid: bindingForm.wecom_userid.trim(),
          display_name: bindingForm.display_name.trim() || null,
        },
      );
      setBindingForm({ corp_scope: '', staffdeck_user_id: '', wecom_userid: '', display_name: '' });
      notify.success('临时身份绑定已保存');
      await load();
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '保存临时绑定失败');
    } finally {
      setSavingBinding(false);
    }
  }

  async function removeBinding(id: string) {
    try {
      await api.delete(
        `/api/enterprise/wecom-oa/temp-identity-bindings/${encodeURIComponent(id)}`
        + `?tenant_id=${encodeURIComponent(TENANT_ID)}`,
      );
      notify.success('已删除临时绑定');
      await load();
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '删除临时绑定失败');
    }
  }

  return (
    <div
      className="mt-[16px] flex flex-col gap-[16px] border-t border-[#eef0f4] pt-[16px]"
      data-testid="wecom-oa-admin-panel"
    >
      <div className="flex min-w-0 flex-col gap-[4px]">
        <span className="text-[13px] font-semibold text-[#18181a]">企微 OA 审批（代发）</span>
        <span className="text-[12px] leading-[1.6] text-[#858b9c]">
          配置自建应用凭证（与智能机器人凭证隔离）及 Mode B 临时身份绑定。密钥保存后不再回显。
          企业范围填上面的企业 ID。企微 userid 在企业微信管理后台打开通讯录，进入成员详情，账号一栏就是 userid。
        </span>
      </div>

      <div className="flex flex-col gap-[12px] rounded-[10px] bg-[#fafbfc] p-[16px]">
        <span className="text-[12px] font-medium text-[#464c5e]">OA 自建应用凭证</span>
        {credential && (
          <span className="text-[12px] text-[#858b9c]">
            已配置企业 ID：{credential.corp_id}
            {credential.has_secret ? ' · Secret 已存' : ''}
            {credential.has_callback_token ? ' · 回调 Token 已存' : ''}
          </span>
        )}
        {(
          [
            ['corp_id', '企业 ID', false],
            ['secret', 'OA Secret', true],
            ['agentid', '应用 AgentId（可选）', false],
            ['callback_token', '回调 Token（可选）', true],
            ['callback_aes_key', '回调 EncodingAESKey（可选）', true],
          ] as const
        ).map(([key, label, secret]) => (
          <label key={key} className="flex flex-col gap-[6px] text-[12px] text-[#464c5e]">
            {label}
            <Input
              type={secret ? 'password' : 'text'}
              value={credForm[key]}
              autoComplete="off"
              onChange={(event) => setCredForm((prev) => ({ ...prev, [key]: event.target.value }))}
              className="h-8 rounded-[10px] text-[12px]"
              placeholder={secret && credential ? '重新输入以更新密钥' : ''}
            />
          </label>
        ))}
        <div className="flex justify-end">
          <UIButton
            onClick={() => void saveCredential()}
            disabled={savingCred || loading}
            className={PRIMARY_BUTTON_CLASS}
          >
            保存 OA 凭证
          </UIButton>
        </div>
      </div>

      <div className="flex flex-col gap-[12px] rounded-[10px] bg-[#fafbfc] p-[16px]">
        <span className="text-[12px] font-medium text-[#464c5e]">临时身份绑定（Mode B）</span>
        <div className="grid gap-[8px] sm:grid-cols-2">
          <label className="flex flex-col gap-[6px] text-[12px] text-[#464c5e]">
            企业 ID（与上面保存的企业 ID 相同）
            <Input
              value={bindingForm.corp_scope}
              autoComplete="off"
              placeholder="ww 开头的企业 ID"
              onChange={(event) =>
                setBindingForm((prev) => ({ ...prev, corp_scope: event.target.value }))
              }
              className="h-8 rounded-[10px] text-[12px]"
            />
          </label>
          <label className="flex flex-col gap-[6px] text-[12px] text-[#464c5e]">
            StaffDeck 用户
            <select
              aria-label="StaffDeck 用户"
              value={bindingForm.staffdeck_user_id}
              onChange={(event) =>
                setBindingForm((prev) => ({ ...prev, staffdeck_user_id: event.target.value }))
              }
              className="h-8 rounded-[10px] border border-[#e3e7f1] bg-white px-2 text-[12px]"
            >
              <option value="">{users.length ? '选择用户' : '暂无可选用户'}</option>
              {users.map((user) => (
                <option key={user.id} value={user.id}>
                  {user.display_name || user.username}（{user.username}）
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-[6px] text-[12px] text-[#464c5e]">
            企微 userid
            <Input
              value={bindingForm.wecom_userid}
              autoComplete="off"
              placeholder="通讯录成员详情里的账号"
              onChange={(event) =>
                setBindingForm((prev) => ({ ...prev, wecom_userid: event.target.value }))
              }
              className="h-8 rounded-[10px] text-[12px]"
            />
          </label>
          <label className="flex flex-col gap-[6px] text-[12px] text-[#464c5e]">
            显示名（可选）
            <Input
              value={bindingForm.display_name}
              autoComplete="off"
              onChange={(event) =>
                setBindingForm((prev) => ({ ...prev, display_name: event.target.value }))
              }
              className="h-8 rounded-[10px] text-[12px]"
            />
          </label>
        </div>
        <div className="flex justify-end">
          <UIButton
            onClick={() => void saveBinding()}
            disabled={savingBinding || loading}
            className={PRIMARY_BUTTON_CLASS}
          >
            新增/更新绑定
          </UIButton>
        </div>
        {bindings.length > 0 && (
          <ul className="m-0 flex list-none flex-col gap-[8px] p-0">
            {bindings.map((row) => (
              <li
                key={row.id}
                className="flex flex-wrap items-center justify-between gap-[8px] rounded-[8px] border border-[#eef0f4] bg-white px-[12px] py-[8px] text-[12px] text-[#464c5e]"
              >
                <span>
                  {row.display_name || row.staffdeck_user_id}
                  {' · '}
                  {row.staffdeck_user_id}
                  {' → '}
                  {row.wecom_userid}
                  <span className="text-[#a0a6b8]">（{row.corp_scope}）</span>
                </span>
                <UIButton
                  variant="outline"
                  className={OUTLINE_BUTTON_CLASS}
                  onClick={() => void removeBinding(row.id)}
                >
                  删除
                </UIButton>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
