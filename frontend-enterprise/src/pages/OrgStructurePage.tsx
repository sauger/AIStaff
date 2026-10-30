import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';

import AppHeader from '@/components/AppHeader';
import { ConfirmDialog } from '@/components/ConfirmDialog';
import {
  Dialog,
  DialogContent,
  DialogTitle,
  Input,
} from '@/components/ui';
import { Button as UIButton } from '@/components/ui/button';
import { notify } from '@/components/ui/app-toast';
import { cn } from '@/lib/utils';
import { DIALOG_PRIMARY_BUTTON_CLASS, OUTLINE_ACTION_BUTTON_CLASS } from '@/lib/enterprise-ui';

import { api, ApiError, TENANT_ID } from '../api/client';
import type { EnterpriseAuthUser } from '../auth';
import type {
  EnterprisePersonListResponse,
  EnterprisePersonRead,
  OrgAgentListResponse,
  OrgAgentMemberRead,
  OrgAgentRead,
  OrgDepartmentPeopleResponse,
  OrgDepartmentRead,
  OrgHeadRead,
  OrgPersonRead,
  OrgSettingsRead,
  OrgTreeAgentRead,
  OrgTreePersonRead,
  OrgTreeResponse,
} from '../types';

type OrgStructurePageProps = {
  currentUser?: EnterpriseAuthUser;
  onLogout?: () => void;
  embedded?: boolean;
};

type DepartmentDraft = {
  id?: string;
  name: string;
  parentId: string;
  sortOrder: string;
};

type PersonDraft = {
  id?: string;
  name: string;
  employeeNo: string;
  phone: string;
  email: string;
  status: 'active' | 'left';
  primaryUserId: string;
  addToDepartment: boolean;
  position: string;
  validFrom: string;
  validTo: string;
};

type MoveDraft = {
  affiliation: OrgTreePersonRead;
  departmentId: string;
  position: string;
  validFrom: string;
  validTo: string;
};

const EMPTY_DEPARTMENT: DepartmentDraft = { name: '', parentId: '', sortOrder: '0' };

const SELECT_CLASS =
  'h-8 w-full rounded-lg border border-input bg-transparent px-2.5 text-sm text-foreground';
const TREE_ACTION_CLASS =
  'rounded-[6px] px-[6px] py-[2px] text-[10px] text-[#858b9c] transition-colors hover:bg-[#eef1f6] hover:text-[#18181a]';

function today(): string {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(new Date());
}

function apiErrorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return '操作失败';
}

function tenureLabel(validFrom: string, validTo?: string | null): string {
  return `${validFrom} – ${validTo || '至今'}`;
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="flex min-w-0 flex-1 flex-col gap-[4px] text-[12px] text-[#464c5e]">
      <span>{label}</span>
      {children}
    </label>
  );
}

function childrenOf(departments: OrgDepartmentRead[], parentId: string | null): OrgDepartmentRead[] {
  return departments
    .filter((item) => (item.parent_id || null) === parentId)
    .sort((left, right) => left.sort_order - right.sort_order || left.name.localeCompare(right.name, 'zh'));
}

function descendantIds(departments: OrgDepartmentRead[], departmentId: string): Set<string> {
  const ids = new Set<string>();
  const walk = (parentId: string) => {
    childrenOf(departments, parentId).forEach((child) => {
      ids.add(child.id);
      walk(child.id);
    });
  };
  walk(departmentId);
  return ids;
}

function personName(person: EnterprisePersonRead | undefined, fallback: string): string {
  return person?.name || fallback;
}

export default function OrgStructurePage({
  currentUser,
  onLogout,
  embedded = false,
}: OrgStructurePageProps = {}) {
  const navigate = useNavigate();
  const [enterpriseName, setEnterpriseName] = useState('');
  const [departments, setDepartments] = useState<OrgDepartmentRead[]>([]);
  const [people, setPeople] = useState<EnterprisePersonListResponse['people']>([]);
  const [orgAgents, setOrgAgents] = useState<OrgAgentRead[]>([]);
  const [tree, setTree] = useState<OrgTreeResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [savingName, setSavingName] = useState(false);
  const [departmentDialog, setDepartmentDialog] = useState<DepartmentDraft | null>(null);
  const [savingDepartment, setSavingDepartment] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<OrgDepartmentRead | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [asOf, setAsOf] = useState(today);
  const [detail, setDetail] = useState<OrgDepartmentPeopleResponse | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [memberPersonId, setMemberPersonId] = useState('');
  const [memberPosition, setMemberPosition] = useState('');
  const [memberFrom, setMemberFrom] = useState(today);
  const [memberTo, setMemberTo] = useState('');
  const [headPersonId, setHeadPersonId] = useState('');
  const [headFrom, setHeadFrom] = useState(today);
  const [headTo, setHeadTo] = useState('');
  const [agentToAdd, setAgentToAdd] = useState('');
  const [agentSaving, setAgentSaving] = useState(false);
  const [personDialog, setPersonDialog] = useState<PersonDraft | null>(null);
  const [savingPerson, setSavingPerson] = useState(false);
  const [moveDraft, setMoveDraft] = useState<MoveDraft | null>(null);
  const [moving, setMoving] = useState(false);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const blockedParents = useMemo(() => {
    if (!departmentDialog?.id) return new Set<string>();
    const blocked = descendantIds(departments, departmentDialog.id);
    blocked.add(departmentDialog.id);
    return blocked;
  }, [departments, departmentDialog?.id]);

  const activePeople = useMemo(
    () => people.filter((person) => person.status !== 'left'),
    [people],
  );

  useEffect(() => {
    void loadAll();
  }, []);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    void loadDetail(selectedId, asOf);
  }, [selectedId]);

  async function loadAll() {
    setLoading(true);
    try {
      const [settings, treeResult, peopleResult, agentResult] = await Promise.all([
        api.get<OrgSettingsRead>(`/api/enterprise/org/settings?tenant_id=${encodeURIComponent(TENANT_ID)}`),
        api.get<OrgTreeResponse>(`/api/enterprise/org/tree?tenant_id=${encodeURIComponent(TENANT_ID)}`),
        api.get<EnterprisePersonListResponse>(
          `/api/enterprise/org/people?tenant_id=${encodeURIComponent(TENANT_ID)}`,
        ),
        api.get<OrgAgentListResponse>(
          `/api/enterprise/org/agents?tenant_id=${encodeURIComponent(TENANT_ID)}`,
        ),
      ]);
      setEnterpriseName(settings.enterprise_name);
      setTree(treeResult);
      setDepartments(treeResult.departments || []);
      setPeople(peopleResult.people || []);
      setOrgAgents(agentResult.agents || []);
      setExpanded((current) => {
        if (current.size) return current;
        return new Set((treeResult.departments || []).map((item) => item.id));
      });
    } catch (error) {
      notify.error(apiErrorMessage(error));
    } finally {
      setLoading(false);
    }
  }

  async function loadDetail(departmentId: string, day: string) {
    setDetailLoading(true);
    try {
      const result = await api.get<OrgDepartmentPeopleResponse>(
        `/api/enterprise/org/departments/${encodeURIComponent(departmentId)}/people?tenant_id=${encodeURIComponent(TENANT_ID)}&as_of=${encodeURIComponent(day)}`,
      );
      setDetail(result);
    } catch (error) {
      notify.error(apiErrorMessage(error));
    } finally {
      setDetailLoading(false);
    }
  }

  async function saveEnterpriseName() {
    const name = enterpriseName.trim();
    if (!name) {
      notify.error('请填写企业名称');
      return;
    }
    setSavingName(true);
    try {
      const saved = await api.patch<OrgSettingsRead>('/api/enterprise/org/settings', {
        tenant_id: TENANT_ID,
        enterprise_name: name,
      });
      setEnterpriseName(saved.enterprise_name);
      notify.success('已保存企业名称');
    } catch (error) {
      notify.error(apiErrorMessage(error));
    } finally {
      setSavingName(false);
    }
  }

  function openDepartmentDialog(parentId = '', department?: OrgDepartmentRead) {
    setDepartmentDialog(
      department
        ? {
          id: department.id,
          name: department.name,
          parentId: department.parent_id || '',
          sortOrder: String(department.sort_order),
        }
        : { name: '', parentId, sortOrder: '0' },
    );
  }

  async function saveDepartment() {
    if (!departmentDialog) return;
    const name = departmentDialog.name.trim();
    if (!name) {
      notify.error('请填写部门名称');
      return;
    }
    const sortOrder = Number(departmentDialog.sortOrder || '0');
    if (!Number.isFinite(sortOrder)) {
      notify.error('排序必须是数字');
      return;
    }
    const payload = {
      tenant_id: TENANT_ID,
      name,
      parent_id: departmentDialog.parentId || null,
      sort_order: sortOrder,
    };
    const editedId = departmentDialog.id;
    setSavingDepartment(true);
    try {
      if (editedId) {
        await api.patch(`/api/enterprise/org/departments/${encodeURIComponent(editedId)}`, payload);
      } else {
        const created = await api.post<OrgDepartmentRead>('/api/enterprise/org/departments', payload);
        setSelectedId(created.id);
        setExpanded((current) => new Set(current).add(created.id));
      }
      notify.success('已保存部门');
      setDepartmentDialog(null);
      await loadAll();
      if (editedId && editedId === selectedId) await loadDetail(editedId, asOf);
    } catch (error) {
      notify.error(apiErrorMessage(error));
    } finally {
      setSavingDepartment(false);
    }
  }

  async function confirmDeleteDepartment() {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await api.delete(
        `/api/enterprise/org/departments/${encodeURIComponent(deleteTarget.id)}?tenant_id=${encodeURIComponent(TENANT_ID)}`,
      );
      notify.success('已删除部门');
      if (selectedId === deleteTarget.id) {
        setSelectedId(null);
        setDetail(null);
      }
      setDeleteTarget(null);
      await loadAll();
    } catch (error) {
      notify.error(apiErrorMessage(error));
    } finally {
      setDeleting(false);
    }
  }

  async function queryPeople() {
    if (!selectedId) return;
    await loadDetail(selectedId, asOf);
  }

  async function addMember() {
    if (!selectedId) return;
    if (!memberPersonId) {
      notify.error('请选择企业人员');
      return;
    }
    try {
      await api.post('/api/enterprise/org/affiliations', {
        tenant_id: TENANT_ID,
        department_id: selectedId,
        person_id: memberPersonId,
        position_title: memberPosition.trim() || null,
        valid_from: memberFrom || null,
        valid_to: memberTo || null,
      });
      notify.success('已添加成员');
      setMemberPersonId('');
      setMemberPosition('');
      setMemberTo('');
      await Promise.all([loadDetail(selectedId, asOf), loadAll()]);
    } catch (error) {
      notify.error(apiErrorMessage(error));
    }
  }

  async function saveAffiliation(row: OrgPersonRead, validFrom: string, validTo: string, position: string) {
    if (!selectedId) return;
    try {
      // 不传 person_id：换人校验只发生在字段变化时，收尾离职人员任职需要放行。
      await api.patch(`/api/enterprise/org/affiliations/${encodeURIComponent(row.affiliation_id)}`, {
        tenant_id: TENANT_ID,
        position_title: position.trim() || null,
        valid_from: validFrom,
        valid_to: validTo || null,
        clear_valid_to: !validTo,
      });
      notify.success('已更新任职');
      await Promise.all([loadDetail(selectedId, asOf), loadAll()]);
    } catch (error) {
      notify.error(apiErrorMessage(error));
    }
  }

  async function removeAffiliation(affiliationId: string) {
    if (!selectedId) return;
    try {
      await api.delete(
        `/api/enterprise/org/affiliations/${encodeURIComponent(affiliationId)}?tenant_id=${encodeURIComponent(TENANT_ID)}`,
      );
      notify.success('已删除任职');
      await Promise.all([loadDetail(selectedId, asOf), loadAll()]);
    } catch (error) {
      notify.error(apiErrorMessage(error));
    }
  }

  async function addHead() {
    if (!selectedId) return;
    if (!headPersonId) {
      notify.error('请选择负责人');
      return;
    }
    try {
      await api.post('/api/enterprise/org/heads', {
        tenant_id: TENANT_ID,
        department_id: selectedId,
        person_id: headPersonId,
        valid_from: headFrom || null,
        valid_to: headTo || null,
      });
      notify.success('已设置负责人');
      setHeadPersonId('');
      setHeadTo('');
      await loadDetail(selectedId, asOf);
    } catch (error) {
      notify.error(apiErrorMessage(error));
    }
  }

  async function saveHead(row: OrgHeadRead, validFrom: string, validTo: string) {
    if (!selectedId) return;
    try {
      await api.patch(`/api/enterprise/org/heads/${encodeURIComponent(row.head_id)}`, {
        tenant_id: TENANT_ID,
        valid_from: validFrom,
        valid_to: validTo || null,
        clear_valid_to: !validTo,
      });
      notify.success('已更新负责人');
      await loadDetail(selectedId, asOf);
    } catch (error) {
      notify.error(apiErrorMessage(error));
    }
  }

  async function removeHead(headId: string) {
    if (!selectedId) return;
    try {
      await api.delete(
        `/api/enterprise/org/heads/${encodeURIComponent(headId)}?tenant_id=${encodeURIComponent(TENANT_ID)}`,
      );
      notify.success('已删除负责人');
      await loadDetail(selectedId, asOf);
    } catch (error) {
      notify.error(apiErrorMessage(error));
    }
  }

  function openCreatePerson() {
    setPersonDialog({
      name: '',
      employeeNo: '',
      phone: '',
      email: '',
      status: 'active',
      primaryUserId: '',
      addToDepartment: Boolean(selectedId),
      position: '',
      validFrom: today(),
      validTo: '',
    });
  }

  function openEditPerson(person: OrgTreePersonRead) {
    const source = people.find((item) => item.id === person.person_id);
    // 编辑只改主档字段；岗位/任职属于任职记录，由"移动"或在部门详情里维护。
    setPersonDialog({
      id: person.person_id,
      name: source?.name || person.name,
      employeeNo: source?.employee_no || '',
      phone: source?.phone || '',
      email: source?.email || '',
      status: source?.status === 'left' ? 'left' : 'active',
      primaryUserId: source?.primary_user_id || '',
      addToDepartment: false,
      position: '',
      validFrom: '',
      validTo: '',
    });
  }

  async function savePerson() {
    if (!personDialog) return;
    if (!personDialog.name.trim()) {
      notify.error('请填写姓名');
      return;
    }
    setSavingPerson(true);
    try {
      const payload = {
        tenant_id: TENANT_ID,
        name: personDialog.name.trim(),
        employee_no: personDialog.employeeNo.trim() || null,
        phone: personDialog.phone.trim() || null,
        email: personDialog.email.trim() || null,
        status: personDialog.status,
        primary_user_id: personDialog.primaryUserId || null,
      };
      const saved = personDialog.id
        ? await api.patch<EnterprisePersonRead>(
          `/api/enterprise/org/people/${encodeURIComponent(personDialog.id)}`,
          payload,
        )
        : await api.post<EnterprisePersonRead>('/api/enterprise/org/people', payload);
      if (!personDialog.id && personDialog.addToDepartment && selectedId) {
        await api.post('/api/enterprise/org/affiliations', {
          tenant_id: TENANT_ID,
          department_id: selectedId,
          person_id: saved.id,
          position_title: personDialog.position.trim() || null,
          valid_from: personDialog.validFrom || null,
          valid_to: personDialog.validTo || null,
        });
      }
      notify.success(personDialog.id ? '企业人员已更新' : '企业人员已创建');
      setPersonDialog(null);
      await Promise.all([loadAll(), selectedId ? loadDetail(selectedId, asOf) : Promise.resolve()]);
    } catch (error) {
      notify.error(apiErrorMessage(error));
    } finally {
      setSavingPerson(false);
    }
  }

  function openMovePerson(person: OrgTreePersonRead) {
    setMoveDraft({
      affiliation: person,
      departmentId: '',
      position: person.position_title || '',
      validFrom: person.valid_from,
      validTo: person.valid_to || '',
    });
  }

  async function saveMovePerson() {
    if (!moveDraft || !moveDraft.departmentId) {
      notify.error('请选择目标部门');
      return;
    }
    setMoving(true);
    try {
      // 专用 move 端点：结束原部门任职并新建目标部门任职，保留原部门历史。
      await api.post(
        `/api/enterprise/org/affiliations/${encodeURIComponent(moveDraft.affiliation.affiliation_id)}/move`,
        {
          tenant_id: TENANT_ID,
          department_id: moveDraft.departmentId,
          position_title: moveDraft.position.trim() || null,
          valid_from: moveDraft.validFrom || null,
          valid_to: moveDraft.validTo || null,
        },
      );
      notify.success('已移动部门');
      setMoveDraft(null);
      await Promise.all([loadAll(), selectedId ? loadDetail(selectedId, asOf) : Promise.resolve()]);
    } catch (error) {
      notify.error(apiErrorMessage(error));
    } finally {
      setMoving(false);
    }
  }

  async function assignAgent(agentId: string, departmentIds: string[], primaryId: string | null) {
    setAgentSaving(true);
    try {
      await api.put(`/api/enterprise/org/agents/${encodeURIComponent(agentId)}/departments`, {
        tenant_id: TENANT_ID,
        department_ids: departmentIds,
        primary_department_id: primaryId,
      });
      await Promise.all([loadDetail(selectedId || '', asOf), loadAll()]);
    } catch (error) {
      notify.error(apiErrorMessage(error));
    } finally {
      setAgentSaving(false);
    }
  }

  async function addAgentToDepartment() {
    if (!selectedId || !agentToAdd) return;
    const agent = orgAgents.find((item) => item.agent_id === agentToAdd);
    if (!agent) return;
    if (agent.departments.some((item) => item.department_id === selectedId)) {
      notify.error('该数字员工已在此部门');
      return;
    }
    const departmentIds = [...agent.departments.map((item) => item.department_id), selectedId];
    const primaryId = agent.departments.find((item) => item.is_primary)?.department_id || selectedId;
    await assignAgent(agent.agent_id, departmentIds, primaryId);
    setAgentToAdd('');
  }

  async function removeAgentFromDepartment(agent: OrgAgentMemberRead) {
    const source = orgAgents.find((item) => item.agent_id === agent.agent_id);
    const remaining = (source?.departments || []).filter((item) => item.department_id !== selectedId);
    const primaryId = remaining.find((item) => item.is_primary)?.department_id || remaining[0]?.department_id || null;
    await assignAgent(agent.agent_id, remaining.map((item) => item.department_id), primaryId);
  }

  const availableAgents = orgAgents.filter(
    (agent) => !agent.departments.some((item) => item.department_id === selectedId),
  );

  function toggleExpanded(departmentId: string) {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(departmentId)) next.delete(departmentId);
      else next.add(departmentId);
      return next;
    });
  }

  const content = (
    <>
      {!embedded ? (
        <AppHeader
          onLogout={onLogout}
          userName={currentUser?.username}
          title="组织架构"
          description="企业范围的部门树。企业人员来自企业管理主档，数字员工按业务归属单独展示。"
        />
      ) : null}

      <section className={cn('rounded-[16px] border border-[#eceef1] bg-white p-[16px]', !embedded && 'mt-[20px]')}>
        <div className="flex flex-wrap items-end justify-between gap-[12px]">
          <div className="flex min-w-[240px] flex-1 flex-col gap-[8px] sm:flex-row sm:items-end">
            <Field label="企业名称">
              <Input
                aria-label="企业名称"
                value={enterpriseName}
                onChange={(event) => setEnterpriseName(event.target.value)}
              />
            </Field>
            <UIButton
              className={cn(DIALOG_PRIMARY_BUTTON_CLASS, 'h-[34px]')}
              disabled={savingName}
              onClick={() => void saveEnterpriseName()}
            >
              保存企业名称
            </UIButton>
          </div>
          <div className="flex gap-[8px]">
            <UIButton
              variant="outline"
              className={cn(OUTLINE_ACTION_BUTTON_CLASS, 'h-[34px]')}
              onClick={() => openCreatePerson()}
            >
              新增人员
            </UIButton>
            <UIButton
              className={cn(DIALOG_PRIMARY_BUTTON_CLASS, 'h-[34px]')}
              onClick={() => openDepartmentDialog()}
            >
              新增部门
            </UIButton>
          </div>
        </div>
      </section>

      <div className="mt-[16px] grid gap-[16px] lg:grid-cols-[minmax(0,1.15fr)_minmax(0,0.85fr)]">
        <section className="rounded-[16px] border border-[#eceef1] bg-white p-[16px]">
          <div className="flex items-start justify-between gap-[12px]">
            <div>
              <h2 className="m-0 text-[14px] font-medium text-[#17191f]">组织架构</h2>
              <p className="m-0 mt-[4px] text-[11px] text-[#858b9c]">展开/收起部门，查看各部门人员与数字员工。</p>
            </div>
            {loading ? <span className="text-[11px] text-[#858b9c]">加载中…</span> : null}
          </div>

          <div className="mt-[12px]">
            {!loading && departments.length === 0 ? (
              <div className="rounded-[12px] border border-dashed border-[#dfe4ee] px-[16px] py-[28px] text-center">
                <p className="m-0 text-[13px] text-[#697085]">还没有部门。先新增一个根部门。</p>
                <UIButton
                  className={cn(DIALOG_PRIMARY_BUTTON_CLASS, 'mt-[12px] h-[32px]')}
                  onClick={() => openDepartmentDialog()}
                >
                  新增部门
                </UIButton>
              </div>
            ) : (
              <DepartmentTree
                departments={departments}
                people={tree?.people || []}
                agents={tree?.agents || []}
                selectedId={selectedId}
                expanded={expanded}
                onToggle={toggleExpanded}
                onSelect={setSelectedId}
                onEdit={(department) => openDepartmentDialog('', department)}
                onAddChild={(department) => openDepartmentDialog(department.id)}
                onDelete={setDeleteTarget}
                onPersonClick={() => navigate('/enterprise/company/people')}
                onEditPerson={openEditPerson}
                onMovePerson={openMovePerson}
                onAgentClick={() => navigate('/enterprise/company/agents')}
              />
            )}
          </div>
        </section>

        <section className="rounded-[16px] border border-[#eceef1] bg-white p-[16px]" data-testid="org-people">
          {!selectedId ? (
            <p className="m-0 text-[13px] text-[#697085]">
              选择左侧部门后，可以查看企业人员、负责人和数字员工。
            </p>
          ) : (
            <div className="flex flex-col gap-[16px]">
              <div className="flex flex-col gap-[8px] sm:flex-row sm:items-end">
                <Field label="查询日期">
                  <Input
                    aria-label="查询日期"
                    type="date"
                    value={asOf}
                    onChange={(event) => setAsOf(event.target.value)}
                  />
                </Field>
                <UIButton
                  variant="outline"
                  className={cn(OUTLINE_ACTION_BUTTON_CLASS, 'h-[34px]')}
                  disabled={detailLoading}
                  onClick={() => void queryPeople()}
                >
                  查询该部门
                </UIButton>
              </div>

              <div>
                <h2 className="m-0 text-[14px] font-medium text-[#17191f]">企业人员</h2>
                {detailLoading && !detail ? (
                  <p className="mt-[8px] text-[13px] text-[#697085]">正在加载组织架构</p>
                ) : null}
                {detail && (detail.people || []).length === 0 ? (
                  <p className="mt-[8px] text-[13px] text-[#697085]">当前没有符合该日期的人员。</p>
                ) : null}
                <ul className="m-0 mt-[8px] flex list-none flex-col gap-[8px] p-0">
                  {(detail?.people || []).map((person) => (
                    <li key={person.affiliation_id} className="text-[13px] text-[#17191f]">
                      <span data-i18n-ignore>{person.person_name}</span>
                      <span className="text-[#697085]">
                        {person.position_title ? ` · ${person.position_title}` : ''}
                        {' · '}
                        <span data-i18n-ignore>{tenureLabel(person.valid_from, person.valid_to)}</span>
                      </span>
                    </li>
                  ))}
                </ul>
              </div>

              <div className="flex flex-col gap-[8px]">
                <h2 className="m-0 text-[14px] font-medium text-[#17191f]">添加已有人员</h2>
                {activePeople.length === 0 ? (
                  <p className="m-0 text-[12px] text-[#697085]">
                    还没有在职企业人员。
                    <button
                      type="button"
                      className="ml-[4px] text-[#1a71ff] underline"
                      onClick={openCreatePerson}
                    >
                      新增企业人员
                    </button>
                  </p>
                ) : (
                  <>
                    <select
                      aria-label="企业人员"
                      className={SELECT_CLASS}
                      value={memberPersonId}
                      onChange={(event) => setMemberPersonId(event.target.value)}
                    >
                      <option value="">请选择企业人员</option>
                      {activePeople.map((person) => (
                        <option key={person.id} value={person.id}>
                          {person.name}{person.employee_no ? ` · ${person.employee_no}` : ''}
                        </option>
                      ))}
                    </select>
                    <Field label="岗位">
                      <Input
                        aria-label="岗位"
                        value={memberPosition}
                        onChange={(event) => setMemberPosition(event.target.value)}
                        placeholder="例如：销售经理"
                      />
                    </Field>
                    <div className="flex flex-col gap-[8px] sm:flex-row">
                      <Field label="任职开始">
                        <Input aria-label="任职开始" type="date" value={memberFrom} onChange={(event) => setMemberFrom(event.target.value)} />
                      </Field>
                      <Field label="任职结束">
                        <Input aria-label="成员任职结束" type="date" value={memberTo} onChange={(event) => setMemberTo(event.target.value)} />
                      </Field>
                    </div>
                    <p className="m-0 text-[12px] text-[#697085]">留空表示至今</p>
                    <UIButton className={cn(DIALOG_PRIMARY_BUTTON_CLASS, 'h-[34px] w-fit')} onClick={() => void addMember()}>
                      添加成员
                    </UIButton>
                  </>
                )}
              </div>

              <TenureList
                title="全部任职"
                rows={(detail?.affiliations || []).map((row) => ({
                  id: row.affiliation_id,
                  label: row.person_name,
                  position: row.position_title || '',
                  validFrom: row.valid_from,
                  validTo: row.valid_to || '',
                  onSave: (validFrom, validTo, position) => saveAffiliation(row, validFrom, validTo, position),
                  onDelete: () => removeAffiliation(row.affiliation_id),
                }))}
              />

              <div>
                <h2 className="m-0 text-[14px] font-medium text-[#17191f]">数字员工</h2>
                <div className="mt-[8px] flex flex-col gap-[6px]">
                  {(detail?.agents || []).map((agent) => (
                    <div key={agent.agent_id} className="flex items-center justify-between gap-[8px] rounded-[10px] border border-[#eceef1] px-[10px] py-[8px]">
                      <div className="min-w-0">
                        <div className="flex items-center gap-[6px]">
                          <span className="rounded-[5px] bg-[#eef1fb] px-[6px] py-[2px] text-[10px] text-[#5470b8]">AI</span>
                          <span className="truncate text-[13px] text-[#17191f]" data-i18n-ignore>{agent.name}</span>
                          <span className="rounded-full bg-[#f2f3f7] px-[7px] py-[2px] text-[10px] text-[#697085]">
                            {agent.is_primary ? '主责' : '共享'}
                          </span>
                        </div>
                        <div className="mt-[2px] text-[11px] text-[#697085]">
                          {agent.status === 'active' ? '在线' : '已下线'}
                          {agent.primary_owner_name ? ` · 负责人：${agent.primary_owner_name}` : ''}
                        </div>
                      </div>
                      <button
                        type="button"
                        className="shrink-0 text-[11px] text-[#d20b0b]"
                        disabled={agentSaving}
                        onClick={() => void removeAgentFromDepartment(agent)}
                      >
                        移出
                      </button>
                    </div>
                  ))}
                  {!detail?.agents?.length ? (
                    <p className="m-0 text-[12px] text-[#697085]">该部门还没有数字员工。</p>
                  ) : null}
                </div>
                <div className="mt-[8px] flex gap-[8px]">
                  <select
                    aria-label="添加数字员工"
                    className={SELECT_CLASS}
                    value={agentToAdd}
                    onChange={(event) => setAgentToAdd(event.target.value)}
                  >
                    <option value="">选择数字员工</option>
                    {availableAgents.map((agent) => (
                      <option key={agent.agent_id} value={agent.agent_id}>{agent.name}</option>
                    ))}
                  </select>
                  <UIButton
                    variant="outline"
                    className={cn(OUTLINE_ACTION_BUTTON_CLASS, 'h-[34px] shrink-0')}
                    disabled={!agentToAdd || agentSaving}
                    onClick={() => void addAgentToDepartment()}
                  >
                    添加数字员工
                  </UIButton>
                </div>
              </div>

              <div>
                <h2 className="m-0 text-[14px] font-medium text-[#17191f]">负责人</h2>
                <p className="mt-[8px] text-[13px] text-[#17191f]">
                  当前负责人：
                  {detail?.current_head ? (
                    <span data-i18n-ignore>{detail.current_head.person_name}</span>
                  ) : (
                    <span>暂无负责人</span>
                  )}
                </p>
                <div className="mt-[8px] flex flex-col gap-[8px]">
                  <select
                    aria-label="负责人人选"
                    className={SELECT_CLASS}
                    value={headPersonId}
                    onChange={(event) => setHeadPersonId(event.target.value)}
                  >
                    <option value="">请选择负责人</option>
                    {activePeople.map((person) => (
                      <option key={person.id} value={person.id}>{person.name}</option>
                    ))}
                  </select>
                  <div className="flex flex-col gap-[8px] sm:flex-row">
                    <Field label="负责人任职开始">
                      <Input aria-label="负责人任职开始" type="date" value={headFrom} onChange={(event) => setHeadFrom(event.target.value)} />
                    </Field>
                    <Field label="负责人任职结束">
                      <Input aria-label="负责人任职结束" type="date" value={headTo} onChange={(event) => setHeadTo(event.target.value)} />
                    </Field>
                  </div>
                  <UIButton className={cn(DIALOG_PRIMARY_BUTTON_CLASS, 'h-[34px] w-fit')} onClick={() => void addHead()}>
                    设为负责人
                  </UIButton>
                </div>
              </div>

              <TenureList
                title="负责人记录"
                rows={(detail?.heads || []).map((row) => ({
                  id: row.head_id,
                  label: row.person_name,
                  validFrom: row.valid_from,
                  validTo: row.valid_to || '',
                  onSave: (validFrom, validTo) => saveHead(row, validFrom, validTo),
                  onDelete: () => removeHead(row.head_id),
                }))}
              />
            </div>
          )}
        </section>
      </div>

      <Dialog open={Boolean(departmentDialog)} onOpenChange={(open) => !open && setDepartmentDialog(null)}>
        <DialogContent className="flex w-[calc(100%-2rem)] flex-col gap-[14px] sm:max-w-[460px]">
          <DialogTitle className="text-[14px] font-normal text-[#757f9c]">
            {departmentDialog?.id ? '编辑部门' : departmentDialog?.parentId ? '新增下级部门' : '新增部门'}
          </DialogTitle>
          <label className="flex flex-col gap-[4px] text-[12px] text-[#464c5e]">
            部门名称
            <Input
              aria-label="部门名称"
              value={departmentDialog?.name || ''}
              onChange={(event) => setDepartmentDialog((prev) => (prev ? { ...prev, name: event.target.value } : prev))}
              placeholder="例如：研发中心"
            />
          </label>
          <label className="flex flex-col gap-[4px] text-[12px] text-[#464c5e]">
            上级部门
            <select
              aria-label="上级部门"
              className={SELECT_CLASS}
              value={departmentDialog?.parentId || ''}
              onChange={(event) => setDepartmentDialog((prev) => (prev ? { ...prev, parentId: event.target.value } : prev))}
            >
              <option value="">根部门</option>
              {departments.filter((item) => !blockedParents.has(item.id)).map((item) => (
                <option key={item.id} value={item.id}>{item.name}</option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-[4px] text-[12px] text-[#464c5e]">
            排序
            <Input
              aria-label="排序"
              value={departmentDialog?.sortOrder || ''}
              onChange={(event) => setDepartmentDialog((prev) => (prev ? { ...prev, sortOrder: event.target.value } : prev))}
            />
          </label>
          <div className="flex justify-end gap-[8px]">
            <UIButton variant="outline" className="h-8 rounded-[10px] px-3 text-[12px]" onClick={() => setDepartmentDialog(null)}>
              取消
            </UIButton>
            <UIButton
              className="h-8 rounded-[10px] bg-[#18181a] px-3 text-[12px] text-white"
              disabled={savingDepartment}
              onClick={() => void saveDepartment()}
            >
              {savingDepartment ? '保存中…' : '保存'}
            </UIButton>
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={Boolean(personDialog)} onOpenChange={(open) => !open && setPersonDialog(null)}>
        <DialogContent className="flex max-h-[calc(100dvh-4rem)] w-[calc(100%-2rem)] flex-col gap-[14px] overflow-y-auto sm:max-w-[480px]">
          <DialogTitle className="text-[14px] font-normal text-[#757f9c]">
            {personDialog?.id ? '编辑企业人员' : '新增企业人员'}
          </DialogTitle>
          <label className="flex flex-col gap-[4px] text-[12px] text-[#464c5e]">
            姓名
            <Input
              value={personDialog?.name || ''}
              onChange={(event) => setPersonDialog((prev) => (prev ? { ...prev, name: event.target.value } : prev))}
              placeholder="例如：张三"
            />
          </label>
          <div className="grid grid-cols-2 gap-[10px] max-[560px]:grid-cols-1">
            <label className="flex flex-col gap-[4px] text-[12px] text-[#464c5e]">
              工号
              <Input
                value={personDialog?.employeeNo || ''}
                onChange={(event) => setPersonDialog((prev) => (prev ? { ...prev, employeeNo: event.target.value } : prev))}
                placeholder="选填"
              />
            </label>
            <label className="flex flex-col gap-[4px] text-[12px] text-[#464c5e]">
              状态
              <select
                className={SELECT_CLASS}
                value={personDialog?.status || 'active'}
                onChange={(event) => setPersonDialog((prev) => (prev ? { ...prev, status: event.target.value as 'active' | 'left' } : prev))}
              >
                <option value="active">在职</option>
                <option value="left">离职</option>
              </select>
            </label>
            <label className="flex flex-col gap-[4px] text-[12px] text-[#464c5e]">
              手机号
              <Input
                value={personDialog?.phone || ''}
                onChange={(event) => setPersonDialog((prev) => (prev ? { ...prev, phone: event.target.value } : prev))}
                placeholder="选填"
              />
            </label>
            <label className="flex flex-col gap-[4px] text-[12px] text-[#464c5e]">
              邮箱
              <Input
                value={personDialog?.email || ''}
                onChange={(event) => setPersonDialog((prev) => (prev ? { ...prev, email: event.target.value } : prev))}
                placeholder="选填"
              />
            </label>
          </div>
          {!personDialog?.id && selectedId ? (
            <label className="flex items-center gap-[8px] rounded-[8px] bg-[#fafbfc] px-[10px] py-[8px] text-[12px] text-[#464c5e]">
              <input
                type="checkbox"
                checked={personDialog?.addToDepartment ?? false}
                onChange={(event) => setPersonDialog((prev) => (prev ? { ...prev, addToDepartment: event.target.checked } : prev))}
              />
              同时加入当前部门
            </label>
          ) : null}
          {personDialog?.addToDepartment && !personDialog.id ? (
            <div className="flex flex-col gap-[8px]">
              <label className="flex flex-col gap-[4px] text-[12px] text-[#464c5e]">
                岗位
                <Input
                  value={personDialog.position}
                  onChange={(event) => setPersonDialog((prev) => (prev ? { ...prev, position: event.target.value } : prev))}
                  placeholder="例如：销售经理"
                />
              </label>
              <div className="flex flex-col gap-[8px] sm:flex-row">
                <Field label="任职开始">
                  <Input type="date" value={personDialog.validFrom} onChange={(event) => setPersonDialog((prev) => (prev ? { ...prev, validFrom: event.target.value } : prev))} />
                </Field>
                <Field label="任职结束">
                  <Input type="date" value={personDialog.validTo} onChange={(event) => setPersonDialog((prev) => (prev ? { ...prev, validTo: event.target.value } : prev))} />
                </Field>
              </div>
            </div>
          ) : null}
          <div className="flex justify-end gap-[8px]">
            <UIButton variant="outline" className="h-8 rounded-[10px] px-3 text-[12px]" onClick={() => setPersonDialog(null)}>
              取消
            </UIButton>
            <UIButton
              className="h-8 rounded-[10px] bg-[#18181a] px-3 text-[12px] text-white"
              disabled={savingPerson}
              onClick={() => void savePerson()}
            >
              {savingPerson ? '保存中…' : '保存'}
            </UIButton>
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={Boolean(moveDraft)} onOpenChange={(open) => !open && setMoveDraft(null)}>
        <DialogContent className="flex w-[calc(100%-2rem)] flex-col gap-[14px] sm:max-w-[460px]">
          <DialogTitle className="text-[14px] font-normal text-[#757f9c]">
            移动部门
          </DialogTitle>
          <p className="m-0 text-[12px] text-[#697085]">
            {moveDraft ? `将「${moveDraft.affiliation.name}」从当前部门移动到目标部门。` : ''}
          </p>
          <label className="flex flex-col gap-[4px] text-[12px] text-[#464c5e]">
            目标部门
            <select
              aria-label="目标部门"
              className={SELECT_CLASS}
              value={moveDraft?.departmentId || ''}
              onChange={(event) => setMoveDraft((prev) => (prev ? { ...prev, departmentId: event.target.value } : prev))}
            >
              <option value="">请选择目标部门</option>
              {departments.filter((item) => item.id !== moveDraft?.affiliation.department_id).map((item) => (
                <option key={item.id} value={item.id}>{item.name}</option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-[4px] text-[12px] text-[#464c5e]">
            岗位
            <Input
              value={moveDraft?.position || ''}
              onChange={(event) => setMoveDraft((prev) => (prev ? { ...prev, position: event.target.value } : prev))}
              placeholder="例如：销售经理"
            />
          </label>
          <div className="flex flex-col gap-[8px] sm:flex-row">
            <Field label="任职开始">
              <Input type="date" value={moveDraft?.validFrom || ''} onChange={(event) => setMoveDraft((prev) => (prev ? { ...prev, validFrom: event.target.value } : prev))} />
            </Field>
            <Field label="任职结束">
              <Input type="date" value={moveDraft?.validTo || ''} onChange={(event) => setMoveDraft((prev) => (prev ? { ...prev, validTo: event.target.value } : prev))} />
            </Field>
          </div>
          <div className="flex justify-end gap-[8px]">
            <UIButton variant="outline" className="h-8 rounded-[10px] px-3 text-[12px]" onClick={() => setMoveDraft(null)}>
              取消
            </UIButton>
            <UIButton
              className="h-8 rounded-[10px] bg-[#18181a] px-3 text-[12px] text-white"
              disabled={moving || !moveDraft?.departmentId}
              onClick={() => void saveMovePerson()}
            >
              {moving ? '移动中…' : '确认移动'}
            </UIButton>
          </div>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => {
          if (!open) setDeleteTarget(null);
        }}
        title="删除部门"
        description="删除前必须先移走该部门的人员、负责人和数字员工；有下级部门时也需要先移走。"
        loading={deleting}
        onConfirm={() => void confirmDeleteDepartment()}
      />
    </>
  );

  if (embedded) return <div>{content}</div>;

  return (
    <div className="min-h-full box-border px-[48px] pt-[32px] pb-[43px] max-[900px]:px-[16px]">
      {content}
    </div>
  );
}

function DepartmentTree({
  departments,
  people,
  agents,
  selectedId,
  expanded,
  onToggle,
  onSelect,
  onEdit,
  onAddChild,
  onDelete,
  onPersonClick,
  onEditPerson,
  onMovePerson,
  onAgentClick,
}: {
  departments: OrgDepartmentRead[];
  people: OrgTreePersonRead[];
  agents: OrgTreeAgentRead[];
  selectedId: string | null;
  expanded: Set<string>;
  onToggle: (id: string) => void;
  onSelect: (id: string) => void;
  onEdit: (department: OrgDepartmentRead) => void;
  onAddChild: (department: OrgDepartmentRead) => void;
  onDelete: (department: OrgDepartmentRead) => void;
  onPersonClick: (person: OrgTreePersonRead) => void;
  onEditPerson: (person: OrgTreePersonRead) => void;
  onMovePerson: (person: OrgTreePersonRead) => void;
  onAgentClick: (agent: OrgTreeAgentRead) => void;
}) {
  const personCount = new Map<string, number>();
  people.forEach((item) => personCount.set(item.person_id, (personCount.get(item.person_id) || 0) + 1));
  const agentCount = new Map<string, number>();
  agents.forEach((item) => agentCount.set(item.agent_id, (agentCount.get(item.agent_id) || 0) + 1));
  const roots = childrenOf(departments, null);
  return (
    <ul className="m-0 flex list-none flex-col gap-[2px] p-0" role="tree" aria-label="组织架构树">
      {roots.map((department) => (
        <DepartmentNode
          key={department.id}
          department={department}
          departments={departments}
          people={people}
          agents={agents}
          personCount={personCount}
          agentCount={agentCount}
          selectedId={selectedId}
          expanded={expanded}
          depth={0}
          onToggle={onToggle}
          onSelect={onSelect}
          onEdit={onEdit}
          onAddChild={onAddChild}
          onDelete={onDelete}
          onPersonClick={onPersonClick}
          onEditPerson={onEditPerson}
          onMovePerson={onMovePerson}
          onAgentClick={onAgentClick}
        />
      ))}
    </ul>
  );
}

function DepartmentNode({
  department,
  departments,
  people,
  agents,
  personCount,
  agentCount,
  selectedId,
  expanded,
  depth,
  onToggle,
  onSelect,
  onEdit,
  onAddChild,
  onDelete,
  onPersonClick,
  onEditPerson,
  onMovePerson,
  onAgentClick,
}: {
  department: OrgDepartmentRead;
  departments: OrgDepartmentRead[];
  people: OrgTreePersonRead[];
  agents: OrgTreeAgentRead[];
  personCount: Map<string, number>;
  agentCount: Map<string, number>;
  selectedId: string | null;
  expanded: Set<string>;
  depth: number;
  onToggle: (id: string) => void;
  onSelect: (id: string) => void;
  onEdit: (department: OrgDepartmentRead) => void;
  onAddChild: (department: OrgDepartmentRead) => void;
  onDelete: (department: OrgDepartmentRead) => void;
  onPersonClick: (person: OrgTreePersonRead) => void;
  onEditPerson: (person: OrgTreePersonRead) => void;
  onMovePerson: (person: OrgTreePersonRead) => void;
  onAgentClick: (agent: OrgTreeAgentRead) => void;
}) {
  const children = childrenOf(departments, department.id);
  const departmentPeople = people.filter((item) => item.department_id === department.id);
  const departmentAgents = agents.filter((item) => item.department_id === department.id);
  const hasChildren = children.length > 0 || departmentPeople.length > 0 || departmentAgents.length > 0;
  const isExpanded = expanded.has(department.id);
  return (
    <li role="treeitem" aria-expanded={hasChildren ? isExpanded : undefined} aria-selected={selectedId === department.id}>
      <div
        className={cn(
          'group flex items-center gap-[4px] rounded-[10px] border border-transparent px-[6px] py-[5px] transition-colors',
          selectedId === department.id && 'border-[#e3e7f1] bg-[#f6f7fa]',
          depth === 0 ? 'bg-[#fafbfc]' : 'bg-white',
        )}
      >
        <button
          type="button"
          aria-label={`${isExpanded ? '收起' : '展开'} ${department.name}`}
          onClick={() => onToggle(department.id)}
          className={cn('grid size-[18px] shrink-0 place-items-center text-[12px] text-[#858b9c]', !hasChildren && 'invisible')}
        >
          {isExpanded ? '▾' : '▸'}
        </button>
        <button
          type="button"
          className="flex min-w-0 flex-1 items-center gap-[6px] text-left"
          onClick={() => onSelect(department.id)}
        >
          <span className="grid size-[20px] shrink-0 place-items-center rounded-[6px] bg-[#eef1f6] text-[11px] text-[#697085]">部</span>
          <span className="truncate text-[13px] font-medium text-[#17191f]" data-i18n-ignore>{department.name}</span>
          <span className="shrink-0 text-[10px] text-[#858b9c]">
            员工 {new Set(departmentPeople.map((item) => item.person_id)).size} · 数字员工 {new Set(departmentAgents.map((item) => item.agent_id)).size}
          </span>
        </button>
        <div className="ml-auto flex shrink-0 items-center gap-[1px] opacity-60 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
          <button type="button" className={TREE_ACTION_CLASS} onClick={() => onAddChild(department)}>新增下级</button>
          <button type="button" className={TREE_ACTION_CLASS} onClick={() => onEdit(department)}>编辑</button>
          <button type="button" className={cn(TREE_ACTION_CLASS, 'text-[#d20b0b]')} onClick={() => onDelete(department)}>删除</button>
        </div>
      </div>

      {hasChildren && isExpanded ? (
        <div className={cn('relative ml-[16px] border-l border-[#e3e7f1] pl-[14px]', depth === 0 && 'mt-[2px]')}>
          {children.length ? (
            <ul className="m-0 flex list-none flex-col gap-[2px] p-0">
              {children.map((child) => (
                <DepartmentNode
                  key={child.id}
                  department={child}
                  departments={departments}
                  people={people}
                  agents={agents}
                  personCount={personCount}
                  agentCount={agentCount}
                  selectedId={selectedId}
                  expanded={expanded}
                  depth={depth + 1}
                  onToggle={onToggle}
                  onSelect={onSelect}
                  onEdit={onEdit}
                  onAddChild={onAddChild}
                  onDelete={onDelete}
                  onPersonClick={onPersonClick}
                  onEditPerson={onEditPerson}
                  onMovePerson={onMovePerson}
                  onAgentClick={onAgentClick}
                />
              ))}
            </ul>
          ) : null}
          {departmentPeople.length ? (
            <div className="mt-[3px] flex flex-col gap-[1px]">
              <span className="px-[6px] pb-[2px] text-[10px] font-medium text-[#858b9c]">企业人员</span>
              {departmentPeople.map((person) => (
                <div
                  key={person.affiliation_id}
                  className="group flex items-center gap-[5px] rounded-[8px] px-[6px] py-[3px] hover:bg-[#f6f7fa]"
                >
                  <span className="rounded-[5px] bg-[#eef4f2] px-[5px] py-[1px] text-[9px] text-[#0f766e]">员工</span>
                  <button
                    type="button"
                    onClick={() => onPersonClick(person)}
                    className="min-w-0 shrink truncate text-left text-[12px] text-[#464c5e] hover:text-[#18181a]"
                    data-i18n-ignore
                  >
                    {person.name}
                  </button>
                  {person.position_title ? <span className="truncate text-[10px] text-[#858b9c]" data-i18n-ignore>{person.position_title}</span> : null}
                  {(personCount.get(person.person_id) || 0) > 1 ? <span className="text-[9px] text-[#858b9c]">兼任</span> : null}
                  <div className="ml-auto flex gap-[1px] opacity-60 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
                    <button type="button" className={TREE_ACTION_CLASS} onClick={() => onEditPerson(person)}>编辑</button>
                    <button type="button" className={TREE_ACTION_CLASS} onClick={() => onMovePerson(person)}>移动</button>
                  </div>
                </div>
              ))}
            </div>
          ) : null}
          {departmentAgents.length ? (
            <div className="mt-[3px] flex flex-col gap-[1px]">
              <span className="px-[6px] pb-[2px] text-[10px] font-medium text-[#858b9c]">数字员工</span>
              {departmentAgents.map((agent) => (
                <button
                  key={`${agent.agent_id}-${agent.department_id}`}
                  type="button"
                  onClick={() => onAgentClick(agent)}
                  className="flex items-center gap-[5px] rounded-[8px] px-[6px] py-[3px] text-left hover:bg-[#f6f7fa]"
                >
                  <span className="rounded-[5px] bg-[#eef1fb] px-[5px] py-[1px] text-[9px] text-[#5470b8]">AI</span>
                  <span className="min-w-0 truncate text-[12px] text-[#464c5e]" data-i18n-ignore>{agent.name}</span>
                  <span className="rounded-full bg-[#f2f3f7] px-[6px] py-[1px] text-[9px] text-[#697085]">
                    {agent.is_primary ? '主责' : '共享'}
                  </span>
                  {agent.primary_owner_name ? <span className="truncate text-[10px] text-[#858b9c]" data-i18n-ignore>{agent.primary_owner_name}</span> : null}
                </button>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

function TenureList({
  title,
  rows,
}: {
  title: string;
  rows: {
    id: string;
    label: string;
    position?: string;
    validFrom: string;
    validTo: string;
    onSave: (validFrom: string, validTo: string, position: string) => Promise<void>;
    onDelete: () => Promise<void>;
  }[];
}) {
  if (rows.length === 0) return null;
  return (
    <div>
      <h2 className="m-0 text-[14px] font-medium text-[#17191f]">{title}</h2>
      <ul className="m-0 mt-[8px] flex list-none flex-col gap-[8px] p-0">
        {rows.map((row) => (
          <TenureRow key={row.id} row={row} />
        ))}
      </ul>
    </div>
  );
}

function TenureRow({
  row,
}: {
  row: {
    id: string;
    label: string;
    position?: string;
    validFrom: string;
    validTo: string;
    onSave: (validFrom: string, validTo: string, position: string) => Promise<void>;
    onDelete: () => Promise<void>;
  };
}) {
  const [validFrom, setValidFrom] = useState(row.validFrom);
  const [validTo, setValidTo] = useState(row.validTo);
  const [position, setPosition] = useState(row.position || '');
  useEffect(() => {
    setValidFrom(row.validFrom);
    setValidTo(row.validTo);
    setPosition(row.position || '');
  }, [row.validFrom, row.validTo, row.position, row.id]);
  return (
    <li className="flex flex-col gap-[8px] rounded-[12px] border border-[#eceef1] p-[8px]">
      <span className="text-[13px] text-[#17191f]" data-i18n-ignore>{row.label}</span>
      <div className="flex flex-col gap-[8px] sm:flex-row sm:items-end">
        {row.position !== undefined ? (
          <Field label="岗位">
            <Input aria-label="岗位" value={position} onChange={(event) => setPosition(event.target.value)} />
          </Field>
        ) : null}
        <Field label="任职开始">
          <Input aria-label="任职开始" type="date" value={validFrom} onChange={(event) => setValidFrom(event.target.value)} />
        </Field>
        <Field label="任职结束">
          <Input aria-label="任职结束" type="date" value={validTo} onChange={(event) => setValidTo(event.target.value)} />
        </Field>
        <UIButton
          variant="outline"
          className={cn(OUTLINE_ACTION_BUTTON_CLASS, 'h-[34px]')}
          onClick={() => void row.onSave(validFrom, validTo, position)}
        >
          保存任职
        </UIButton>
        <UIButton
          variant="outline"
          className={cn(OUTLINE_ACTION_BUTTON_CLASS, 'h-[34px]')}
          onClick={() => void row.onDelete()}
        >
          删除
        </UIButton>
      </div>
    </li>
  );
}
