import { useEffect, useMemo, useState } from 'react';

import {
  Dialog,
  DialogContent,
  DialogTitle,
  Input,
} from '@/components/ui';
import { Button as UIButton } from '@/components/ui/button';
import { notify } from '@/components/ui/app-toast';
import { cn } from '@/lib/utils';

import { api, TENANT_ID } from '../../api/client';

export type WecomDepartment = {
  id: number;
  name: string;
  parent_id: number;
  order: number;
};

export type WecomDirectoryUser = {
  userid: string;
  name: string;
  email: string;
  department_ids: number[];
};

type DepartmentNode = WecomDepartment & { children: DepartmentNode[] };

type DirectoryResponse = {
  app_name?: string;
  departments: WecomDepartment[];
};

type UsersResponse = {
  users: WecomDirectoryUser[];
};

type Props = {
  mode?: 'multiple' | 'single';
  buttonLabel: string;
  title: string;
  description: string;
  confirmLabel?: string;
  buttonClassName?: string;
  disabled?: boolean;
  onConfirm: (users: WecomDirectoryUser[]) => void | Promise<void>;
};

const FIELD_CLASS = 'h-8 rounded-[10px] text-[12px]';

export function buildWecomDepartmentTree(departments: WecomDepartment[]): DepartmentNode[] {
  const ids = new Set(departments.map((item) => item.id));
  const byParent = new Map<number, WecomDepartment[]>();
  for (const department of departments) {
    const parentId = ids.has(department.parent_id) ? department.parent_id : 0;
    const rows = byParent.get(parentId) || [];
    rows.push(department);
    byParent.set(parentId, rows);
  }
  const sortRows = (rows: WecomDepartment[]) => [...rows].sort(
    (left, right) => right.order - left.order
      || left.name.localeCompare(right.name, 'zh')
      || left.id - right.id,
  );

  function walk(parentId: number): DepartmentNode[] {
    return sortRows(byParent.get(parentId) || []).map((department) => ({
      ...department,
      children: walk(department.id),
    }));
  }
  return walk(0);
}

function firstDepartmentId(nodes: DepartmentNode[]): number | null {
  const first = nodes[0];
  return first ? first.id : null;
}

export default function WecomDirectoryPicker({
  mode = 'multiple',
  buttonLabel,
  title,
  description,
  confirmLabel = '确认选择',
  buttonClassName,
  disabled = false,
  onConfirm,
}: Props) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [usersLoading, setUsersLoading] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState('');
  const [appName, setAppName] = useState('');
  const [tree, setTree] = useState<DepartmentNode[]>([]);
  const [departmentId, setDepartmentId] = useState<number | null>(null);
  const [users, setUsers] = useState<WecomDirectoryUser[]>([]);
  const [selected, setSelected] = useState<Map<string, WecomDirectoryUser>>(new Map());
  const [keyword, setKeyword] = useState('');

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    setError('');
    setUsers([]);
    setSelected(new Map());
    setKeyword('');
    setDepartmentId(null);
    void api.get<DirectoryResponse>(
      `/api/enterprise/wecom-apps/directory/departments?tenant_id=${encodeURIComponent(TENANT_ID)}`,
    ).then((result) => {
      if (cancelled) return;
      const nodes = buildWecomDepartmentTree(result.departments || []);
      setTree(nodes);
      setAppName(result.app_name || '');
      setDepartmentId(firstDepartmentId(nodes));
    }).catch((cause: unknown) => {
      if (cancelled) return;
      setTree([]);
      setError(cause instanceof Error ? cause.message : '加载企微通讯录失败');
    }).finally(() => {
      if (!cancelled) setLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [open]);

  useEffect(() => {
    if (!open || departmentId == null) return;
    let cancelled = false;
    setUsersLoading(true);
    setError('');
    void api.get<UsersResponse>(
      `/api/enterprise/wecom-apps/directory/departments/${departmentId}/users`
      + `?tenant_id=${encodeURIComponent(TENANT_ID)}`,
    ).then((result) => {
      if (!cancelled) setUsers(result.users || []);
    }).catch((cause: unknown) => {
      if (cancelled) return;
      setUsers([]);
      setError(cause instanceof Error ? cause.message : '加载企微通讯录失败');
    }).finally(() => {
      if (!cancelled) setUsersLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [open, departmentId]);

  const visibleUsers = useMemo(() => {
    const text = keyword.trim().toLowerCase();
    if (!text) return users;
    return users.filter((user) => [user.name, user.email, user.userid]
      .some((value) => value.toLowerCase().includes(text)));
  }, [keyword, users]);

  function toggleUser(user: WecomDirectoryUser) {
    setSelected((current) => {
      if (mode === 'single') {
        return new Map([[user.userid, user]]);
      }
      const next = new Map(current);
      if (next.has(user.userid)) next.delete(user.userid);
      else next.set(user.userid, user);
      return next;
    });
  }

  async function confirm() {
    const usersToSave = [...selected.values()];
    if (!usersToSave.length) {
      notify.error('请先选择成员');
      return;
    }
    setConfirming(true);
    try {
      await onConfirm(usersToSave);
      setOpen(false);
    } catch (cause: unknown) {
      notify.error(cause instanceof Error ? cause.message : '保存企微选择失败');
    } finally {
      setConfirming(false);
    }
  }

  return (
    <>
      <UIButton
        type="button"
        variant="outline"
        className={buttonClassName || 'h-8 rounded-[10px] px-3 text-[12px]'}
        disabled={disabled}
        onClick={() => setOpen(true)}
      >
        {buttonLabel}
      </UIButton>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="flex max-h-[min(680px,calc(100%-2rem))] w-[calc(100%-2rem)] flex-col gap-3 overflow-hidden sm:max-w-[760px]">
          <DialogTitle className="text-[14px] font-normal text-[#757f9c]">{title}</DialogTitle>
          <p className="m-0 text-[12px] leading-[1.6] text-[#858b9c]">{description}</p>
          {appName ? (
            <p className="m-0 text-[12px] text-[#464c5e]">
              默认应用：<span data-i18n-ignore>{appName}</span>
            </p>
          ) : null}
          {error ? <p className="m-0 text-[12px] text-[#d20b0b]">{error}</p> : null}
          <div className="grid min-h-0 flex-1 grid-cols-[200px_minmax(0,1fr)] gap-3 max-[640px]:grid-cols-1">
            <div className="flex max-h-[360px] flex-col overflow-y-auto rounded-[10px] border border-[#eef0f4] bg-[#fafbfc] p-1" aria-label="企微部门">
              {loading ? <p className="m-0 px-2 py-3 text-[12px] text-[#858b9c]">加载部门…</p> : null}
              {!loading && tree.length === 0 ? <p className="m-0 px-2 py-3 text-[12px] text-[#858b9c]">没有可见部门。</p> : null}
              <DepartmentTree
                nodes={tree}
                depth={0}
                selectedId={departmentId}
                onSelect={setDepartmentId}
              />
            </div>
            <div className="flex min-h-0 flex-col gap-2">
              <Input
                value={keyword}
                onChange={(event) => setKeyword(event.target.value)}
                placeholder="搜索姓名、邮箱或 userid"
                aria-label="搜索企微成员"
                className={FIELD_CLASS}
              />
              <div className="flex max-h-[320px] flex-col overflow-y-auto rounded-[10px] border border-[#eef0f4]" aria-label="企微成员">
                {usersLoading ? <p className="m-0 px-3 py-3 text-[12px] text-[#858b9c]">加载成员…</p> : null}
                {!usersLoading && visibleUsers.length === 0 ? (
                  <p className="m-0 px-3 py-3 text-[12px] text-[#858b9c]">该部门没有可选择的直属成员。</p>
                ) : null}
                {visibleUsers.map((user) => {
                  const checked = selected.has(user.userid);
                  return (
                    <label
                      key={user.userid}
                      className={cn(
                        'flex cursor-pointer items-start gap-2 border-b border-[#f2f4f8] px-3 py-2 text-[12px] last:border-b-0',
                        checked ? 'bg-[#f6f7fa]' : 'bg-white',
                      )}
                    >
                      <input
                        type={mode === 'single' ? 'radio' : 'checkbox'}
                        name={mode === 'single' ? 'wecom-directory-user' : undefined}
                        checked={checked}
                        onChange={() => toggleUser(user)}
                        aria-label={`${user.name} ${user.userid}`}
                        className="mt-[2px]"
                      />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate font-medium text-[#18181a]" data-i18n-ignore>{user.name}</span>
                        <span className="mt-[2px] block truncate text-[11px] text-[#858b9c]" data-i18n-ignore>
                          {user.email || '无邮箱'}
                          {' · '}
                          {user.userid}
                        </span>
                      </span>
                    </label>
                  );
                })}
              </div>
            </div>
          </div>
          <div className="flex items-center justify-between gap-2">
            <span className="text-[11px] text-[#858b9c]">{`已选 ${selected.size} 人`}</span>
            <div className="flex gap-2">
              <UIButton type="button" variant="outline" className="h-8 rounded-[10px] px-3 text-[12px]" onClick={() => setOpen(false)}>
                取消
              </UIButton>
              <UIButton
                type="button"
                className="h-8 rounded-[10px] bg-[#18181a] px-3 text-[12px] text-white"
                disabled={confirming || selected.size === 0}
                onClick={() => void confirm()}
              >
                {confirming ? '保存中…' : confirmLabel}
              </UIButton>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}

function DepartmentTree({
  nodes,
  depth,
  selectedId,
  onSelect,
}: {
  nodes: DepartmentNode[];
  depth: number;
  selectedId: number | null;
  onSelect: (id: number) => void;
}) {
  return (
    <>
      {nodes.map((node) => (
        <div key={node.id}>
          <button
            type="button"
            className={cn(
              'block w-full truncate rounded-[8px] py-[6px] pr-2 text-left text-[12px]',
              selectedId === node.id ? 'bg-white font-medium text-[#18181a]' : 'text-[#464c5e] hover:bg-white',
            )}
            style={{ paddingLeft: 8 + depth * 14 }}
            aria-current={selectedId === node.id ? 'true' : undefined}
            onClick={() => onSelect(node.id)}
          >
            <span data-i18n-ignore>{node.name}</span>
          </button>
          {node.children.length > 0 ? (
            <DepartmentTree nodes={node.children} depth={depth + 1} selectedId={selectedId} onSelect={onSelect} />
          ) : null}
        </div>
      ))}
    </>
  );
}
