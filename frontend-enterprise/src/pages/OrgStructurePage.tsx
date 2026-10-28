import { useEffect, useMemo, useState, type ReactNode } from 'react';

import AppHeader from '@/components/AppHeader';
import { ConfirmDialog } from '@/components/ConfirmDialog';
import { Input } from '@/components/ui';
import { Button as UIButton } from '@/components/ui/button';
import { notify } from '@/components/ui/app-toast';
import { cn } from '@/lib/utils';
import { DIALOG_PRIMARY_BUTTON_CLASS, OUTLINE_ACTION_BUTTON_CLASS } from '@/lib/enterprise-ui';

import { api, ApiError, TENANT_ID } from '../api/client';
import type { EnterpriseAuthUser } from '../auth';
import type {
  OrgContactListResponse,
  OrgDepartmentListResponse,
  OrgDepartmentPeopleResponse,
  OrgDepartmentRead,
  OrgHeadRead,
  OrgPersonRead,
  OrgSettingsRead,
} from '../types';

type OrgStructurePageProps = {
  currentUser?: EnterpriseAuthUser;
  onLogout?: () => void;
};

type DepartmentDraft = {
  id?: string;
  name: string;
  parentId: string;
  sortOrder: string;
};

const EMPTY_DEPARTMENT: DepartmentDraft = { name: '', parentId: '', sortOrder: '0' };

const SELECT_CLASS =
  'h-8 w-full rounded-lg border border-input bg-transparent px-2.5 text-sm text-foreground';

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

function apiErrorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return '操作失败';
}

function contactLabel(name: string, agentName: string): string {
  return `${name} · ${agentName}`;
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

export default function OrgStructurePage({ currentUser, onLogout }: OrgStructurePageProps = {}) {
  const [enterpriseName, setEnterpriseName] = useState('');
  const [departments, setDepartments] = useState<OrgDepartmentRead[]>([]);
  const [contacts, setContacts] = useState<OrgContactListResponse['contacts']>([]);
  const [loading, setLoading] = useState(true);
  const [savingName, setSavingName] = useState(false);
  const [draft, setDraft] = useState<DepartmentDraft>(EMPTY_DEPARTMENT);
  const [savingDepartment, setSavingDepartment] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<OrgDepartmentRead | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [asOf, setAsOf] = useState(today);
  const [people, setPeople] = useState<OrgDepartmentPeopleResponse | null>(null);
  const [peopleLoading, setPeopleLoading] = useState(false);
  const [memberContactId, setMemberContactId] = useState('');
  const [memberFrom, setMemberFrom] = useState(today);
  const [memberTo, setMemberTo] = useState('');
  const [headContactId, setHeadContactId] = useState('');
  const [headFrom, setHeadFrom] = useState(today);
  const [headTo, setHeadTo] = useState('');

  const blockedParents = useMemo(() => {
    if (!draft.id) return new Set<string>();
    const blocked = descendantIds(departments, draft.id);
    blocked.add(draft.id);
    return blocked;
  }, [departments, draft.id]);

  useEffect(() => {
    void loadAll();
  }, []);

  useEffect(() => {
    if (!selectedId) {
      setPeople(null);
      return;
    }
    void loadPeople(selectedId, asOf);
  }, [selectedId]);

  async function loadAll() {
    setLoading(true);
    try {
      const [settings, departmentList, contactList] = await Promise.all([
        api.get<OrgSettingsRead>(`/api/enterprise/org/settings?tenant_id=${encodeURIComponent(TENANT_ID)}`),
        api.get<OrgDepartmentListResponse>(
          `/api/enterprise/org/departments?tenant_id=${encodeURIComponent(TENANT_ID)}`,
        ),
        api.get<OrgContactListResponse>(
          `/api/enterprise/org/contacts?tenant_id=${encodeURIComponent(TENANT_ID)}`,
        ),
      ]);
      setEnterpriseName(settings.enterprise_name);
      setDepartments(departmentList.departments);
      setContacts(contactList.contacts);
    } catch (error) {
      notify.error(apiErrorMessage(error));
    } finally {
      setLoading(false);
    }
  }

  async function loadPeople(departmentId: string, day: string) {
    setPeopleLoading(true);
    try {
      const result = await api.get<OrgDepartmentPeopleResponse>(
        `/api/enterprise/org/departments/${encodeURIComponent(departmentId)}/people?tenant_id=${encodeURIComponent(TENANT_ID)}&as_of=${encodeURIComponent(day)}`,
      );
      setPeople(result);
    } catch (error) {
      notify.error(apiErrorMessage(error));
    } finally {
      setPeopleLoading(false);
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

  async function saveDepartment() {
    const name = draft.name.trim();
    if (!name) {
      notify.error('请填写部门名称');
      return;
    }
    const sortOrder = Number(draft.sortOrder || '0');
    if (!Number.isFinite(sortOrder)) {
      notify.error('排序必须是数字');
      return;
    }
    const payload = {
      tenant_id: TENANT_ID,
      name,
      parent_id: draft.parentId || null,
      sort_order: sortOrder,
    };
    const editedId = draft.id;
    setSavingDepartment(true);
    try {
      if (editedId) {
        await api.patch(`/api/enterprise/org/departments/${encodeURIComponent(editedId)}`, payload);
      } else {
        const created = await api.post<OrgDepartmentRead>('/api/enterprise/org/departments', payload);
        setSelectedId(created.id);
      }
      notify.success('已保存部门');
      setDraft(EMPTY_DEPARTMENT);
      await loadAll();
      if (editedId && editedId === selectedId) await loadPeople(editedId, asOf);
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
        setPeople(null);
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
    await loadPeople(selectedId, asOf);
  }

  async function addMember() {
    if (!selectedId) return;
    if (!memberContactId) {
      notify.error('请选择人员');
      return;
    }
    try {
      await api.post('/api/enterprise/org/affiliations', {
        tenant_id: TENANT_ID,
        department_id: selectedId,
        contact_id: memberContactId,
        valid_from: memberFrom || null,
        valid_to: memberTo || null,
      });
      notify.success('已添加成员');
      setMemberContactId('');
      setMemberTo('');
      await loadPeople(selectedId, asOf);
    } catch (error) {
      notify.error(apiErrorMessage(error));
    }
  }

  async function saveAffiliation(row: OrgPersonRead, validFrom: string, validTo: string) {
    if (!selectedId) return;
    try {
      await api.patch(`/api/enterprise/org/affiliations/${encodeURIComponent(row.affiliation_id)}`, {
        tenant_id: TENANT_ID,
        contact_id: row.contact_id,
        valid_from: validFrom,
        valid_to: validTo || null,
        clear_valid_to: !validTo,
      });
      notify.success('已更新任职');
      await loadPeople(selectedId, asOf);
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
      await loadPeople(selectedId, asOf);
    } catch (error) {
      notify.error(apiErrorMessage(error));
    }
  }

  async function addHead() {
    if (!selectedId) return;
    if (!headContactId) {
      notify.error('请选择人员');
      return;
    }
    try {
      await api.post('/api/enterprise/org/heads', {
        tenant_id: TENANT_ID,
        department_id: selectedId,
        contact_id: headContactId,
        valid_from: headFrom || null,
        valid_to: headTo || null,
      });
      notify.success('已设置负责人');
      setHeadContactId('');
      setHeadTo('');
      await loadPeople(selectedId, asOf);
    } catch (error) {
      notify.error(apiErrorMessage(error));
    }
  }

  async function saveHead(row: OrgHeadRead, validFrom: string, validTo: string) {
    if (!selectedId) return;
    try {
      await api.patch(`/api/enterprise/org/heads/${encodeURIComponent(row.head_id)}`, {
        tenant_id: TENANT_ID,
        contact_id: row.contact_id,
        valid_from: validFrom,
        valid_to: validTo || null,
        clear_valid_to: !validTo,
      });
      notify.success('已更新负责人');
      await loadPeople(selectedId, asOf);
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
      await loadPeople(selectedId, asOf);
    } catch (error) {
      notify.error(apiErrorMessage(error));
    }
  }

  function beginEdit(department: OrgDepartmentRead) {
    setDraft({
      id: department.id,
      name: department.name,
      parentId: department.parent_id || '',
      sortOrder: String(department.sort_order),
    });
  }

  function beginChild(department: OrgDepartmentRead) {
    setDraft({ name: '', parentId: department.id, sortOrder: '0' });
    setSelectedId(department.id);
  }

  return (
    <div className="min-h-full box-border px-[48px] pt-[32px] pb-[43px] max-[900px]:px-[16px]">
      <AppHeader
        onLogout={onLogout}
        userName={currentUser?.username}
        title="组织架构"
        description="企业范围的部门树，全租户共用。人员选自各数字员工通讯录里的联系人。"
      />

      <section className="mt-[20px] rounded-[16px] border border-[#eceef1] bg-white p-[16px]">
        <div className="flex flex-col gap-[12px] sm:flex-row sm:items-end">
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
      </section>

      <div className="mt-[16px] grid gap-[16px] lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
        <section className="rounded-[16px] border border-[#eceef1] bg-white p-[16px]">
          <h2 className="m-0 text-[14px] font-medium text-[#17191f]">部门</h2>
          <div className="mt-[12px] flex flex-col gap-[8px]">
            <Field label="部门名称">
              <Input
                aria-label="部门名称"
                placeholder="例如：研发中心"
                value={draft.name}
                onChange={(event) => setDraft((prev) => ({ ...prev, name: event.target.value }))}
              />
            </Field>
            <Field label="上级部门">
              <select
                aria-label="上级部门"
                className={SELECT_CLASS}
                value={draft.parentId}
                onChange={(event) => setDraft((prev) => ({ ...prev, parentId: event.target.value }))}
              >
                <option value="">根部门</option>
                {departments.filter((item) => !blockedParents.has(item.id)).map((item) => (
                  <option key={item.id} value={item.id}>{item.name}</option>
                ))}
              </select>
            </Field>
            <Field label="排序">
              <Input
                aria-label="排序"
                value={draft.sortOrder}
                onChange={(event) => setDraft((prev) => ({ ...prev, sortOrder: event.target.value }))}
              />
            </Field>
            <div className="flex gap-[8px]">
              <UIButton
                className={cn(DIALOG_PRIMARY_BUTTON_CLASS, 'h-[34px]')}
                disabled={savingDepartment}
                onClick={() => void saveDepartment()}
              >
                {draft.id ? '保存部门' : '新增部门'}
              </UIButton>
              {draft.id ? (
                <UIButton
                  variant="outline"
                  className={cn(OUTLINE_ACTION_BUTTON_CLASS, 'h-[34px]')}
                  onClick={() => setDraft(EMPTY_DEPARTMENT)}
                >
                  取消
                </UIButton>
              ) : null}
            </div>
          </div>

          <div className="mt-[16px]">
            {loading ? <p className="text-[13px] text-[#697085]">正在加载组织架构</p> : null}
            {!loading && departments.length === 0 ? (
              <p className="text-[13px] text-[#697085]">还没有部门。先新增一个根部门。</p>
            ) : (
              <DepartmentTree
                departments={departments}
                parentId={null}
                selectedId={selectedId}
                onSelect={setSelectedId}
                onEdit={beginEdit}
                onAddChild={beginChild}
                onDelete={setDeleteTarget}
              />
            )}
          </div>
        </section>

        <section className="rounded-[16px] border border-[#eceef1] bg-white p-[16px]" data-testid="org-people">
          {!selectedId ? (
            <p className="m-0 text-[13px] text-[#697085]">
              选择一个部门后，可以查看该部门的人员、负责人和任职时间。
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
                  disabled={peopleLoading}
                  onClick={() => void queryPeople()}
                >
                  查询该部门
                </UIButton>
              </div>

              <div>
                <h2 className="m-0 text-[14px] font-medium text-[#17191f]">直接成员</h2>
                {peopleLoading && !people ? (
                  <p className="mt-[8px] text-[13px] text-[#697085]">正在加载组织架构</p>
                ) : null}
                {people && people.people.length === 0 ? (
                  <p className="mt-[8px] text-[13px] text-[#697085]">当前没有符合该日期的人员。</p>
                ) : null}
                <ul className="m-0 mt-[8px] flex list-none flex-col gap-[8px] p-0">
                  {(people?.people || []).map((person) => (
                    <li key={person.affiliation_id} className="text-[13px] text-[#17191f]">
                      <span data-i18n-ignore>{person.contact_name}</span>
                      <span className="text-[#697085]">
                        {' · '}
                        <span data-i18n-ignore>{person.agent_name}</span>
                        {' · '}
                        <span data-i18n-ignore>{tenureLabel(person.valid_from, person.valid_to)}</span>
                      </span>
                    </li>
                  ))}
                </ul>
              </div>

              <div className="flex flex-col gap-[8px]">
                <h2 className="m-0 text-[14px] font-medium text-[#17191f]">添加成员</h2>
                <ContactSelect
                  contacts={contacts}
                  value={memberContactId}
                  onChange={setMemberContactId}
                  label="人员"
                />
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
              </div>

              <TenureList
                title="全部任职"
                rows={(people?.affiliations || []).map((row) => ({
                  id: row.affiliation_id,
                  label: contactLabel(row.contact_name, row.agent_name),
                  validFrom: row.valid_from,
                  validTo: row.valid_to || '',
                  onSave: (validFrom, validTo) => saveAffiliation(row, validFrom, validTo),
                  onDelete: () => removeAffiliation(row.affiliation_id),
                }))}
              />

              <div>
                <h2 className="m-0 text-[14px] font-medium text-[#17191f]">负责人</h2>
                <p className="mt-[8px] text-[13px] text-[#17191f]">
                  当前负责人：
                  {people?.current_head ? (
                    <span data-i18n-ignore>
                      {contactLabel(people.current_head.contact_name, people.current_head.agent_name)}
                    </span>
                  ) : (
                    <span>暂无负责人</span>
                  )}
                </p>
                <div className="mt-[8px] flex flex-col gap-[8px]">
                  <ContactSelect
                    contacts={contacts}
                    value={headContactId}
                    onChange={setHeadContactId}
                    label="负责人人选"
                  />
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
                rows={(people?.heads || []).map((row) => ({
                  id: row.head_id,
                  label: contactLabel(row.contact_name, row.agent_name),
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

      <ConfirmDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => {
          if (!open) setDeleteTarget(null);
        }}
        title="删除部门"
        description="删除后该部门的任职和负责人记录也会去掉。下级部门需要先移走。"
        loading={deleting}
        onConfirm={() => void confirmDeleteDepartment()}
      />
    </div>
  );
}

function ContactSelect({
  contacts,
  value,
  onChange,
  label,
}: {
  contacts: OrgContactListResponse['contacts'];
  value: string;
  onChange: (value: string) => void;
  label: string;
}) {
  return (
    <Field label={label}>
      <select
        aria-label={label}
        className={SELECT_CLASS}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="">请选择人员</option>
        {contacts.map((contact) => (
          <option key={contact.id} value={contact.id}>
            {contactLabel(contact.name, contact.agent_name)}
          </option>
        ))}
      </select>
    </Field>
  );
}

function DepartmentTree({
  departments,
  parentId,
  selectedId,
  onSelect,
  onEdit,
  onAddChild,
  onDelete,
}: {
  departments: OrgDepartmentRead[];
  parentId: string | null;
  selectedId: string | null;
  onSelect: (id: string) => void;
  onEdit: (department: OrgDepartmentRead) => void;
  onAddChild: (department: OrgDepartmentRead) => void;
  onDelete: (department: OrgDepartmentRead) => void;
}) {
  const nodes = childrenOf(departments, parentId);
  if (nodes.length === 0) return null;
  return (
    <ul className={cn('m-0 flex list-none flex-col gap-[6px] p-0', parentId && 'mt-[6px] ml-[16px]')}>
      {nodes.map((department) => (
        <li key={department.id} data-testid={`dept-${department.id}`}>
          <div
            className={cn(
              'flex flex-wrap items-center gap-[8px] rounded-[12px] border border-transparent px-[8px] py-[6px]',
              selectedId === department.id && 'border-[#eceef1] bg-[#f6f7fa]',
            )}
          >
            <button
              type="button"
              className="text-[13px] font-medium text-[#17191f]"
              onClick={() => onSelect(department.id)}
            >
              <span data-i18n-ignore>{department.name}</span>
            </button>
            <UIButton
              variant="outline"
              className={cn(OUTLINE_ACTION_BUTTON_CLASS, 'h-[28px] px-[8px]')}
              onClick={() => onAddChild(department)}
            >
              添加下级
            </UIButton>
            <UIButton
              variant="outline"
              className={cn(OUTLINE_ACTION_BUTTON_CLASS, 'h-[28px] px-[8px]')}
              onClick={() => onEdit(department)}
            >
              编辑
            </UIButton>
            <UIButton
              variant="outline"
              className={cn(OUTLINE_ACTION_BUTTON_CLASS, 'h-[28px] px-[8px]')}
              onClick={() => onDelete(department)}
            >
              删除
            </UIButton>
          </div>
          <DepartmentTree
            departments={departments}
            parentId={department.id}
            selectedId={selectedId}
            onSelect={onSelect}
            onEdit={onEdit}
            onAddChild={onAddChild}
            onDelete={onDelete}
          />
        </li>
      ))}
    </ul>
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
    validFrom: string;
    validTo: string;
    onSave: (validFrom: string, validTo: string) => Promise<void>;
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
    validFrom: string;
    validTo: string;
    onSave: (validFrom: string, validTo: string) => Promise<void>;
    onDelete: () => Promise<void>;
  };
}) {
  const [validFrom, setValidFrom] = useState(row.validFrom);
  const [validTo, setValidTo] = useState(row.validTo);
  useEffect(() => {
    setValidFrom(row.validFrom);
    setValidTo(row.validTo);
  }, [row.validFrom, row.validTo, row.id]);
  return (
    <li className="flex flex-col gap-[8px] rounded-[12px] border border-[#eceef1] p-[8px]">
      <span className="text-[13px] text-[#17191f]" data-i18n-ignore>{row.label}</span>
      <div className="flex flex-col gap-[8px] sm:flex-row sm:items-end">
        <Field label="任职开始">
          <Input aria-label="任职开始" type="date" value={validFrom} onChange={(event) => setValidFrom(event.target.value)} />
        </Field>
        <Field label="任职结束">
          <Input aria-label="任职结束" type="date" value={validTo} onChange={(event) => setValidTo(event.target.value)} />
        </Field>
        <UIButton
          variant="outline"
          className={cn(OUTLINE_ACTION_BUTTON_CLASS, 'h-[34px]')}
          onClick={() => void row.onSave(validFrom, validTo)}
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
