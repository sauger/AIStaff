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
  template_name?: string | null;
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

function withoutCatalogError(value: string | null | undefined): string | null {
  const text = String(value || '').trim();
  if (!text || /gettemplatelist/i.test(text)) return null;
  return text;
}

function sourceLabel(source: string | undefined): string | null {
  if (source === 'saved') return '已保存';
  if (source === 'approval') return '近 30 天审批';
  if (source === 'assignment') return '已分配';
  if (source === 'lookup') return '已核对';
  return null;
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
  const [verified, setVerified] = useState<ApprovalTemplate | null>(null);
  const [openTemplateId, setOpenTemplateId] = useState<string | null>(null);
  const [selectedAgentIds, setSelectedAgentIds] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [savingTemplateId, setSavingTemplateId] = useState<string | null>(null);
  const [removingTemplateId, setRemovingTemplateId] = useState<string | null>(null);
  const [removingAssignmentKey, setRemovingAssignmentKey] = useState<string | null>(null);
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
    setVerified(null);
    setOpenTemplateId(null);
    setLookupError(null);
    void load(appId);
  }, [appId, load]);

  const templates = useMemo(() => {
    const rows = new Map<string, ApprovalTemplate>();
    for (const row of payload?.templates || []) rows.set(row.template_id, row);
    for (const assignment of payload?.assignments || []) {
      if (rows.has(assignment.template_id)) continue;
      const name = String(assignment.template_name || '').trim() || assignment.template_id;
      rows.set(assignment.template_id, {
        template_id: assignment.template_id,
        name,
        source: 'assignment',
      });
    }
    if (verified && !rows.has(verified.template_id)) rows.set(verified.template_id, verified);
    return [...rows.values()];
  }, [payload, verified]);

  const verifiedRow = verified
    ? templates.find((row) => row.template_id === verified.template_id) || verified
    : null;
  const listRows = templates.filter((row) => row.template_id !== verifiedRow?.template_id);

  function assignees(templateId: string): TemplateAssignment[] {
    return (payload?.assignments || []).filter((row) => row.template_id === templateId);
  }

  function templateTitle(templateId: string): string {
    return templates.find((row) => row.template_id === templateId)?.name
      || (payload?.assignments || []).find((row) => row.template_id === templateId)?.template_name
      || templateId;
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

  async function unassign(templateId: string, agentId: string) {
    if (!appId) return;
    const key = `${templateId}:${agentId}`;
    setRemovingAssignmentKey(key);
    try {
      await api.post(
        `/api/enterprise/wecom-apps/${encodeURIComponent(appId)}/approval-templates/assignments/remove?${tenantQuery()}`,
        {
          template_id: templateId,
          agent_ids: [agentId],
          scenario_key: 'default',
        },
      );
      notify.success('已取消分配');
      await load(appId);
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '取消分配失败');
    } finally {
      setRemovingAssignmentKey(null);
    }
  }

  async function saveTemplate(template: ApprovalTemplate) {
    if (!appId) return;
    setSavingTemplateId(template.template_id);
    setLookupError(null);
    try {
      const result = await api.post<{ template: ApprovalTemplate | null; error?: string | null }>(
        `/api/enterprise/wecom-apps/${encodeURIComponent(appId)}/approval-templates/saved?${tenantQuery()}`,
        { template_id: template.template_id },
      );
      if (!result.template) {
        const message = result.error || '保存审批模板失败';
        if (verified?.template_id === template.template_id) setLookupError(message);
        else notify.error(message);
        return;
      }
      notify.success('已保存审批模板');
      setVerified(null);
      setLookupId('');
      await load(appId);
    } catch (error) {
      const message = error instanceof Error ? error.message : '保存审批模板失败';
      if (verified?.template_id === template.template_id) setLookupError(message);
      else notify.error(message);
    } finally {
      setSavingTemplateId(null);
    }
  }

  async function forgetTemplate(template: ApprovalTemplate) {
    if (!appId) return;
    setRemovingTemplateId(template.template_id);
    try {
      await api.delete(
        `/api/enterprise/wecom-apps/${encodeURIComponent(appId)}/approval-templates/saved?${tenantQuery()}&template_id=${encodeURIComponent(template.template_id)}`,
      );
      notify.success('已取消保存');
      if (verified?.template_id === template.template_id) setVerified(null);
      await load(appId);
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '取消保存失败');
    } finally {
      setRemovingTemplateId(null);
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
      setVerified({ ...result.template, source: result.template.source || 'lookup' });
      setLookupId('');
      notify.success('已核对审批模板');
    } catch (error) {
      setLookupError(error instanceof Error ? error.message : '核对审批模板失败');
    } finally {
      setLookingUp(false);
    }
  }

  const error = withoutCatalogError(payload?.error);
  const notice = withoutCatalogError(payload?.notice);

  function renderCard(template: ApprovalTemplate) {
    const assigned = assignees(template.template_id);
    const open = openTemplateId === template.template_id;
    const label = sourceLabel(template.source);
    const saved = template.source === 'saved';
    return (
      <li
        key={template.template_id}
        className="flex flex-col gap-[10px] rounded-[12px] border border-[#eef0f4] bg-white px-[16px] py-[12px]"
        data-testid="wecom-template-row"
      >
        <div className="flex flex-wrap items-center gap-[8px] text-[13px] text-[#18181a]">
          <strong className="font-medium" data-i18n-ignore>{template.name}</strong>
          {label ? (
            <span className="rounded-[6px] bg-[#f4f5f7] px-[6px] py-[1px] text-[11px] text-[#858b9c]">
              {label}
            </span>
          ) : null}
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
        <div className="flex flex-wrap items-center gap-[8px]">
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
          {saved ? (
            <UIButton
              variant="outline"
              className={OUTLINE_BUTTON_CLASS}
              disabled={removingTemplateId === template.template_id}
              onClick={() => void forgetTemplate(template)}
            >
              取消保存
            </UIButton>
          ) : (
            <UIButton
              className={PRIMARY_BUTTON_CLASS}
              disabled={savingTemplateId === template.template_id}
              onClick={() => void saveTemplate(template)}
            >
              保存模板
            </UIButton>
          )}
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
  }

  return (
    <section className="flex flex-col gap-[16px]" data-testid="wecom-approval-templates">
      <div className="flex flex-col gap-[4px]">
        <span className="text-[14px] font-semibold text-[#18181a]">审批模板</span>
        <span className="max-w-[720px] text-[12px] leading-[1.6] text-[#858b9c]">
          管理要代发的办公「审批」模板。已保存的模板和已分配给数字员工的模板会一直显示；近 30 天审批单里出现过的模板只作辅助发现。一个模板可以分给多名数字员工，每名员工的默认场景只保留一个模板 ID。
        </span>
      </div>

      {apps.length === 0 ? (
        <p className="m-0 text-[12px] text-[#858b9c]" data-testid="wecom-template-empty">
          还没有企微自建应用，无法管理审批模板。
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

      {apps.length > 0 ? (
        <div className="flex flex-col gap-[8px] rounded-[12px] border border-dashed border-[#e3e7f1] p-[12px]">
          <span className="text-[12px] font-medium text-[#464c5e]">用模板 ID 核对</span>
          <p className="m-0 text-[12px] leading-[1.6] text-[#858b9c]">
            粘贴办公「审批」系统应用里的模板 ID，先核对名称，再点「保存模板」。保存后刷新仍在列表里。自建应用「审批接口」编辑页上的 ID 不能用于核对或代发。
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

      {!loading && verifiedRow ? (
        <div className="flex flex-col gap-[8px]" data-testid="wecom-template-verified">
          <p className="m-0 text-[12px] leading-[1.6] text-[#464c5e]">
            {verifiedRow.source === 'saved'
              ? '已保存在列表中'
              : '核对成功。点「保存模板」后写入本系统，下次打开仍可分配给数字员工。'}
          </p>
          <ul className="m-0 flex list-none flex-col gap-[10px] p-0">
            {renderCard(verifiedRow)}
          </ul>
        </div>
      ) : null}

      {!loading && error ? (
        <p className="m-0 text-[12px] leading-[1.6] text-[#b45309]" data-testid="wecom-template-error">
          {error}
        </p>
      ) : null}

      {!loading && notice ? (
        <p className="m-0 text-[12px] leading-[1.6] text-[#858b9c]" data-testid="wecom-template-notice">
          {notice}
        </p>
      ) : null}

      {!loading && apps.length > 0 && listRows.length === 0 && !verifiedRow ? (
        <p className="m-0 text-[12px] leading-[1.6] text-[#858b9c]" data-testid="wecom-template-empty">
          还没有已保存或已分配的模板。在上方填写办公「审批」里的模板 ID，核对成功后点「保存模板」。
        </p>
      ) : null}

      {listRows.length > 0 ? (
        <ul className="m-0 flex list-none flex-col gap-[10px] p-0">
          {listRows.map((template) => renderCard(template))}
        </ul>
      ) : null}

      {!loading && apps.length > 0 ? (
        <div
          className="flex flex-col gap-[8px] rounded-[12px] border border-[#eef0f4] bg-white px-[16px] py-[12px]"
          data-testid="wecom-template-assignments"
        >
          <span className="text-[13px] font-medium text-[#18181a]">已分配</span>
          {(payload?.assignments || []).length === 0 ? (
            <p className="m-0 text-[12px] text-[#858b9c]">还没有分配给数字员工的模板。</p>
          ) : (
            <ul className="m-0 flex list-none flex-col gap-[8px] p-0">
              {(payload?.assignments || []).map((row) => {
                const key = `${row.template_id}:${row.agent_id}:${row.scenario_key}`;
                return (
                  <li key={key} className="flex flex-wrap items-center gap-[8px] text-[12px] text-[#464c5e]">
                    <span data-i18n-ignore>{templateTitle(row.template_id)}</span>
                    <span data-i18n-ignore>{row.template_id}</span>
                    <span data-i18n-ignore>{row.agent_name}</span>
                    <UIButton
                      variant="outline"
                      className={OUTLINE_BUTTON_CLASS}
                      aria-label={`取消分配 ${row.agent_name}`}
                      disabled={removingAssignmentKey === `${row.template_id}:${row.agent_id}`}
                      onClick={() => void unassign(row.template_id, row.agent_id)}
                    >
                      取消分配
                    </UIButton>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      ) : null}
    </section>
  );
}
