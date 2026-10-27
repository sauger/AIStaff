import { useCallback, useEffect, useState } from 'react';

import { Input } from '@/components/ui';
import { Button as UIButton } from '@/components/ui/button';
import { notify } from '@/components/ui/app-toast';

import { api, TENANT_ID } from '../../api/client';

const PRIMARY_BUTTON_CLASS =
  'h-8 gap-1 rounded-[10px] bg-[#18181a] px-5 text-[12px] font-normal text-white hover:bg-[#303030]';

type ApprovalConfig = {
  template_id: string;
  scenario_key: string;
  applicant_mode: string;
  mode_a_userid?: string | null;
  field_mappings: Array<Record<string, unknown>>;
  compliance_enabled?: boolean;
  compliance_policy_ref?: string | null;
  display_name?: string | null;
};

type Props = {
  agentId: string;
};

const EMPTY: ApprovalConfig = {
  template_id: '',
  scenario_key: 'default',
  applicant_mode: 'fixed',
  mode_a_userid: '',
  field_mappings: [],
  compliance_enabled: false,
  compliance_policy_ref: '',
  display_name: '',
};

export default function WecomOaAgentConfigPanel({ agentId }: Props) {
  const [form, setForm] = useState<ApprovalConfig>(EMPTY);
  const [mappingsText, setMappingsText] = useState('[]');
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    if (!agentId) return;
    setLoading(true);
    try {
      const res = await api.get<{ config: ApprovalConfig | null }>(
        `/api/enterprise/wecom-oa/agents/${encodeURIComponent(agentId)}/approval-config`
        + `?tenant_id=${encodeURIComponent(TENANT_ID)}&scenario_key=default`,
      );
      if (res.config) {
        setForm({ ...EMPTY, ...res.config });
        setMappingsText(JSON.stringify(res.config.field_mappings || [], null, 2));
      } else {
        setForm(EMPTY);
        setMappingsText('[]');
      }
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '加载审批配置失败');
    } finally {
      setLoading(false);
    }
  }, [agentId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function save() {
    let fieldMappings: Array<Record<string, unknown>> = [];
    try {
      const parsed = JSON.parse(mappingsText || '[]');
      if (!Array.isArray(parsed)) throw new Error('字段映射须为 JSON 数组');
      fieldMappings = parsed;
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '字段映射 JSON 无效');
      return;
    }
    if (!form.template_id.trim()) {
      notify.error('请填写审批模板 ID');
      return;
    }
    if (form.applicant_mode === 'fixed' && !String(form.mode_a_userid || '').trim()) {
      notify.error('Mode A 需填写固定申请人 userid');
      return;
    }
    setSaving(true);
    try {
      await api.put(
        `/api/enterprise/wecom-oa/agents/${encodeURIComponent(agentId)}/approval-config`
        + `?tenant_id=${encodeURIComponent(TENANT_ID)}`,
        {
          template_id: form.template_id.trim(),
          scenario_key: form.scenario_key.trim() || 'default',
          applicant_mode: form.applicant_mode,
          mode_a_userid: String(form.mode_a_userid || '').trim() || null,
          field_mappings: fieldMappings,
          compliance_enabled: Boolean(form.compliance_enabled),
          compliance_policy_ref: String(form.compliance_policy_ref || '').trim() || null,
          display_name: String(form.display_name || '').trim() || null,
        },
      );
      notify.success('企微审批场景配置已保存');
      await load();
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '保存审批配置失败');
    } finally {
      setSaving(false);
    }
  }

  return (
    <div
      className="mt-[16px] flex flex-col gap-[12px] rounded-[10px] border border-[#eef0f4] bg-[#fafbfc] p-[16px]"
      data-testid="wecom-oa-agent-config"
    >
      <div>
        <strong className="text-[13px] text-[#18181a]">企微审批代发配置</strong>
        <p className="m-0 mt-[4px] text-[12px] text-[#858b9c]">
          绑定模板 ID、申请人模式与字段映射；确认摘要后才会向企微提交。
        </p>
      </div>
      <label className="flex flex-col gap-[6px] text-[12px] text-[#464c5e]">
        显示名（可选）
        <Input
          value={form.display_name || ''}
          onChange={(e) => setForm((p) => ({ ...p, display_name: e.target.value }))}
          className="h-8 rounded-[10px] text-[12px]"
          disabled={loading}
        />
      </label>
      <label className="flex flex-col gap-[6px] text-[12px] text-[#464c5e]">
        模板 ID
        <Input
          value={form.template_id}
          onChange={(e) => setForm((p) => ({ ...p, template_id: e.target.value }))}
          className="h-8 rounded-[10px] text-[12px]"
          disabled={loading}
        />
      </label>
      <label className="flex flex-col gap-[6px] text-[12px] text-[#464c5e]">
        场景键
        <Input
          value={form.scenario_key}
          onChange={(e) => setForm((p) => ({ ...p, scenario_key: e.target.value }))}
          className="h-8 rounded-[10px] text-[12px]"
          disabled={loading}
        />
      </label>
      <label className="flex flex-col gap-[6px] text-[12px] text-[#464c5e]">
        申请人模式
        <select
          value={form.applicant_mode}
          onChange={(e) => setForm((p) => ({ ...p, applicant_mode: e.target.value }))}
          className="h-8 rounded-[10px] border border-[#e3e7f1] bg-white px-2 text-[12px]"
          disabled={loading}
        >
          <option value="fixed">Mode A：固定员工 userid</option>
          <option value="chat_partner">Mode B：聊天对方代发</option>
        </select>
      </label>
      {form.applicant_mode === 'fixed' && (
        <label className="flex flex-col gap-[6px] text-[12px] text-[#464c5e]">
          Mode A 固定 userid
          <Input
            value={form.mode_a_userid || ''}
            onChange={(e) => setForm((p) => ({ ...p, mode_a_userid: e.target.value }))}
            className="h-8 rounded-[10px] text-[12px]"
            disabled={loading}
          />
        </label>
      )}
      <label className="flex flex-col gap-[6px] text-[12px] text-[#464c5e]">
        字段映射（JSON 数组）
        <textarea
          value={mappingsText}
          onChange={(e) => setMappingsText(e.target.value)}
          rows={5}
          className="rounded-[10px] border border-[#e3e7f1] bg-white px-2 py-1 font-mono text-[11px]"
          disabled={loading}
        />
      </label>
      <label className="flex items-center gap-[8px] text-[12px] text-[#464c5e]">
        <input
          type="checkbox"
          checked={Boolean(form.compliance_enabled)}
          onChange={(e) => setForm((p) => ({ ...p, compliance_enabled: e.target.checked }))}
          disabled={loading}
        />
        提交前启用合规检查
      </label>
      {form.compliance_enabled && (
        <label className="flex flex-col gap-[6px] text-[12px] text-[#464c5e]">
          合规策略引用
          <Input
            value={form.compliance_policy_ref || ''}
            onChange={(e) => setForm((p) => ({ ...p, compliance_policy_ref: e.target.value }))}
            className="h-8 rounded-[10px] text-[12px]"
            disabled={loading}
          />
        </label>
      )}
      <div className="flex justify-end">
        <UIButton onClick={() => void save()} disabled={saving || loading} className={PRIMARY_BUTTON_CLASS}>
          保存审批配置
        </UIButton>
      </div>
    </div>
  );
}
