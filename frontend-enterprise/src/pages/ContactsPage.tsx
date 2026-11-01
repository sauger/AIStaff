import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';

import AppHeader from '@/components/AppHeader';
import CapabilityScopeLoading from '@/components/CapabilityScopeLoading';
import { ConfirmDialog } from '@/components/ConfirmDialog';
import { Input, Textarea } from '@/components/ui';
import { Button as UIButton } from '@/components/ui/button';
import { notify } from '@/components/ui/app-toast';
import { cn } from '@/lib/utils';
import {
  DIALOG_PRIMARY_BUTTON_CLASS,
  OUTLINE_ACTION_BUTTON_CLASS,
} from '@/lib/enterprise-ui';
import {
  ENTERPRISE_AGENT_STORAGE_KEY,
  isTeamScope,
  persistSharedAgentScope,
  readEmployeeScope,
} from '@/lib/agent-scope-storage';
import IconChevronDown from '@/assets/icons/chevron-down.svg?react';
import IconContacts from '@/assets/icons/cap-contacts.svg?react';
import IconFolder from '@/assets/icons/cap-folder.svg?react';
import IconOrg from '@/assets/icons/nav-org.svg?react';
import IconPlus from '@/assets/icons/plus.svg?react';
import { api, ApiError, TENANT_ID } from '../api/client';
import { isEmployeeOwnedBy, type EnterpriseAuthUser } from '../auth';
import { visibleEmployeeAgents } from '../employee';
import type {
  AgentProfileRead,
  ContactListResponse,
  ContactRead,
  EnterprisePersonListResponse,
  EnterprisePersonRead,
  OrgDepartmentRead,
  OrgSettingsRead,
  OrgTreePersonRead,
  OrgTreeResponse,
} from '../types';

type ContactsPageProps = {
  currentUser?: EnterpriseAuthUser;
  onLogout?: () => void;
};

type ContactDraft = {
  id?: string;
  name: string;
  phone: string;
  email: string;
  title: string;
  city: string;
  notes: string;
  wecom_userid: string;
  feishu_openid: string;
  dingtalk_userid: string;
};

type Selection =
  | { type: 'company' }
  | { type: 'department'; id: string }
  | { type: 'member'; id: string }
  | { type: 'personal' }
  | { type: 'contact'; id: string }
  | { type: 'contact-new' };

const EMPTY_DRAFT: ContactDraft = {
  name: '',
  phone: '',
  email: '',
  title: '',
  city: '',
  notes: '',
  wecom_userid: '',
  feishu_openid: '',
  dingtalk_userid: '',
};

const TEXT_FIELDS: { key: keyof ContactDraft; label: string; placeholder: string }[] = [
  { key: 'phone', label: '手机号', placeholder: '选填' },
  { key: 'email', label: '邮箱', placeholder: '选填' },
  { key: 'title', label: '职位', placeholder: '选填' },
  { key: 'city', label: '所在城市', placeholder: '选填' },
  { key: 'wecom_userid', label: '企微 userid', placeholder: '选填，可之后由消息补上' },
  { key: 'feishu_openid', label: '飞书 openid', placeholder: '选填，可之后由消息补上' },
  { key: 'dingtalk_userid', label: '钉钉 userid', placeholder: '选填，可之后由消息补上' },
];

function contactSummary(contact: ContactRead): string {
  return [contact.title, contact.city, contact.phone, contact.email].filter(Boolean).join(' · ');
}

function apiErrorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return '操作失败';
}

function draftFromContact(contact: ContactRead): ContactDraft {
  return {
    id: contact.id,
    name: contact.name,
    phone: contact.phone || '',
    email: contact.email || '',
    title: contact.title || '',
    city: contact.city || '',
    notes: contact.notes || '',
    wecom_userid: contact.wecom_userid || '',
    feishu_openid: contact.feishu_openid || '',
    dingtalk_userid: contact.dingtalk_userid || '',
  };
}

function payloadFromDraft(draft: ContactDraft) {
  return {
    tenant_id: TENANT_ID,
    name: draft.name.trim(),
    phone: draft.phone.trim(),
    email: draft.email.trim(),
    title: draft.title.trim(),
    city: draft.city.trim(),
    notes: draft.notes.trim(),
    wecom_userid: draft.wecom_userid.trim(),
    feishu_openid: draft.feishu_openid.trim(),
    dingtalk_userid: draft.dingtalk_userid.trim(),
  };
}

function normalizeDirectory(
  settings: OrgSettingsRead,
  tree: OrgTreeResponse,
  people: EnterprisePersonListResponse,
) {
  const rawName = settings && typeof settings === 'object' && typeof settings.enterprise_name === 'string'
    ? settings.enterprise_name.trim()
    : '';
  return {
    name: rawName,
    departments: Array.isArray(tree?.departments) ? tree.departments : [],
    people: Array.isArray(tree?.people) ? tree.people : [],
    roster: Array.isArray(people?.people) ? people.people : [],
  };
}

function sortDepartments(list: OrgDepartmentRead[]): OrgDepartmentRead[] {
  return [...list].sort(
    (left, right) => left.sort_order - right.sort_order || left.name.localeCompare(right.name, 'zh-CN'),
  );
}

export default function ContactsPage({ currentUser, onLogout }: ContactsPageProps = {}) {
  const [agentId, setAgentId] = useState(readEmployeeScope);
  const [agents, setAgents] = useState<AgentProfileRead[]>([]);
  const [agentScopeLoaded, setAgentScopeLoaded] = useState(false);
  const [listing, setListing] = useState<ContactListResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [draft, setDraft] = useState<ContactDraft | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ContactRead | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [selection, setSelection] = useState<Selection>({ type: 'company' });
  const [companyOpen, setCompanyOpen] = useState(true);
  const [personalOpen, setPersonalOpen] = useState(true);
  const [directoryLoaded, setDirectoryLoaded] = useState(false);
  const [directoryError, setDirectoryError] = useState<string | null>(null);
  const [enterpriseName, setEnterpriseName] = useState('');
  const [departments, setDepartments] = useState<OrgDepartmentRead[]>([]);
  const [treePeople, setTreePeople] = useState<OrgTreePersonRead[]>([]);
  const [roster, setRoster] = useState<EnterprisePersonRead[]>([]);
  const [openDepartmentIds, setOpenDepartmentIds] = useState<Set<string>>(new Set());
  const savingRef = useRef(false);
  const loadSeqRef = useRef(0);

  const currentAgent = useMemo(
    () => agents.find((item) => item.id === agentId) || null,
    [agents, agentId],
  );
  const canWrite = Boolean(listing?.can_write && currentAgent && isEmployeeOwnedBy(currentAgent, currentUser));
  const contacts = listing?.contacts || [];
  const personalContacts = useMemo(
    () => [...contacts].sort((left, right) => left.name.localeCompare(right.name, 'zh-CN')),
    [contacts],
  );

  const departmentById = useMemo(
    () => new Map(departments.map((item) => [item.id, item])),
    [departments],
  );
  const rosterById = useMemo(() => new Map(roster.map((item) => [item.id, item])), [roster]);
  const childDepartments = useMemo(() => {
    const map = new Map<string, OrgDepartmentRead[]>();
    const roots: OrgDepartmentRead[] = [];
    for (const department of departments) {
      if (department.parent_id && departmentById.has(department.parent_id)) {
        const list = map.get(department.parent_id) || [];
        list.push(department);
        map.set(department.parent_id, list);
      } else {
        roots.push(department);
      }
    }
    for (const list of map.values()) {
      list.sort(
        (left, right) => left.sort_order - right.sort_order || left.name.localeCompare(right.name, 'zh-CN'),
      );
    }
    return { roots: sortDepartments(roots), map };
  }, [departmentById, departments]);
  const membersByDepartment = useMemo(() => {
    const map = new Map<string, OrgTreePersonRead[]>();
    const seen = new Set<string>();
    for (const row of treePeople) {
      const key = `${row.department_id}:${row.person_id}`;
      if (seen.has(key)) continue;
      seen.add(key);
      const list = map.get(row.department_id) || [];
      list.push(row);
      map.set(row.department_id, list);
    }
    for (const list of map.values()) {
      list.sort((left, right) => left.name.localeCompare(right.name, 'zh-CN'));
    }
    return map;
  }, [treePeople]);
  const activePeople = useMemo(() => roster.filter((person) => person.status !== 'left'), [roster]);
  const assignedIds = useMemo(() => new Set(treePeople.map((row) => row.person_id)), [treePeople]);
  const unassignedPeople = useMemo(
    () => activePeople
      .filter((person) => !assignedIds.has(person.id))
      .sort((left, right) => left.name.localeCompare(right.name, 'zh-CN')),
    [activePeople, assignedIds],
  );
  const headcount = useMemo(() => {
    const ids = new Set(activePeople.map((person) => person.id));
    for (const row of treePeople) ids.add(row.person_id);
    return ids.size;
  }, [activePeople, treePeople]);
  const companyLabel = !directoryLoaded || directoryError
    ? '企业通讯录'
    : (enterpriseName || '未命名企业');

  useEffect(() => {
    void loadAgents();
    void loadDirectory();
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
    setSelection((current) => (
      current.type === 'contact' || current.type === 'contact-new' ? { type: 'personal' } : current
    ));
    setDraft(null);
  }, [agentId]);

  useEffect(() => {
    if (!agentScopeLoaded || !agentId || isTeamScope(agentId)) return;
    void loadContacts();
  }, [agentId, agentScopeLoaded]);

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

  async function loadDirectory() {
    try {
      const [settings, treeResult, peopleResult] = await Promise.all([
        api.get<OrgSettingsRead>(`/api/enterprise/org/settings?tenant_id=${encodeURIComponent(TENANT_ID)}`),
        api.get<OrgTreeResponse>(`/api/enterprise/org/tree?tenant_id=${encodeURIComponent(TENANT_ID)}`),
        api.get<EnterprisePersonListResponse>(
          `/api/enterprise/org/people?tenant_id=${encodeURIComponent(TENANT_ID)}`,
        ),
      ]);
      const normalized = normalizeDirectory(settings, treeResult, peopleResult);
      setEnterpriseName(normalized.name);
      setDepartments(normalized.departments);
      setTreePeople(normalized.people);
      setRoster(normalized.roster);
      setOpenDepartmentIds(new Set(normalized.departments.map((item) => item.id)));
      setDirectoryError(null);
    } catch (error) {
      setDirectoryError(apiErrorMessage(error));
    } finally {
      setDirectoryLoaded(true);
    }
  }

  async function loadContacts() {
    if (!agentId || isTeamScope(agentId)) return;
    const seq = ++loadSeqRef.current;
    setLoading(true);
    setLoadError(null);
    try {
      const result = await api.get<ContactListResponse>(
        `/api/enterprise/contacts?tenant_id=${encodeURIComponent(TENANT_ID)}&agent_id=${encodeURIComponent(agentId)}`,
      );
      if (seq !== loadSeqRef.current) return;
      setListing(result);
    } catch (error) {
      if (seq !== loadSeqRef.current) return;
      const message = apiErrorMessage(error);
      setLoadError(message);
      setListing(null);
      notify.error(message);
    } finally {
      if (seq === loadSeqRef.current) setLoading(false);
    }
  }

  function toggleDepartment(id: string) {
    setOpenDepartmentIds((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function selectMember(id: string) {
    setSelection({ type: 'member', id });
  }

  function selectContact(contact: ContactRead) {
    setPersonalOpen(true);
    setSelection({ type: 'contact', id: contact.id });
    setDraft(draftFromContact(contact));
  }

  function startCreate() {
    if (!canWrite) return;
    setPersonalOpen(true);
    setSelection({ type: 'contact-new' });
    setDraft({ ...EMPTY_DRAFT });
  }

  async function saveDraft() {
    if (savingRef.current || !draft || !agentId) return;
    if (!draft.name.trim()) {
      notify.error('请填写姓名');
      return;
    }
    const payload = payloadFromDraft(draft);
    savingRef.current = true;
    setSaving(true);
    try {
      if (draft.id) {
        const saved = await api.patch<ContactRead>(
          `/api/enterprise/contacts/${encodeURIComponent(draft.id)}?agent_id=${encodeURIComponent(agentId)}`,
          payload,
        );
        notify.success('已更新联系人');
        setSelection({ type: 'contact', id: saved.id });
        setDraft(draftFromContact(saved));
      } else {
        const saved = await api.post<ContactRead>(
          `/api/enterprise/contacts?agent_id=${encodeURIComponent(agentId)}`,
          payload,
        );
        notify.success('已保存联系人');
        setSelection({ type: 'contact', id: saved.id });
        setDraft(draftFromContact(saved));
      }
      await loadContacts();
    } catch (error) {
      notify.error(apiErrorMessage(error));
    } finally {
      savingRef.current = false;
      setSaving(false);
    }
  }

  async function confirmDelete() {
    if (savingRef.current || !deleteTarget || !agentId) return;
    savingRef.current = true;
    setSaving(true);
    try {
      await api.delete(
        `/api/enterprise/contacts/${encodeURIComponent(deleteTarget.id)}?tenant_id=${encodeURIComponent(TENANT_ID)}&agent_id=${encodeURIComponent(agentId)}`,
      );
      notify.success('已删除联系人');
      setDeleteTarget(null);
      setSelection({ type: 'personal' });
      setDraft(null);
      await loadContacts();
    } catch (error) {
      notify.error(apiErrorMessage(error));
    } finally {
      savingRef.current = false;
      setSaving(false);
    }
  }

  function memberPosts(personId: string): { departments: string; titles: string } {
    const departmentsText: string[] = [];
    const titles: string[] = [];
    for (const row of treePeople) {
      if (row.person_id !== personId) continue;
      const name = departmentById.get(row.department_id)?.name;
      if (name && !departmentsText.includes(name)) departmentsText.push(name);
      if (row.position_title && !titles.includes(row.position_title)) titles.push(row.position_title);
    }
    return {
      departments: departmentsText.join('、'),
      titles: titles.join('、'),
    };
  }

  function renderDetail() {
    if (selection.type === 'company') {
      return (
        <CompanyDetail
          loaded={directoryLoaded}
          error={directoryError}
          name={enterpriseName || '未命名企业'}
          ignoreName={Boolean(enterpriseName)}
          headcount={headcount}
          departments={childDepartments.roots}
          members={listedMembers(activePeople, treePeople)}
          onSelectDepartment={(id) => setSelection({ type: 'department', id })}
          onSelectMember={selectMember}
        />
      );
    }
    if (selection.type === 'department') {
      const department = departmentById.get(selection.id);
      const members = membersByDepartment.get(selection.id) || [];
      const children = childDepartments.map.get(selection.id) || [];
      return (
        <DepartmentDetail
          name={department?.name || ''}
          members={members}
          children={children}
          onSelectDepartment={(id) => setSelection({ type: 'department', id })}
          onSelectMember={selectMember}
        />
      );
    }
    if (selection.type === 'member') {
      const person = rosterById.get(selection.id);
      const treeRow = treePeople.find((row) => row.person_id === selection.id);
      const posts = memberPosts(selection.id);
      return (
        <MemberDetail
          name={person?.name || treeRow?.name || ''}
          department={posts.departments}
          title={posts.titles}
          phone={person?.phone || ''}
          email={person?.email || ''}
          employeeNo={person?.employee_no || ''}
          status={person?.status === 'left' ? '已离职' : '在职'}
        />
      );
    }
    if (selection.type === 'contact' || selection.type === 'contact-new') {
      const contact = selection.type === 'contact'
        ? contacts.find((item) => item.id === selection.id) || null
        : null;
      return (
        <ContactDetail
          contact={contact}
          draft={draft}
          canWrite={canWrite && (selection.type === 'contact-new' || Boolean(contact))}
          saving={saving}
          onChange={setDraft}
          onSave={() => void saveDraft()}
          onDelete={() => contact && setDeleteTarget(contact)}
        />
      );
    }
    return (
      <PersonalDetail
        contacts={personalContacts}
        loading={loading}
        canWrite={canWrite}
        onSelect={selectContact}
        onCreate={startCreate}
      />
    );
  }

  if (!agentScopeLoaded) return <CapabilityScopeLoading />;

  if (isTeamScope(agentId) || !agentId) {
    return (
      <div className="min-h-full box-border px-[48px] pt-[32px] pb-[43px] max-[900px]:px-[16px]">
        <AppHeader
          onLogout={onLogout}
          userName={currentUser?.username}
          title="通讯录"
          description="企业通讯录全员可见。个人通讯录只属于当前数字员工，可以自行添加联系人。"
        />
        <EmptyState text="请先选择一个数字员工，再管理通讯录。" />
      </div>
    );
  }

  return (
    <div className="min-h-full box-border px-[48px] pt-[32px] pb-[43px] max-[900px]:px-[16px]">
      <AppHeader
        onLogout={onLogout}
        userName={currentUser?.username}
        title="通讯录"
        description="企业通讯录全员可见。个人通讯录只属于当前数字员工，可以自行添加联系人。"
      />

      {listing && !canWrite ? (
        <p className="mt-[16px] rounded-[10px] bg-[#f6f7fa] px-[12px] py-[8px] text-[12px] text-[#697085]">
          管理员正在查阅其他员工的通讯录，只能查看，不能新增、编辑或删除。
        </p>
      ) : null}
      {loadError ? (
        <p className="mt-[16px] text-[13px] text-[#b45309]" role="alert">{loadError}</p>
      ) : null}

      <div className="mt-[20px] flex h-[min(720px,calc(100vh-220px))] min-h-[480px] overflow-hidden rounded-[16px] border border-[#eceef1] bg-white max-[900px]:h-auto max-[900px]:flex-col">
        <nav aria-label="通讯录导航" className="flex w-[300px] shrink-0 flex-col border-r border-[#eceef1] max-[900px]:w-full max-[900px]:border-r-0 max-[900px]:border-b">
          <div role="tree" aria-label="通讯录导航" className="min-h-0 flex-1 overflow-y-auto px-[8px] py-[8px]">
            <TreeRow
              label={companyLabel}
              depth={0}
              selected={selection.type === 'company'}
              expanded={companyOpen}
              canExpand
              ignoreLabel={Boolean(enterpriseName) && directoryLoaded && !directoryError}
              onToggle={() => setCompanyOpen((open) => !open)}
              onSelect={() => setSelection({ type: 'company' })}
              icon={<IconOrg className="size-[14px]" />}
            />
            {companyOpen ? (
              <div role="group">
                <DepartmentBranches
                  departments={childDepartments.roots}
                  depth={1}
                  childrenByParent={childDepartments.map}
                  membersByDepartment={membersByDepartment}
                  openDepartmentIds={openDepartmentIds}
                  selection={selection}
                  onToggle={toggleDepartment}
                  onSelectDepartment={(id) => setSelection({ type: 'department', id })}
                  onSelectMember={selectMember}
                />
                {unassignedPeople.map((person) => (
                  <TreeRow
                    key={person.id}
                    label={person.name}
                    depth={1}
                    selected={selection.type === 'member' && selection.id === person.id}
                    canExpand={false}
                    ignoreLabel
                    onSelect={() => selectMember(person.id)}
                    icon={<IconContacts className="size-[14px]" />}
                  />
                ))}
              </div>
            ) : null}

            <TreeRow
              label="个人通讯录"
              depth={0}
              selected={selection.type === 'personal'}
              expanded={personalOpen}
              canExpand
              onToggle={() => setPersonalOpen((open) => !open)}
              onSelect={() => setSelection({ type: 'personal' })}
              icon={<IconContacts className="size-[14px]" />}
              trailing={canWrite ? (
                <button
                  type="button"
                  aria-label="新增联系人"
                  className="flex size-[28px] shrink-0 items-center justify-center rounded-[8px] text-[#464c5e] hover:bg-[#f6f7fa]"
                  onClick={startCreate}
                >
                  <IconPlus className="size-[14px]" />
                </button>
              ) : null}
            />
            {personalOpen ? (
              <div role="group">
                {personalContacts.map((contact) => (
                  <TreeRow
                    key={contact.id}
                    label={contact.name}
                    depth={1}
                    selected={selection.type === 'contact' && selection.id === contact.id}
                    canExpand={false}
                    ignoreLabel
                    onSelect={() => selectContact(contact)}
                    icon={<IconContacts className="size-[14px]" />}
                  />
                ))}
              </div>
            ) : null}
          </div>
        </nav>
        <section aria-label="通讯录详情" className="min-w-0 flex-1 overflow-y-auto px-[28px] py-[24px] max-[900px]:min-h-[320px]">
          {renderDetail()}
        </section>
      </div>

      <ConfirmDialog
        open={Boolean(deleteTarget)}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
        title={`删除「${deleteTarget?.name || ''}」？`}
        description="删除后这条联系人记录无法恢复。"
        onConfirm={() => void confirmDelete()}
      />
    </div>
  );
}

function listedMembers(
  activePeople: EnterprisePersonRead[],
  treePeople: OrgTreePersonRead[],
): { id: string; name: string }[] {
  const map = new Map<string, string>();
  for (const person of activePeople) map.set(person.id, person.name);
  for (const row of treePeople) {
    if (!map.has(row.person_id)) map.set(row.person_id, row.name);
  }
  return [...map.entries()]
    .map(([id, name]) => ({ id, name }))
    .sort((left, right) => left.name.localeCompare(right.name, 'zh-CN'));
}

function DepartmentBranches({
  departments,
  depth,
  childrenByParent,
  membersByDepartment,
  openDepartmentIds,
  selection,
  onToggle,
  onSelectDepartment,
  onSelectMember,
}: {
  departments: OrgDepartmentRead[];
  depth: number;
  childrenByParent: Map<string, OrgDepartmentRead[]>;
  membersByDepartment: Map<string, OrgTreePersonRead[]>;
  openDepartmentIds: Set<string>;
  selection: Selection;
  onToggle: (id: string) => void;
  onSelectDepartment: (id: string) => void;
  onSelectMember: (id: string) => void;
}) {
  return (
    <>
      {departments.map((department) => {
        const children = childrenByParent.get(department.id) || [];
        const members = membersByDepartment.get(department.id) || [];
        const expanded = openDepartmentIds.has(department.id);
        return (
          <div key={department.id}>
            <TreeRow
              label={department.name}
              depth={depth}
              selected={selection.type === 'department' && selection.id === department.id}
              expanded={expanded}
              canExpand
              ignoreLabel
              onToggle={() => onToggle(department.id)}
              onSelect={() => onSelectDepartment(department.id)}
              icon={<IconFolder className="size-[14px]" />}
            />
            {expanded ? (
              <div role="group">
                <DepartmentBranches
                  departments={children}
                  depth={depth + 1}
                  childrenByParent={childrenByParent}
                  membersByDepartment={membersByDepartment}
                  openDepartmentIds={openDepartmentIds}
                  selection={selection}
                  onToggle={onToggle}
                  onSelectDepartment={onSelectDepartment}
                  onSelectMember={onSelectMember}
                />
                {members.map((member) => (
                  <TreeRow
                    key={member.affiliation_id}
                    label={member.name}
                    depth={depth + 1}
                    selected={selection.type === 'member' && selection.id === member.person_id}
                    canExpand={false}
                    ignoreLabel
                    onSelect={() => onSelectMember(member.person_id)}
                    icon={<IconContacts className="size-[14px]" />}
                  />
                ))}
              </div>
            ) : null}
          </div>
        );
      })}
    </>
  );
}

function TreeRow({
  label,
  depth,
  selected,
  expanded,
  canExpand,
  ignoreLabel,
  onToggle,
  onSelect,
  icon,
  trailing,
}: {
  label: string;
  depth: number;
  selected: boolean;
  expanded?: boolean;
  canExpand: boolean;
  ignoreLabel?: boolean;
  onToggle?: () => void;
  onSelect: () => void;
  icon: ReactNode;
  trailing?: ReactNode;
}) {
  return (
    <div className="flex items-center gap-[2px]" style={{ paddingLeft: depth * 14 }}>
      {canExpand ? (
        <button
          type="button"
          className="flex size-[22px] shrink-0 items-center justify-center rounded-[6px] text-[#858b9c] hover:bg-[#f6f7fa]"
          aria-label={expanded ? `收起 ${label}` : `展开 ${label}`}
          onClick={onToggle}
        >
          <IconChevronDown className={cn('size-[14px] transition-transform', expanded ? '' : '-rotate-90')} />
        </button>
      ) : (
        <span className="inline-block size-[22px] shrink-0" />
      )}
      <button
        type="button"
        aria-pressed={selected}
        onClick={onSelect}
        className={cn(
          'flex h-[32px] min-w-0 flex-1 items-center gap-[6px] rounded-[8px] px-[6px] text-left text-[13px]',
          selected ? 'bg-[#d6e6ff] font-medium text-[#174ea6]' : 'text-[#17191f] hover:bg-[#f6f7fa]',
        )}
      >
        <span className="flex size-[16px] shrink-0 items-center justify-center">{icon}</span>
        {ignoreLabel
          ? <span className="truncate" data-i18n-ignore>{label}</span>
          : <span className="truncate">{label}</span>}
      </button>
      {trailing}
    </div>
  );
}

function CountLine({ count }: { count: number }) {
  return <p className="m-0 text-[13px] text-[#858b9c]">{`- ${count}人 -`}</p>;
}

function CompanyDetail({
  loaded,
  error,
  name,
  ignoreName,
  headcount,
  departments,
  members,
  onSelectDepartment,
  onSelectMember,
}: {
  loaded: boolean;
  error: string | null;
  name: string;
  ignoreName: boolean;
  headcount: number;
  departments: OrgDepartmentRead[];
  members: { id: string; name: string }[];
  onSelectDepartment: (id: string) => void;
  onSelectMember: (id: string) => void;
}) {
  if (!loaded) return <p className="m-0 text-[13px] text-[#697085]">正在加载通讯录</p>;
  if (error) return <p className="m-0 text-[13px] text-[#b45309]" role="alert">{error}</p>;
  return (
    <div className="flex flex-col gap-[16px]">
      <div>
        <h2
          className="m-0 text-[22px] font-medium text-[#17191f]"
          data-i18n-ignore={ignoreName ? true : undefined}
        >
          {name}
        </h2>
        <div className="mt-[8px]">
          <CountLine count={headcount} />
        </div>
      </div>
      {departments.length === 0 && members.length === 0 ? (
        <p className="m-0 text-[13px] text-[#697085]">还没有部门或成员</p>
      ) : null}
      {departments.length > 0 ? (
        <NameList title="部门" items={departments.map((item) => ({ id: item.id, name: item.name }))} onSelect={onSelectDepartment} />
      ) : null}
      {members.length > 0 ? (
        <NameList title="成员列表" items={members} onSelect={onSelectMember} />
      ) : null}
    </div>
  );
}

function DepartmentDetail({
  name,
  members,
  children,
  onSelectDepartment,
  onSelectMember,
}: {
  name: string;
  members: OrgTreePersonRead[];
  children: OrgDepartmentRead[];
  onSelectDepartment: (id: string) => void;
  onSelectMember: (id: string) => void;
}) {
  return (
    <div className="flex flex-col gap-[16px]">
      <div>
        <h2 className="m-0 text-[22px] font-medium text-[#17191f]" data-i18n-ignore>{name}</h2>
        <div className="mt-[8px]">
          <CountLine count={members.length} />
        </div>
      </div>
      {children.length > 0 ? (
        <NameList
          title="下级部门"
          items={children.map((item) => ({ id: item.id, name: item.name }))}
          onSelect={onSelectDepartment}
        />
      ) : null}
      <div>
        <h3 className="m-0 text-[13px] font-medium text-[#464c5e]">直接成员</h3>
        {members.length === 0 ? (
          <p className="mt-[8px] mb-0 text-[13px] text-[#697085]">当前没有直接成员</p>
        ) : (
          <NameList
            title=""
            items={members.map((item) => ({ id: item.person_id, name: item.name }))}
            onSelect={onSelectMember}
          />
        )}
      </div>
    </div>
  );
}

function NameList({
  title,
  items,
  onSelect,
}: {
  title: string;
  items: { id: string; name: string }[];
  onSelect: (id: string) => void;
}) {
  return (
    <div>
      {title ? <h3 className="m-0 text-[13px] font-medium text-[#464c5e]">{title}</h3> : null}
      <ul className={cn('m-0 flex list-none flex-col gap-[4px] p-0', title ? 'mt-[8px]' : '')}>
        {items.map((item) => (
          <li key={item.id}>
            <button
              type="button"
              className="text-left text-[13px] text-[#174ea6] hover:underline"
              onClick={() => onSelect(item.id)}
            >
              <span data-i18n-ignore>{item.name}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function MemberDetail({
  name,
  department,
  title,
  phone,
  email,
  employeeNo,
  status,
}: {
  name: string;
  department: string;
  title: string;
  phone: string;
  email: string;
  employeeNo: string;
  status: string;
}) {
  return (
    <div>
      <h2 className="m-0 text-[22px] font-medium text-[#17191f]" data-i18n-ignore>{name}</h2>
      <dl className="mt-[16px] mb-0">
        <ReadRow label="所属部门" value={department} />
        <ReadRow label="职位" value={title} />
        <ReadRow label="手机号" value={phone} />
        <ReadRow label="邮箱" value={email} />
        <ReadRow label="工号" value={employeeNo} />
        <ReadRow label="状态" value={status} literal />
      </dl>
    </div>
  );
}

function PersonalDetail({
  contacts,
  loading,
  canWrite,
  onSelect,
  onCreate,
}: {
  contacts: ContactRead[];
  loading: boolean;
  canWrite: boolean;
  onSelect: (contact: ContactRead) => void;
  onCreate: () => void;
}) {
  return (
    <div className="flex flex-col gap-[16px]">
      <div className="flex items-start justify-between gap-[12px]">
        <div>
          <h2 className="m-0 text-[22px] font-medium text-[#17191f]">个人通讯录</h2>
          <div className="mt-[8px]">
            <CountLine count={contacts.length} />
          </div>
        </div>
        {canWrite ? (
          <UIButton className={cn(DIALOG_PRIMARY_BUTTON_CLASS, 'h-[34px]')} onClick={onCreate}>
            新增联系人
          </UIButton>
        ) : null}
      </div>
      {loading && contacts.length === 0 ? (
        <p className="m-0 text-[13px] text-[#697085]">正在加载通讯录</p>
      ) : null}
      {!loading && contacts.length === 0 ? (
        <p className="m-0 text-[13px] text-[#697085]">还没有联系人，先新增一条吧。</p>
      ) : null}
      {contacts.length > 0 ? (
        <NameList
          title="成员列表"
          items={contacts.map((item) => ({ id: item.id, name: item.name }))}
          onSelect={(id) => {
            const contact = contacts.find((item) => item.id === id);
            if (contact) onSelect(contact);
          }}
        />
      ) : null}
    </div>
  );
}

function ContactDetail({
  contact,
  draft,
  canWrite,
  saving,
  onChange,
  onSave,
  onDelete,
}: {
  contact: ContactRead | null;
  draft: ContactDraft | null;
  canWrite: boolean;
  saving: boolean;
  onChange: (draft: ContactDraft) => void;
  onSave: () => void;
  onDelete: () => void;
}) {
  if (!canWrite) {
    if (!contact) return <p className="m-0 text-[13px] text-[#697085]">还没有联系人，先新增一条吧。</p>;
    return (
      <div>
        <h2 className="m-0 text-[22px] font-medium text-[#17191f]" data-i18n-ignore>{contact.name}</h2>
        <ContactBadges contact={contact} />
        <dl className="mt-[16px] mb-0">
          <ReadRow label="手机号" value={contact.phone || ''} />
          <ReadRow label="邮箱" value={contact.email || ''} />
          <ReadRow label="职位" value={contact.title || ''} />
          <ReadRow label="所在城市" value={contact.city || ''} />
          <ReadRow label="企微 userid" value={contact.wecom_userid || ''} />
          <ReadRow label="飞书 openid" value={contact.feishu_openid || ''} />
          <ReadRow label="钉钉 userid" value={contact.dingtalk_userid || ''} />
          <ReadRow label="备注" value={contact.notes || ''} />
        </dl>
      </div>
    );
  }

  const form = draft || EMPTY_DRAFT;
  return (
    <div className="flex max-w-[520px] flex-col gap-[12px]">
      <h2 className="m-0 text-[22px] font-medium text-[#17191f]">
        {form.id ? '编辑联系人' : '新增联系人'}
      </h2>
      {contact ? <ContactBadges contact={contact} /> : null}
      <p className="m-0 text-[12px] text-[#697085]">
        联系人不必已有对话账号。企微、飞书、钉钉的外部 id 可留空，之后由入站消息补上尚未填写的值。
      </p>
      <label className="flex flex-col gap-[6px] text-[12px] font-medium text-[#464c5e]">
        姓名
        <Input
          value={form.name}
          onChange={(event) => onChange({ ...form, name: event.target.value })}
          placeholder="例如：张三"
        />
      </label>
      {TEXT_FIELDS.map((field) => (
        <label key={field.key} className="flex flex-col gap-[6px] text-[12px] font-medium text-[#464c5e]">
          {field.label}
          <Input
            value={form[field.key] || ''}
            onChange={(event) => onChange({ ...form, [field.key]: event.target.value })}
            placeholder={field.placeholder}
          />
        </label>
      ))}
      <label className="flex flex-col gap-[6px] text-[12px] font-medium text-[#464c5e]">
        备注
        <Textarea
          value={form.notes}
          onChange={(event) => onChange({ ...form, notes: event.target.value })}
          placeholder="选填"
        />
      </label>
      <div className="flex gap-[8px]">
        <UIButton className={DIALOG_PRIMARY_BUTTON_CLASS} disabled={saving} onClick={onSave}>
          保存
        </UIButton>
        {form.id ? (
          <UIButton
            variant="outline"
            className={OUTLINE_ACTION_BUTTON_CLASS}
            aria-label={`删除 ${form.name}`}
            onClick={onDelete}
          >
            删除
          </UIButton>
        ) : null}
      </div>
    </div>
  );
}

function ContactBadges({ contact }: { contact: ContactRead }) {
  const summary = contactSummary(contact);
  return (
    <div className="mt-[8px]">
      <p className="m-0 text-[12px] text-[#858b9c]">
        {summary ? <span data-i18n-ignore>{summary}</span> : '未填写联系方式'}
      </p>
      <div className="mt-[8px] flex flex-wrap items-center gap-[6px] text-[11px]">
        {contact.wecom_userid ? (
          <span className="rounded-full bg-[#eef4f2] px-[9px] py-[3px] text-[#0f766e]">
            企微 <span data-i18n-ignore>{contact.wecom_userid}</span>
          </span>
        ) : null}
        {contact.feishu_openid ? (
          <span className="rounded-full bg-[#eef4f2] px-[9px] py-[3px] text-[#0f766e]">
            飞书 <span data-i18n-ignore>{contact.feishu_openid}</span>
          </span>
        ) : null}
        {contact.dingtalk_userid ? (
          <span className="rounded-full bg-[#eef4f2] px-[9px] py-[3px] text-[#0f766e]">
            钉钉 <span data-i18n-ignore>{contact.dingtalk_userid}</span>
          </span>
        ) : null}
        {contact.primary_user_id ? (
          <span className="rounded-full bg-[#f6f7fa] px-[9px] py-[3px] text-[#697085]">
            已关联对话账号
          </span>
        ) : null}
      </div>
    </div>
  );
}

function ReadRow({ label, value, literal = false }: { label: string; value: string; literal?: boolean }) {
  const text = value.trim();
  return (
    <div className="grid grid-cols-[88px_minmax(0,1fr)] gap-[12px] border-b border-[#f3f4f6] py-[10px] text-[13px]">
      <dt className="text-[#858b9c]">{label}</dt>
      <dd className="m-0 text-[#17191f]">
        {text ? (literal ? text : <span data-i18n-ignore>{text}</span>) : '未填写'}
      </dd>
    </div>
  );
}

function EmptyState({ text }: { text: string }) {
  return (
    <div className="mt-[20px] flex min-h-[240px] flex-col items-center justify-center rounded-[16px] border border-dashed border-[#e3e7f1] bg-white px-[24px] text-center">
      <p className="m-0 text-[14px] font-medium text-[#17191f]">{text}</p>
    </div>
  );
}
