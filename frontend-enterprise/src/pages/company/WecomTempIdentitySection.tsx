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

export default function WecomTempIdentitySection({ currentUser }: Props) {
  const isAdmin = isEnterpriseAdmin(currentUser);
  const [bindings, setBindings] = useState<TempBinding[]>([]);
  const [users, setUsers] = useState<StaffUser[]>([]);
  const [loading, setLoading] = useState(false);
  const [savingBinding, setSavingBinding] = useState(false);
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
      const [bindRes, userRes] = await Promise.all([
        api.get<{ bindings: TempBinding[] }>(
          `/api/enterprise/wecom-oa/temp-identity-bindings?tenant_id=${encodeURIComponent(TENANT_ID)}`,
        ),
        api.get<StaffUser[]>(`/api/auth/users?tenant_id=${encodeURIComponent(TENANT_ID)}`),
      ]);
      setBindings(bindRes.bindings || []);
      setUsers(Array.isArray(userRes) ? userRes : []);
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '加载临时身份绑定失败');
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
    <section
      className="mt-[28px] flex flex-col gap-[12px] border-t border-[#eef0f4] pt-[20px]"
      data-testid="wecom-temp-identity-bindings"
    >
      <div className="flex min-w-0 flex-col gap-[4px]">
        <span className="text-[13px] font-semibold text-[#18181a]">临时身份绑定（Mode B）</span>
        <span className="text-[12px] leading-[1.6] text-[#858b9c]">
          这不是应用凭证。临时身份绑定仍用于 Mode B，规则与接口不变。P0 不改 Mode A/B。
          企微 userid 在企业微信管理后台打开通讯录，进入成员详情，账号一栏就是 userid。
        </span>
      </div>
      <div className="grid gap-[8px] sm:grid-cols-2">
        <label className="flex flex-col gap-[6px] text-[12px] text-[#464c5e]">
          企业 ID
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
    </section>
  );
}
