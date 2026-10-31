import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { ConfirmDialog } from '@/components/ConfirmDialog';
import {
  Dialog,
  DialogContent,
  DialogTitle,
  Input,
  UnderlineTabs,
  type UnderlineTabItem,
} from '@/components/ui';
import { Button as UIButton } from '@/components/ui/button';
import { notify } from '@/components/ui/app-toast';
import { cn } from '@/lib/utils';

import { api, TENANT_ID } from '../../api/client';
import { isEnterpriseAdmin, type EnterpriseAuthUser } from '../../auth';
import { EnterpriseRoute } from '../../enums/routes';
import WecomDirectoryPicker, { type WecomDirectoryUser } from './WecomDirectoryPicker';
import type {
  EnterprisePersonListResponse,
  EnterprisePersonRead,
  OrgAgentListResponse,
  OrgAgentRead,
  OrgDepartmentListResponse,
  OrgDepartmentRead,
  OrgTreeResponse,
  UserRead,
} from '../../types';

type MemberFilter = 'all' | 'people' | 'agents';

type PersonDraft = {
  id?: string;
  name: string;
  employeeNo: string;
  phone: string;
  email: string;
  status: 'active' | 'left';
  primaryUserId: string;
  primaryUsername: string;
  wecomUserid: string;
};

type AgentDraft = {
  agent: OrgAgentRead;
  departmentIds: string[];
  primaryId: string;
};

const EMPTY_PERSON: PersonDraft = {
  name: '',
  employeeNo: '',
  phone: '',
  email: '',
  status: 'active',
  primaryUserId: '',
  primaryUsername: '',
  wecomUserid: '',
};

const INPUT_CLASS = 'h-8 rounded-[10px] text-[12px]';

function draftFromPerson(row: EnterprisePersonRead): PersonDraft {
  return {
    id: row.id,
    name: row.name,
    employeeNo: row.employee_no || '',
    phone: row.phone || '',
    email: row.email || '',
    status: row.status === 'left' ? 'left' : 'active',
    primaryUserId: row.primary_user_id || '',
    primaryUsername: row.primary_username || '',
    wecomUserid: '',
  };
}

function formatWecomPerson(person: WecomDirectoryUser): string {
  return [person.name, person.email, person.userid].filter(Boolean).join(' ');
}

function personStatus(status: string): { label: string; className: string } {
  return status === 'left'
    ? { label: '已离职', className: 'bg-[#f2f3f7] text-[#858b9c]' }
    : { label: '在职', className: 'bg-[#eef4f2] text-[#0f766e]' };
}

function agentStatus(status: string): { label: string; className: string } {
  return status === 'active'
    ? { label: '在线', className: 'bg-[#eef4f2] text-[#0f766e]' }
    : { label: '已下线', className: 'bg-[#f2f3f7] text-[#858b9c]' };
}

export default function CompanyMembersPage({ currentUser }: { currentUser?: EnterpriseAuthUser }) {
  const canPickWecom = isEnterpriseAdmin(currentUser);
  const navigate = useNavigate();
  const [people, setPeople] = useState<EnterprisePersonRead[]>([]);
  const [agents, setAgents] = useState<OrgAgentRead[]>([]);
  const [tree, setTree] = useState<OrgTreeResponse | null>(null);
  const [departments, setDepartments] = useState<OrgDepartmentRead[]>([]);
  const [users, setUsers] = useState<UserRead[]>([]);
  const [filter, setFilter] = useState<MemberFilter>('all');
  const [searchText, setSearchText] = useState('');
  const [loading, setLoading] = useState(false);
  const [personDraft, setPersonDraft] = useState<PersonDraft | null>(null);
  const [personSaving, setPersonSaving] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<EnterprisePersonRead | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [agentDraft, setAgentDraft] = useState<AgentDraft | null>(null);
  const [agentSaving, setAgentSaving] = useState(false);
  const [agentSearch, setAgentSearch] = useState('');
  const [wecomSelection, setWecomSelection] = useState<WecomDirectoryUser[]>([]);

  async function load() {
    setLoading(true);
    try {
      const [peopleResult, treeResult, agentResult, departmentResult, userRows] = await Promise.all([
        api.get<EnterprisePersonListResponse>(
          `/api/enterprise/org/people?tenant_id=${encodeURIComponent(TENANT_ID)}`,
        ),
        api.get<OrgTreeResponse>(
          `/api/enterprise/org/tree?tenant_id=${encodeURIComponent(TENANT_ID)}`,
        ),
        api.get<OrgAgentListResponse>(
          `/api/enterprise/org/agents?tenant_id=${encodeURIComponent(TENANT_ID)}`,
        ),
        api.get<OrgDepartmentListResponse>(
          `/api/enterprise/org/departments?tenant_id=${encodeURIComponent(TENANT_ID)}`,
        ),
        api.get<UserRead[]>(`/api/auth/users?tenant_id=${encodeURIComponent(TENANT_ID)}`),
      ]);
      setPeople(peopleResult.people || []);
      setTree(treeResult);
      setAgents(agentResult.agents || []);
      setDepartments(departmentResult.departments || []);
      setUsers(Array.isArray(userRows) ? userRows.filter((item) => item.source === 'web') : []);
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '加载成员失败');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  const departmentsByPerson = useMemo(() => {
    const map = new Map<string, { name: string; position?: string | null }[]>();
    const names = new Map(departments.map((item) => [item.id, item.name]));
    for (const item of tree?.people || []) {
      const rows = map.get(item.person_id) || [];
      rows.push({
        name: names.get(item.department_id) || item.department_id,
        position: item.position_title,
      });
      map.set(item.person_id, rows);
    }
    return map;
  }, [departments, tree]);

  const availableUsers = users.filter(
    (user) => !people.some((person) => person.primary_user_id === user.id),
  );

  const counts = {
    all: people.length + agents.length,
    people: people.length,
    agents: agents.length,
  };

  const filterItems: UnderlineTabItem<MemberFilter>[] = [
    { value: 'all', label: `全部 ${counts.all}` },
    { value: 'people', label: `企业人员 ${counts.people}` },
    { value: 'agents', label: `数字员工 ${counts.agents}` },
  ];

  const keyword = searchText.trim().toLowerCase();
  const personRows = people
    .filter((person) => filter === 'all' || filter === 'people')
    .filter((person) => {
      if (!keyword) return true;
      const departmentsText = (departmentsByPerson.get(person.id) || [])
        .map((item) => `${item.name} ${item.position || ''}`)
        .join(' ');
      return [
        person.name,
        person.employee_no || '',
        person.primary_username || '',
        person.primary_user_name || '',
        departmentsText,
      ].some((value) => value.toLowerCase().includes(keyword));
    })
    .map((person) => ({ kind: 'person' as const, person }));
  const agentRows = agents
    .filter(() => filter === 'all' || filter === 'agents')
    .filter((agent) => {
      if (!keyword) return true;
      return [
        agent.name,
        agent.description || '',
        agent.primary_owner_name || '',
        ...agent.departments.map((item) => `${item.department_name} ${item.is_primary ? '主责' : '共享'}`),
      ].some((value) => value.toLowerCase().includes(keyword));
    })
    .map((agent) => ({ kind: 'agent' as const, agent }));
  const rows = [...personRows, ...agentRows].sort((left, right) => {
    const leftName = left.kind === 'person' ? left.person.name : left.agent.name;
    const rightName = right.kind === 'person' ? right.person.name : right.agent.name;
    return leftName.localeCompare(rightName, 'zh');
  });

  function openPersonCreate() {
    setPersonDraft({ ...EMPTY_PERSON });
  }

  function fillPersonFromWecom(person: WecomDirectoryUser) {
    setPersonDraft({
      ...EMPTY_PERSON,
      name: person.name,
      email: person.email,
      wecomUserid: person.userid,
    });
  }

  async function copyText(value: string) {
    try {
      if (!navigator.clipboard?.writeText) {
        notify.error('当前浏览器不支持复制');
        return;
      }
      await navigator.clipboard.writeText(value);
      notify.success('已复制');
    } catch {
      notify.error('复制失败');
    }
  }

  function openPersonEdit(person: EnterprisePersonRead) {
    setPersonDraft(draftFromPerson(person));
  }

  async function savePerson() {
    if (!personDraft) return;
    if (!personDraft.name.trim()) {
      notify.error('请填写姓名');
      return;
    }
    setPersonSaving(true);
    try {
      const payload = {
        tenant_id: TENANT_ID,
        name: personDraft.name.trim(),
        employee_no: personDraft.employeeNo.trim() || null,
        phone: personDraft.phone.trim() || null,
        email: personDraft.email.trim() || null,
        status: personDraft.status,
        primary_user_id: personDraft.primaryUserId || null,
      };
      if (personDraft.id) {
        await api.patch(`/api/enterprise/org/people/${encodeURIComponent(personDraft.id)}`, payload);
        notify.success('企业人员已更新');
      } else {
        await api.post('/api/enterprise/org/people', payload);
        notify.success('企业人员已创建');
      }
      setPersonDraft(null);
      await load();
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '保存企业人员失败');
    } finally {
      setPersonSaving(false);
    }
  }

  async function confirmDeletePerson() {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await api.delete(
        `/api/enterprise/org/people/${encodeURIComponent(deleteTarget.id)}?tenant_id=${encodeURIComponent(TENANT_ID)}`,
      );
      notify.success('企业人员已删除');
      setDeleteTarget(null);
      await load();
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '删除企业人员失败');
    } finally {
      setDeleting(false);
    }
  }

  function openAgentEditor(agent: OrgAgentRead) {
    setAgentDraft({
      agent,
      departmentIds: agent.departments.map((item) => item.department_id),
      primaryId: agent.departments.find((item) => item.is_primary)?.department_id || '',
    });
    setAgentSearch('');
  }

  function toggleAgentDepartment(departmentId: string) {
    setAgentDraft((current) => {
      if (!current) return current;
      const departmentIds = current.departmentIds.includes(departmentId)
        ? current.departmentIds.filter((item) => item !== departmentId)
        : [...current.departmentIds, departmentId];
      const primaryId = departmentIds.includes(current.primaryId)
        ? current.primaryId
        : departmentIds[0] || '';
      return { ...current, departmentIds, primaryId };
    });
  }

  async function saveAgentDepartments() {
    if (!agentDraft) return;
    if (agentDraft.departmentIds.length && !agentDraft.departmentIds.includes(agentDraft.primaryId)) {
      notify.error('请选择主责部门');
      return;
    }
    setAgentSaving(true);
    try {
      await api.put(`/api/enterprise/org/agents/${encodeURIComponent(agentDraft.agent.agent_id)}/departments`, {
        tenant_id: TENANT_ID,
        department_ids: agentDraft.departmentIds,
        primary_department_id: agentDraft.departmentIds.length ? agentDraft.primaryId : null,
      });
      notify.success('部门归属已更新');
      setAgentDraft(null);
      await load();
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '保存部门归属失败');
    } finally {
      setAgentSaving(false);
    }
  }

  const agentDepartments = agentDraft
    ? departments.filter((item) => item.name.toLowerCase().includes(agentSearch.trim().toLowerCase()))
    : [];

  return (
    <div className="flex flex-col gap-[16px]">
      <div className="flex flex-wrap items-start justify-between gap-[12px]">
        <div>
          <h2 className="m-0 text-[15px] font-semibold text-[#18181a]">成员</h2>
          <p className="m-0 mt-[4px] text-[12px] text-[#858b9c]">
            统一查看企业人员和数字员工；可按类型筛选。数字员工能力配置仍在「我的数字员工」中维护。
          </p>
        </div>
        <div className="flex flex-wrap gap-[8px]">
          <UIButton
            variant="outline"
            className="h-8 rounded-[10px] px-3 text-[12px]"
            onClick={() => void load()}
            disabled={loading}
          >
            刷新
          </UIButton>
          <UIButton
            variant="outline"
            className="h-8 rounded-[10px] px-3 text-[12px]"
            onClick={() => navigate(EnterpriseRoute.Agents)}
          >
            创建数字员工
          </UIButton>
          {canPickWecom ? (
            <WecomDirectoryPicker
              buttonLabel="从企微选择"
              title="从企微通讯录选择"
              description="可多选。确认后把姓名、邮箱和 userid 留在本页，用于核对账号和权限；不会写入组织或通讯录。"
              buttonClassName="h-8 rounded-[10px] px-3 text-[12px]"
              onConfirm={(users) => setWecomSelection(users)}
            />
          ) : null}
          <UIButton
            className="h-8 rounded-[10px] bg-[#18181a] px-3 text-[12px] text-white"
            onClick={openPersonCreate}
          >
            新建人员
          </UIButton>
        </div>
      </div>

      {wecomSelection.length > 0 ? (
        <section className="rounded-[14px] border border-[#e3e7f1] bg-white p-[14px]" data-testid="wecom-selected-people">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div>
              <h3 className="m-0 text-[13px] font-semibold text-[#18181a]">{`已从企微选择 ${wecomSelection.length} 人`}</h3>
              <p className="m-0 mt-1 text-[12px] leading-[1.6] text-[#858b9c]">
                这些信息只留在本页，用于配账号和权限。复制后可到账号管理调整角色，或到企微应用填写临时身份绑定。一次只能把一位成员填入新建人员表单，userid 不会写入人员档案。
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              <UIButton
                type="button"
                variant="outline"
                className="h-8 rounded-[10px] px-3 text-[12px]"
                onClick={() => copyText(wecomSelection.map(formatWecomPerson).join('\n'))}
              >
                复制全部
              </UIButton>
              <UIButton
                type="button"
                variant="outline"
                className="h-8 rounded-[10px] px-3 text-[12px]"
                onClick={() => navigate(EnterpriseRoute.Accounts)}
              >
                前往账号管理
              </UIButton>
              <UIButton
                type="button"
                variant="outline"
                className="h-8 rounded-[10px] px-3 text-[12px]"
                onClick={() => navigate(EnterpriseRoute.CompanyWecomApps)}
              >
                前往企微应用
              </UIButton>
              <UIButton
                type="button"
                className="h-8 rounded-[10px] bg-[#18181a] px-3 text-[12px] text-white"
                disabled={wecomSelection.length !== 1}
                onClick={() => wecomSelection[0] && fillPersonFromWecom(wecomSelection[0])}
              >
                填入新建人员
              </UIButton>
            </div>
          </div>
          <ul className="m-0 mt-3 flex list-none flex-col gap-2 p-0">
            {wecomSelection.map((person) => (
              <li key={person.userid} className="flex flex-wrap items-center justify-between gap-2 rounded-[8px] bg-[#fafbfc] px-3 py-2 text-[12px]">
                <span data-i18n-ignore>
                  {person.name}
                  {' · '}
                  {person.email || '无邮箱'}
                  {' · '}
                  {person.userid}
                </span>
                <UIButton
                  type="button"
                  variant="outline"
                  className="h-7 rounded-[8px] px-2 text-[11px]"
                  onClick={() => copyText(formatWecomPerson(person))}
                >
                  复制
                </UIButton>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <div className="flex flex-wrap items-end justify-between gap-[12px]">
        <UnderlineTabs
          aria-label="成员类型筛选"
          value={filter}
          onChange={setFilter}
          items={filterItems}
          tabClassName="w-auto min-w-[92px] px-[10px]"
        />
        <label className="flex h-8 w-[280px] max-w-full items-center gap-[6px] rounded-[10px] border border-[#e3e7f1] bg-white px-[10px] text-[12px] text-[#858b9c]">
          <span aria-hidden="true">⌕</span>
          <input
            value={searchText}
            onChange={(event) => setSearchText(event.target.value)}
            placeholder="按姓名、账号、部门或负责人筛选"
            aria-label="筛选成员"
            className="min-w-0 flex-1 bg-transparent text-[#18181a] outline-none placeholder:text-[#a0a6b8]"
          />
        </label>
      </div>

      <div className="overflow-hidden rounded-[14px] border border-[#e3e7f1] bg-white">
        <div className="grid grid-cols-[minmax(0,1.25fr)_100px_minmax(0,1.4fr)_minmax(0,0.9fr)_90px_150px] gap-[12px] border-b border-[#eef0f4] bg-[#fafbfc] px-[16px] py-[10px] text-[11px] text-[#858b9c] max-[1000px]:hidden">
          <span>成员</span>
          <span>类型</span>
          <span>组织归属</span>
          <span>账号 / 负责人</span>
          <span>状态</span>
          <span className="text-right">操作</span>
        </div>
        {loading ? (
          <p className="m-0 px-[16px] py-[28px] text-[12px] text-[#858b9c]">加载中…</p>
        ) : rows.length === 0 ? (
          <div className="px-[16px] py-[32px] text-center">
            <p className="m-0 text-[13px] text-[#697085]">没有符合筛选条件的成员。</p>
            {(filter === 'all' || filter === 'people') && people.length === 0 ? (
              <UIButton className="mt-[10px] h-8 rounded-[10px] bg-[#18181a] px-3 text-[12px] text-white" onClick={openPersonCreate}>
                新建第一个企业人员
              </UIButton>
            ) : null}
          </div>
        ) : (
          rows.map((row) => {
            if (row.kind === 'person') {
              const person = row.person;
              const status = personStatus(person.status);
              const personDepartments = departmentsByPerson.get(person.id) || [];
              return (
                <div
                  key={`person-${person.id}`}
                  className="grid grid-cols-[minmax(0,1.25fr)_100px_minmax(0,1.4fr)_minmax(0,0.9fr)_90px_150px] items-center gap-[12px] border-b border-[#f2f4f8] px-[16px] py-[12px] last:border-b-0 max-[1000px]:grid-cols-1"
                >
                  <div className="min-w-0">
                    <div className="truncate text-[13px] font-medium text-[#18181a]" data-i18n-ignore>{person.name}</div>
                    <div className="mt-[2px] text-[11px] text-[#858b9c]" data-i18n-ignore>{person.employee_no || '未填工号'}</div>
                  </div>
                  <div>
                    <span className="rounded-full bg-[#eef4f2] px-[8px] py-[3px] text-[10px] text-[#0f766e]">企业人员</span>
                  </div>
                  <div className="flex flex-wrap gap-[4px]">
                    {personDepartments.length ? personDepartments.map((item) => (
                      <span key={`${item.name}-${item.position || ''}`} className="rounded-full bg-[#f2f3f7] px-[8px] py-[3px] text-[11px] text-[#464c5e]" data-i18n-ignore>
                        {item.name}{item.position ? ` · ${item.position}` : ''}
                      </span>
                    )) : <span className="text-[11px] text-[#858b9c]">未加入部门</span>}
                  </div>
                  <div className="truncate text-[12px] text-[#464c5e]">
                    {person.primary_username ? <span data-i18n-ignore>{person.primary_username}</span> : <span className="text-[#858b9c]">未开通账号</span>}
                  </div>
                  <div>
                    <span className={cn('rounded-full px-[8px] py-[3px] text-[10px]', status.className)}>{status.label}</span>
                  </div>
                  <div className="flex justify-end gap-[6px] max-[1000px]:justify-start">
                    <UIButton variant="outline" className="h-7 rounded-[8px] px-2 text-[11px]" onClick={() => openPersonEdit(person)}>编辑</UIButton>
                    <UIButton variant="outline" className="h-7 rounded-[8px] px-2 text-[11px] text-[#d20b0b]" onClick={() => setDeleteTarget(person)}>删除</UIButton>
                  </div>
                </div>
              );
            }
            const agent = row.agent;
            const status = agentStatus(agent.status);
            return (
              <div
                key={`agent-${agent.agent_id}`}
                className="grid grid-cols-[minmax(0,1.25fr)_100px_minmax(0,1.4fr)_minmax(0,0.9fr)_90px_150px] items-center gap-[12px] border-b border-[#f2f4f8] px-[16px] py-[12px] last:border-b-0 max-[1000px]:grid-cols-1"
              >
                <div className="min-w-0">
                  <div className="flex items-center gap-[6px]">
                    <span className="rounded-[5px] bg-[#eef1fb] px-[6px] py-[2px] text-[9px] text-[#5470b8]">AI</span>
                    <span className="truncate text-[13px] font-medium text-[#18181a]" data-i18n-ignore>{agent.name}</span>
                  </div>
                  {agent.description ? <div className="mt-[2px] truncate text-[11px] text-[#858b9c]" data-i18n-ignore>{agent.description}</div> : null}
                </div>
                <div>
                  <span className="rounded-full bg-[#eef1fb] px-[8px] py-[3px] text-[10px] text-[#5470b8]">数字员工</span>
                </div>
                <div className="flex flex-wrap gap-[4px]">
                  {agent.departments.length ? agent.departments.map((item) => (
                    <span
                      key={item.department_id}
                      className={cn(
                        'rounded-full px-[8px] py-[3px] text-[11px]',
                        item.is_primary ? 'bg-[#18181a] text-white' : 'bg-[#f2f3f7] text-[#464c5e]',
                      )}
                      data-i18n-ignore
                    >
                      {item.department_name}{item.is_primary ? ' · 主责' : ' · 共享'}
                    </span>
                  )) : <span className="text-[11px] text-[#858b9c]">未归属部门</span>}
                </div>
                <div className="truncate text-[12px] text-[#464c5e]">
                  {agent.primary_owner_name ? <span data-i18n-ignore>{agent.primary_owner_name}</span> : <span className="text-[#858b9c]">未设置负责人</span>}
                </div>
                <div>
                  <span className={cn('rounded-full px-[8px] py-[3px] text-[10px]', status.className)}>{status.label}</span>
                </div>
                <div className="flex justify-end gap-[6px] max-[1000px]:justify-start">
                  <UIButton variant="outline" className="h-7 rounded-[8px] px-2 text-[11px]" onClick={() => openAgentEditor(agent)}>设置部门</UIButton>
                  <UIButton variant="outline" className="h-7 rounded-[8px] px-2 text-[11px]" onClick={() => navigate(EnterpriseRoute.Agents)}>查看详情</UIButton>
                </div>
              </div>
            );
          })
        )}
      </div>

      <p className="m-0 text-[11px] text-[#858b9c]">
        企业人员的部门任职、岗位和负责人由「组织架构」维护；数字员工负责人不在此页修改。
      </p>

      <Dialog open={Boolean(personDraft)} onOpenChange={(open) => !open && setPersonDraft(null)}>
        <DialogContent className="flex w-[calc(100%-2rem)] flex-col gap-[14px] sm:max-w-[480px]">
          <DialogTitle className="text-[14px] font-normal text-[#757f9c]">
            {personDraft?.id ? '编辑企业人员' : '新建企业人员'}
          </DialogTitle>
          <label className="flex flex-col gap-[4px] text-[12px] text-[#464c5e]">
            姓名
            <Input
              value={personDraft?.name || ''}
              onChange={(event) => setPersonDraft((prev) => (prev ? { ...prev, name: event.target.value } : prev))}
              className={INPUT_CLASS}
              placeholder="例如：张三"
            />
          </label>
          <div className="grid grid-cols-2 gap-[10px] max-[560px]:grid-cols-1">
            <label className="flex flex-col gap-[4px] text-[12px] text-[#464c5e]">
              工号
              <Input value={personDraft?.employeeNo || ''} onChange={(event) => setPersonDraft((prev) => (prev ? { ...prev, employeeNo: event.target.value } : prev))} className={INPUT_CLASS} placeholder="选填" />
            </label>
            <label className="flex flex-col gap-[4px] text-[12px] text-[#464c5e]">
              状态
              <select className="h-8 rounded-[10px] border border-input bg-transparent px-2.5 text-[12px]" value={personDraft?.status || 'active'} onChange={(event) => setPersonDraft((prev) => (prev ? { ...prev, status: event.target.value as 'active' | 'left' } : prev))}>
                <option value="active">在职</option>
                <option value="left">离职</option>
              </select>
            </label>
            <label className="flex flex-col gap-[4px] text-[12px] text-[#464c5e]">
              手机号
              <Input value={personDraft?.phone || ''} onChange={(event) => setPersonDraft((prev) => (prev ? { ...prev, phone: event.target.value } : prev))} className={INPUT_CLASS} placeholder="选填" />
            </label>
            <label className="flex flex-col gap-[4px] text-[12px] text-[#464c5e]">
              邮箱
              <Input value={personDraft?.email || ''} onChange={(event) => setPersonDraft((prev) => (prev ? { ...prev, email: event.target.value } : prev))} className={INPUT_CLASS} placeholder="选填" />
            </label>
          </div>
          {personDraft?.wecomUserid ? (
            <div className="rounded-[10px] bg-[#fafbfc] px-3 py-2 text-[12px] text-[#464c5e]">
              企微 userid
              <div className="mt-1 font-medium text-[#18181a]" data-i18n-ignore>{personDraft.wecomUserid}</div>
              <p className="m-0 mt-1 text-[11px] text-[#858b9c]">仅用于核对，保存人员档案时不会写入。</p>
            </div>
          ) : null}
          <label className="flex flex-col gap-[4px] text-[12px] text-[#464c5e]">
            关联登录账号（可选）
            <select className="h-8 rounded-[10px] border border-input bg-transparent px-2.5 text-[12px]" value={personDraft?.primaryUserId || ''} onChange={(event) => setPersonDraft((prev) => (prev ? { ...prev, primaryUserId: event.target.value } : prev))}>
              <option value="">不关联 / 未开通</option>
              {availableUsers.map((user) => (
                <option key={user.id} value={user.id}>{user.display_name || user.username}（{user.username}）</option>
              ))}
              {personDraft?.primaryUserId && !availableUsers.some((user) => user.id === personDraft.primaryUserId) ? (
                <option value={personDraft.primaryUserId} data-i18n-ignore>{personDraft.primaryUsername || personDraft.primaryUserId}</option>
              ) : null}
            </select>
          </label>
          <div className="flex justify-end gap-[8px]">
            <UIButton variant="outline" className="h-8 rounded-[10px] px-3 text-[12px]" onClick={() => setPersonDraft(null)}>取消</UIButton>
            <UIButton className="h-8 rounded-[10px] bg-[#18181a] px-3 text-[12px] text-white" disabled={personSaving} onClick={() => void savePerson()}>{personSaving ? '保存中…' : '保存'}</UIButton>
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={Boolean(agentDraft)} onOpenChange={(open) => !open && setAgentDraft(null)}>
        <DialogContent className="flex w-[calc(100%-2rem)] flex-col gap-[14px] sm:max-w-[520px]">
          <DialogTitle className="text-[14px] font-normal text-[#757f9c]">
            {`设置部门归属${agentDraft ? `：${agentDraft.agent.name}` : ''}`}
          </DialogTitle>
          <p className="m-0 text-[12px] text-[#858b9c]">勾选服务的部门，并指定一个主责部门；清空全部部门表示暂不归属。</p>
          <Input
            value={agentSearch}
            onChange={(event) => setAgentSearch(event.target.value)}
            placeholder="筛选部门"
            aria-label="筛选部门"
            className={INPUT_CLASS}
          />
          <div className="flex max-h-[280px] flex-col gap-[4px] overflow-y-auto">
            {agentDepartments.length === 0 ? (
              <p className="m-0 text-[12px] text-[#858b9c]">没有匹配的部门。</p>
            ) : agentDepartments.map((department) => (
              <label key={department.id} className="flex items-center gap-[8px] rounded-[8px] px-[8px] py-[6px] text-[12px] hover:bg-[#f6f7fa]">
                <input
                  type="checkbox"
                  checked={agentDraft?.departmentIds.includes(department.id) || false}
                  onChange={() => toggleAgentDepartment(department.id)}
                />
                <span className="min-w-0 flex-1 truncate" data-i18n-ignore>{department.name}</span>
                <input
                  type="radio"
                  name="member-primary-department"
                  checked={agentDraft?.primaryId === department.id}
                  disabled={!agentDraft?.departmentIds.includes(department.id)}
                  onChange={() => setAgentDraft((prev) => (prev ? { ...prev, primaryId: department.id } : prev))}
                  aria-label={`主责部门 ${department.name}`}
                />
                <span className="text-[10px] text-[#858b9c]">主责</span>
              </label>
            ))}
          </div>
          <div className="flex items-center justify-between gap-[8px]">
            <span className="text-[11px] text-[#858b9c]">
              已选 {agentDraft?.departmentIds.length || 0} 个部门
              {agentDraft?.departmentIds.length ? `，主责：${departments.find((item) => item.id === agentDraft.primaryId)?.name || '未选'}` : ''}
            </span>
            <div className="flex gap-[8px]">
              <UIButton variant="outline" className="h-8 rounded-[10px] px-3 text-[12px]" onClick={() => setAgentDraft(null)}>取消</UIButton>
              <UIButton className="h-8 rounded-[10px] bg-[#18181a] px-3 text-[12px] text-white" disabled={agentSaving} onClick={() => void saveAgentDepartments()}>{agentSaving ? '保存中…' : '保存'}</UIButton>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={Boolean(deleteTarget)}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
        loading={deleting}
        title={`删除企业人员「${deleteTarget?.name || ''}」？`}
        description="删除后会一并移除其部门任职和负责人记录；登录账号不会被删除。"
        onConfirm={() => void confirmDeletePerson()}
      />
    </div>
  );
}
