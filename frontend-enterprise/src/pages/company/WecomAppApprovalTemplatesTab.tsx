import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';

import { Input } from '@/components/ui';
import { Button as UIButton } from '@/components/ui/button';
import { notify } from '@/components/ui/app-toast';

import { api, TENANT_ID } from '../../api/client';
import { EnterpriseRoute } from '../../enums/routes';

const PRIMARY_BUTTON_CLASS =
  'h-8 gap-1 rounded-[10px] bg-[#18181a] px-5 text-[12px] font-normal text-white hover:bg-[#303030]';
const OUTLINE_BUTTON_CLASS =
  'h-8 gap-1 rounded-[10px] border-[#e3e7f1] px-5 text-[12px] font-normal text-[#464c5e] hover:bg-[#f6f6f6] hover:text-[#18181a]';

export type WecomAppOption = {
  id: string;
  name: string;
  is_default: boolean;
  status: string;
};

type ApprovalTemplate = {
  template_id: string;
  name: string;
  control_count?: number | null;
  source?: string;
};

type TemplateAssignment = {
  agent_id: string;
  agent_name: string;
  template_id: string;
  scenario_key: string;
};

type TemplatePayload = {
  app_id: string;
  app_name: string;
  templates: ApprovalTemplate[];
  error?: string | null;
  notice?: string | null;
  assignments: TemplateAssignment[];
};

type AgentOption = {
  id: string;
  name: string;
};

function tenantQuery(): string {
  return `tenant_id=${encodeURIComponent(TENANT_ID)}`;
}

function pickAppId(apps: WecomAppOption[], preferredAppId: string | null): string {
  if (preferredAppId && apps.some((app) => app.id === preferredAppId)) return preferredAppId;
  const fallback = apps.find((app) => app.is_default && app.status !== 'disabled')
    || apps.find((app) => app.status !== 'disabled')
    || apps[0];
  return fallback?.id || '';
}

function asAgents(value: unknown): AgentOption[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    if (!item || typeof item !== 'object') return [];
    const row = item as { id?: unknown; name?: unknown };
    const id = String(row.id || '').trim();
    const name = String(row.name || '').trim();
    if (!id || !name) return [];
    return [{ id, name }];
  });
}

export default function WecomAppApprovalTemplatesTab({
  apps,
  preferredAppId,
}: {
  apps: WecomAppOption[];
  preferredAppId: string | null;
}) {
  const [appId, setAppId] = useState(() => pickAppId(apps, preferredAppId));
  const [loading, setLoading] = useState(false);
  const [payload, setPayload] = useState<TemplatePayload | null>(null);
  const [agents, setAgents] = useState<AgentOption[]>([]);
  const [extraTemplates, setExtraTemplates] = useState<ApprovalTemplate[]>([]);
  const [openTemplateId, setOpenTemplateId] = useState<string | null>(null);
  const [selectedAgentIds, setSelectedAgentIds] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [lookupId, setLookupId] = useState('');
  const [lookupError, setLookupError] = useState<string | null>(null);
  const [lookingUp, setLookingUp] = useState(false);
  const requestRef = useRef(0);

  useEffect(() => {
    if (preferredAppId && apps.some((app) => app.id === preferredAppId)) {
      setAppId(preferredAppId);
      return;
    }
    setAppId((current) => (
      current && apps.some((app) => app.id === current) ? current : pickAppId(apps, null)
    ));
  }, [apps, preferredAppId]);

  const load = useCallback(async (nextAppId: string) => {
    const requestId = requestRef.current + 1;
    requestRef.current = requestId;
    if (!nextAppId) {
      setPayload(null);
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const [templates, agentRows] = await Promise.all([
        api.get<TemplatePayload>(
          `/api/enterprise/wecom-apps/${encodeURIComponent(nextAppId)}/approval-templates?${tenantQuery()}`,
        ),
        api.get<unknown>(`/api/enterprise/agents?${tenantQuery()}`),
      ]);
      if (requestRef.current !== requestId) return;
      setPayload(templates);
      setAgents(asAgents(agentRows));
    } catch (error) {
      if (requestRef.current !== requestId) return;
      setPayload({
        app_id: nextAppId,
        app_name: '',
        templates: [],
        assignments: [],
        error: error instanceof Error ? error.message : '读取审批模板失败',
      });
    } finally {
      if (requestRef.current === requestId) setLoading(false);
    }
  }, []);

  useEffect(() => {
    setExtraTemplates([]);
    setOpenTemplateId(null);
    setLookupError(null);
    void load(appId);
  }, [appId, load]);

  const templates = useMemo(() => {
    const rows = new Map<string, ApprovalTemplate>();
    for (const row of payload?.templates || []) rows.set(row.template_id, row);
    for (const row of extraTemplates) {
      if (!rows.has(row.template_id)) rows.set(row.template_id, row);
    }
    return [...rows.values()];
  }, [extraTemplates, payload]);

  function assignees(templateId: string): TemplateAssignment[] {
    return (payload?.assignments || []).filter((row) => row.template_id === templateId);
  }

  function toggleAgent(agentId: string) {
    setSelectedAgentIds((current) => (
      current.includes(agentId)
        ? current.filter((id) => id !== agentId)
        : [...current, agentId]
    ));
  }

  async function saveAssignment(template: ApprovalTemplate) {
    if (!appId) return;
    if (selectedAgentIds.length === 0) {
      notify.error('请选择数字员工');
      return;
    }
    setSaving(true);
    try {
      await api.post(
        `/api/enterprise/wecom-apps/${encodeURIComponent(appId)}/approval-templates/assignments?${tenantQuery()}`,
        {
          template_id: template.template_id,
          template_name: template.name,
          agent_ids: selectedAgentIds,
          scenario_key: 'default',
        },
      );
      notify.success('已写入员工的企微审批代发配置');
      setOpenTemplateId(null);
      setSelectedAgentIds([]);
      await load(appId);
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '分配审批模板失败');
    } finally {
      setSaving(false);
    }
  }

  async function lookupTemplate() {
    const templateId = lookupId.trim();
    if (!appId || !templateId) {
      setLookupError('请填写模板 ID');
      return;
    }
    setLookingUp(true);
    setLookupError(null);
    try {
      const result = await api.post<{ template: ApprovalTemplate | null; error?: string | null }>(
        `/api/enterprise/wecom-apps/${encodeURIComponent(appId)}/approval-templates/lookup?${tenantQuery()}`,
        { template_id: templateId },
      );
      if (!result.template) {
        setLookupError(result.error || '未找到该审批模板');
        return;
      }
      setExtraTemplates((current) => (
        current.some((row) => row.template_id === result.template?.template_id)
          ? current
          : [...current, result.template as ApprovalTemplate]
      ));
      setLookupId('');
      notify.success('已核对审批模板');
    } catch (error) {
      setLookupError(error instanceof Error ? error.message : '核对审批模板失败');
    } finally {
      setLookingUp(false);
    }
  }

  const error = payload?.error || null;
  const notice = payload?.notice || null;

  return (
    <section className="flex flex-col gap-[16px]" data-testid="wecom-approval-templates">
      <div className="flex flex-col gap-[4px]">
        <span className="text-[14px] font-semibold text-[#18181a]">可见审批模板</span>
        <span className="max-w-[720px] text-[12px] leading-[1.6] text-[#858b9c]">
          用该自建应用的凭证读取企微侧可见模板。一个模板可以分给多名数字员工；每名员工的默认场景只保留一个模板 ID，已填写的申请人 userid 会保留。
        </span>
      </div>

      {apps.length === 0 ? (
        <p className="m-0 text-[12px] text-[#858b9c]" data-testid="wecom-template-empty">
          还没有企微自建应用，无法读取审批模板。
        </p>
      ) : (
        <label className="flex max-w-[360px] flex-col gap-[6px] text-[12px] text-[#464c5e]">
          当前应用
          <select
            aria-label="当前应用"
            value={appId}
            onChange={(event) => setAppId(event.target.value)}
            className="h-8 rounded-[10px] border border-[#e3e7f1] bg-white px-2 text-[12px]"
          >
            {apps.map((app) => (
              <option key={app.id} value={app.id}>
                {app.name}
                {app.is_default ? '（默认）' : ''}
                {app.status === 'disabled' ? '（已停用）' : ''}
              </option>
            ))}
          </select>
        </label>
      )}

      {loading ? <span className="text-[12px] text-[#858b9c]">正在读取审批模板</span> : null}

      {!loading && apps.length > 0 && templates.length === 0 ? (
        <p className="m-0 text-[12px] leading-[1.6] text-[#858b9c]" data-testid="wecom-template-empty">
          {error || notice || '该应用没有可列出的审批模板'}
        </p>
      ) : null}

      {!loading && templates.length > 0 && error ? (
        <p className="m-0 text-[12px] leading-[1.6] text-[#b45309]" data-testid="wecom-template-error">
          {error}
        </p>
      ) : null}

      {!loading && templates.length > 0 && notice ? (
        <p className="m-0 text-[12px] leading-[1.6] text-[#858b9c]">{notice}</p>
      ) : null}

      {templates.length > 0 ? (
        <ul className="m-0 flex list-none flex-col gap-[10px] p-0">
          {templates.map((template) => {
            const assigned = assignees(template.template_id);
            const open = openTemplateId === template.template_id;
            return (
              <li
                key={template.template_id}
                className="flex flex-col gap-[10px] rounded-[12px] border border-[#eef0f4] bg-white px-[16px] py-[12px]"
                data-testid="wecom-template-row"
              >
                <div className="flex flex-wrap items-center gap-[8px] text-[13px] text-[#18181a]">
                  <strong className="font-medium" data-i18n-ignore>{template.name}</strong>
                  {template.control_count != null ? (
                    <span className="text-[11px] text-[#858b9c]">{`${template.control_count} 个控件`}</span>
                  ) : null}
                </div>
                <div className="text-[12px] text-[#464c5e]">
                  模板 ID
                  {' '}
                  <span data-i18n-ignore>{template.template_id}</span>
                </div>
                <div className="text-[12px] text-[#464c5e]">
                  {assigned.length === 0 ? '尚未分配' : '已分配：'}
                  {assigned.length > 0 ? (
                    <span data-i18n-ignore>
                      {assigned.map((row) => row.agent_name).join('、')}
                    </span>
                  ) : null}
                </div>
                <div>
                  <UIButton
                    variant="outline"
                    className={OUTLINE_BUTTON_CLASS}
                    onClick={() => {
                      setOpenTemplateId(open ? null : template.template_id);
                      setSelectedAgentIds([]);
                    }}
                  >
                    分配给数字员工
                  </UIButton>
                </div>
                {open ? (
                  <div className="flex flex-col gap-[8px] rounded-[10px] bg-[#fafbfc] p-[12px]">
                    {agents.length === 0 ? (
                      <p className="m-0 text-[12px] text-[#858b9c]">租户内还没有数字员工。</p>
                    ) : (
                      <div className="flex flex-col gap-[6px]">
                        {agents.map((agent) => (
                          <label key={agent.id} className="flex items-center gap-[8px] text-[12px] text-[#18181a]">
                            <input
                              type="checkbox"
                              checked={selectedAgentIds.includes(agent.id)}
                              onChange={() => toggleAgent(agent.id)}
                            />
                            <span data-i18n-ignore>{agent.name}</span>
                          </label>
                        ))}
                      </div>
                    )}
                    <div className="flex flex-wrap items-center gap-[8px]">
                      <UIButton
                        className={PRIMARY_BUTTON_CLASS}
                        disabled={saving || agents.length === 0}
                        onClick={() => void saveAssignment(template)}
                      >
                        保存分配
                      </UIButton>
                      <Link className="text-[12px] text-[#464c5e] underline" to={EnterpriseRoute.Agents}>
                        打开数字员工档案核对
                      </Link>
                    </div>
                  </div>
                ) : null}
              </li>
            );
          })}
        </ul>
      ) : null}

      {apps.length > 0 ? (
        <div className="flex flex-col gap-[8px] rounded-[12px] border border-dashed border-[#e3e7f1] p-[12px]">
          <span className="text-[12px] font-medium text-[#464c5e]">用模板 ID 核对</span>
          <p className="m-0 text-[12px] leading-[1.6] text-[#858b9c]">
            近期没有审批单时，目录里可能看不到模板。粘贴模板 ID 后，用当前应用凭证向企微核对名称。
          </p>
          <div className="flex flex-wrap items-center gap-[8px]">
            <Input
              aria-label="模板 ID"
              value={lookupId}
              onChange={(event) => setLookupId(event.target.value)}
              className="h-8 max-w-[360px] rounded-[10px] text-[12px]"
            />
            <UIButton
              variant="outline"
              className={OUTLINE_BUTTON_CLASS}
              disabled={lookingUp || !appId}
              onClick={() => void lookupTemplate()}
            >
              核对
            </UIButton>
          </div>
          {lookupError ? (
            <p className="m-0 text-[12px] text-[#b45309]" data-testid="wecom-template-lookup-error">
              {lookupError}
            </p>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
