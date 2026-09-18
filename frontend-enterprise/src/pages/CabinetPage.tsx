import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';

import AppHeader from '@/components/AppHeader';
import CapabilityScopeLoading from '@/components/CapabilityScopeLoading';
import { ConfirmDialog } from '@/components/ConfirmDialog';
import { DataTable, type DataTableColumn } from '@/components/DataTable';
import {
  Dialog,
  DialogContent,
  DialogTitle,
  Input,
} from '@/components/ui';
import { Button as UIButton } from '@/components/ui/button';
import { notify } from '@/components/ui/app-toast';
import { cn } from '@/lib/utils';
import {
  DIALOG_CANCEL_BUTTON_CLASS,
  DIALOG_FOOTER_CLASS,
  DIALOG_PRIMARY_BUTTON_CLASS,
  formatDateTime,
  OUTLINE_ACTION_BUTTON_CLASS,
} from '@/lib/enterprise-ui';
import {
  ENTERPRISE_AGENT_STORAGE_KEY,
  isTeamScope,
  persistSharedAgentScope,
  readEmployeeScope,
} from '@/lib/agent-scope-storage';
import { api, ApiError, TENANT_ID } from '../api/client';
import IconFolder from '../assets/icons/cap-folder.svg?react';
import IconDownload from '../assets/icons/download.svg?react';
import IconEdit from '../assets/icons/edit.svg?react';
import IconRefresh from '../assets/icons/refresh.svg?react';
import IconTrash from '../assets/icons/trash.svg?react';
import IconUpload from '../assets/icons/upload.svg?react';
import { isEmployeeOwnedBy, type EnterpriseAuthUser } from '../auth';
import { visibleEmployeeAgents } from '../employee';
import type { AgentProfileRead, CabinetEntryRead, CabinetListResponse } from '../types';

const EMPTY_COPY = '先上传模板，再在对话里说以某某为模板生产';

const CABINET_ICON_BUTTON_CLASS =
  'size-[34px] shrink-0 rounded-[10px] border-[0.5px] border-[#e3e7f1] bg-white p-0 text-[#757f9c] hover:border-[#cbd3e6] hover:bg-white hover:text-[#18181a] focus-visible:border-[#18181a] focus-visible:ring-0 [&_svg]:size-[14px]';

const CABINET_ICON_PRIMARY_BUTTON_CLASS =
  'size-[34px] shrink-0 rounded-[10px] bg-[#18181a] p-0 text-white hover:bg-[#303030] focus-visible:ring-2 focus-visible:ring-[#18181a]/30 [&_svg]:size-[14px]';

function CabinetIconButton({
  label,
  onClick,
  disabled,
  children,
  variant = 'outline',
}: {
  label: string;
  onClick?: () => void;
  disabled?: boolean;
  children: ReactNode;
  variant?: 'outline' | 'primary';
}) {
  return (
    <UIButton
      type="button"
      variant={variant === 'primary' ? 'default' : 'outline'}
      size="icon"
      className={variant === 'primary' ? CABINET_ICON_PRIMARY_BUTTON_CLASS : CABINET_ICON_BUTTON_CLASS}
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={onClick}
    >
      {children}
    </UIButton>
  );
}

type CabinetPageProps = {
  currentUser?: EnterpriseAuthUser;
  onLogout?: () => void;
};

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error('读取文件失败'));
    reader.onload = () => {
      const result = String(reader.result || '');
      resolve(result.includes(',') ? result.split(',').pop() || '' : result);
    };
    reader.readAsDataURL(file);
  });
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function apiErrorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return '操作失败';
}

export default function CabinetPage({ currentUser, onLogout }: CabinetPageProps = {}) {
  const [agentId, setAgentId] = useState(readEmployeeScope);
  const [agents, setAgents] = useState<AgentProfileRead[]>([]);
  const [agentScopeLoaded, setAgentScopeLoaded] = useState(false);
  const [listing, setListing] = useState<CabinetListResponse | null>(null);
  const [path, setPath] = useState('');
  const [loading, setLoading] = useState(false);
  const [folderOpen, setFolderOpen] = useState(false);
  const [folderName, setFolderName] = useState('');
  const [renameTarget, setRenameTarget] = useState<CabinetEntryRead | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [deleteTarget, setDeleteTarget] = useState<CabinetEntryRead | null>(null);
  const [overwriteFile, setOverwriteFile] = useState<File | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const loadSeqRef = useRef(0);

  const currentAgent = useMemo(
    () => agents.find((item) => item.id === agentId) || null,
    [agents, agentId],
  );
  const canWrite = Boolean(currentAgent && isEmployeeOwnedBy(currentAgent, currentUser));
  const isRoot = path === '';
  const breadcrumbs = useMemo(() => {
    const parts = path ? path.split('/') : [];
    const crumbs = [{ label: '根目录', value: '' }];
    parts.forEach((part, index) => {
      crumbs.push({ label: part, value: parts.slice(0, index + 1).join('/') });
    });
    return crumbs;
  }, [path]);

  useEffect(() => {
    void loadAgents();
  }, []);

  useEffect(() => {
    const onScopeChange = (event: Event) => {
      const next = (event as CustomEvent<{ agentId?: string }>).detail?.agentId || '';
      if (!next || isTeamScope(next)) return;
      setAgentId(next);
      setPath('');
    };
    window.addEventListener('ultrarag-enterprise-agent-scope-change', onScopeChange);
    return () => window.removeEventListener('ultrarag-enterprise-agent-scope-change', onScopeChange);
  }, []);

  useEffect(() => {
    if (!agentScopeLoaded || !agentId || isTeamScope(agentId)) return;
    void loadFolder(path);
  }, [agentId, path, agentScopeLoaded]);

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

  async function loadFolder(nextPath: string) {
    if (!agentId || isTeamScope(agentId)) return;
    const seq = ++loadSeqRef.current;
    setLoading(true);
    try {
      const params = new URLSearchParams({
        tenant_id: TENANT_ID,
        agent_id: agentId,
        path: nextPath,
      });
      const result = await api.get<CabinetListResponse>(
        `/api/enterprise/cabinet?${params.toString()}`,
      );
      if (seq !== loadSeqRef.current) return;
      setListing(result);
    } catch (error) {
      if (seq !== loadSeqRef.current) return;
      notify.error(apiErrorMessage(error));
      setListing(null);
    } finally {
      if (seq === loadSeqRef.current) setLoading(false);
    }
  }

  async function createFolder() {
    const name = folderName.trim();
    if (!name || !agentId) return;
    try {
      await api.post(
        `/api/enterprise/cabinet/folders?agent_id=${encodeURIComponent(agentId)}`,
        { tenant_id: TENANT_ID, path, name },
      );
      notify.success('已新建文件夹');
      setFolderOpen(false);
      setFolderName('');
      await loadFolder(path);
    } catch (error) {
      notify.error(apiErrorMessage(error));
    }
  }

  async function uploadFile(file: File, overwrite = false) {
    if (!agentId) return;
    try {
      const contentBase64 = await fileToBase64(file);
      await api.post(
        `/api/enterprise/cabinet/files?agent_id=${encodeURIComponent(agentId)}`,
        {
          tenant_id: TENANT_ID,
          path,
          filename: file.name,
          content_base64: contentBase64,
          overwrite,
        },
      );
      notify.success(overwrite ? '已覆盖文件' : '已上传到文件柜');
      setOverwriteFile(null);
      await loadFolder(path);
    } catch (error) {
      if (!overwrite && error instanceof ApiError && error.code === 'CABINET_EXISTS') {
        setOverwriteFile(file);
        return;
      }
      notify.error(apiErrorMessage(error));
    }
  }

  async function downloadEntry(entry: CabinetEntryRead) {
    if (!agentId) return;
    try {
      const params = new URLSearchParams({
        tenant_id: TENANT_ID,
        agent_id: agentId,
        path: entry.path,
      });
      const blob = await api.blob(`/api/enterprise/cabinet/files/content?${params.toString()}`);
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = entry.name;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (error) {
      notify.error(apiErrorMessage(error));
    }
  }

  async function renameEntry() {
    if (!agentId || !renameTarget) return;
    const nextName = renameValue.trim();
    if (!nextName) return;
    const destination = path ? `${path}/${nextName}` : nextName;
    try {
      await api.patch(
        `/api/enterprise/cabinet/entries?agent_id=${encodeURIComponent(agentId)}`,
        {
          tenant_id: TENANT_ID,
          path: renameTarget.path,
          destination_path: destination,
        },
      );
      notify.success('已重命名');
      setRenameTarget(null);
      await loadFolder(path);
    } catch (error) {
      notify.error(apiErrorMessage(error));
    }
  }

  async function confirmDelete() {
    if (!agentId || !deleteTarget) return;
    try {
      const params = new URLSearchParams({
        tenant_id: TENANT_ID,
        agent_id: agentId,
        path: deleteTarget.path,
      });
      await api.delete(`/api/enterprise/cabinet/entries?${params.toString()}`);
      notify.success('已删除');
      setDeleteTarget(null);
      await loadFolder(path);
    } catch (error) {
      notify.error(apiErrorMessage(error));
    }
  }

  const columns: DataTableColumn<CabinetEntryRead>[] = [
    {
      key: 'name',
      title: '名称',
      render: (row) => (
        <button
          type="button"
          className="inline-flex min-w-0 items-center gap-[8px] text-left text-[13px] text-[#17191f]"
          onClick={() => {
            if (row.kind === 'folder') setPath(row.path);
            else void downloadEntry(row);
          }}
        >
          {row.kind === 'folder' ? <IconFolder className="size-[14px] shrink-0" /> : null}
          <span className="truncate">{row.name}</span>
        </button>
      ),
    },
    {
      key: 'kind',
      title: '类型',
      width: 90,
      render: (row) => (row.kind === 'folder' ? '文件夹' : '文件'),
    },
    {
      key: 'size',
      title: '大小',
      width: 110,
      render: (row) => (row.kind === 'file' ? formatSize(row.size_bytes) : '—'),
    },
    {
      key: 'updated',
      title: '更新时间',
      width: 180,
      render: (row) => formatDateTime(row.updated_at),
    },
    {
      key: 'actions',
      title: '操作',
      width: 160,
      render: (row) => (
        <div className="flex flex-nowrap items-center gap-[8px]">
          {row.kind === 'file' ? (
            <CabinetIconButton label="下载" onClick={() => void downloadEntry(row)}>
              <IconDownload aria-hidden="true" />
            </CabinetIconButton>
          ) : null}
          {canWrite ? (
            <>
              <CabinetIconButton
                label="重命名"
                onClick={() => {
                  setRenameTarget(row);
                  setRenameValue(row.name);
                }}
              >
                <IconEdit aria-hidden="true" />
              </CabinetIconButton>
              <CabinetIconButton label="删除" onClick={() => setDeleteTarget(row)}>
                <IconTrash aria-hidden="true" />
              </CabinetIconButton>
            </>
          ) : null}
        </div>
      ),
    },
  ];

  if (!agentScopeLoaded) return <CapabilityScopeLoading />;

  const entries = listing?.entries || [];
  const empty = !loading && entries.length === 0;

  return (
    <div className="min-h-full box-border px-[48px] pt-[32px] pb-[43px] max-[900px]:px-[16px]">
      <AppHeader
        onLogout={onLogout}
        userName={currentUser?.username}
        title="文件柜"
        description="浏览、上传当前员工的工作文件。在对话里可以说「以某某为模板生产」。"
      />

      {currentAgent && !canWrite ? (
        <p className="mt-[16px] rounded-[10px] bg-[#f6f7fa] px-[12px] py-[8px] text-[12px] text-[#697085]">
          管理员正在查阅其他员工的文件柜，只能浏览和下载。
        </p>
      ) : null}

      <div className="mt-[20px] mb-[16px] flex flex-wrap items-center justify-between gap-[12px]">
        <nav className="flex min-w-0 flex-wrap items-center gap-[6px] text-[12px] text-[#697085]">
          {breadcrumbs.map((crumb, index) => (
            <span key={`${crumb.value}-${index}`} className="inline-flex items-center gap-[6px]">
              {index > 0 ? <span>/</span> : null}
              <button
                type="button"
                className={cn(
                  'rounded-[6px] px-[4px] py-[2px]',
                  crumb.value === path ? 'font-medium text-[#17191f]' : 'hover:bg-[#f3f5f8]',
                )}
                onClick={() => setPath(crumb.value)}
              >
                {crumb.label}
              </button>
            </span>
          ))}
        </nav>
        <div className="flex shrink-0 flex-nowrap items-center gap-[8px]">
          <UIButton
            variant="outline"
            className={OUTLINE_ACTION_BUTTON_CLASS}
            disabled={loading}
            onClick={() => void loadFolder(path)}
          >
            <IconRefresh className={cn('size-[14px]', loading && 'animate-spin')} />
            刷新
          </UIButton>
          {canWrite ? (
            <>
              <CabinetIconButton label="新建文件夹" onClick={() => setFolderOpen(true)}>
                <IconFolder aria-hidden="true" />
              </CabinetIconButton>
              <CabinetIconButton
                label="上传"
                variant="primary"
                onClick={() => fileInputRef.current?.click()}
              >
                <IconUpload aria-hidden="true" />
              </CabinetIconButton>
            </>
          ) : null}
          <input
            ref={fileInputRef}
            type="file"
            className="hidden"
            onChange={(event) => {
              const file = event.target.files?.[0];
              event.target.value = '';
              if (file) void uploadFile(file);
            }}
          />
        </div>
      </div>

      {empty ? (
        <div className="flex min-h-[240px] flex-col items-center justify-center rounded-[16px] border border-dashed border-[#e3e7f1] bg-white px-[24px] text-center">
          <p className="m-0 text-[14px] font-medium text-[#17191f]">{EMPTY_COPY}</p>
          {isRoot ? (
            <p className="mt-[8px] m-0 text-[12px] text-[#858b9c]">
              文件柜是这个数字员工的工作盘，不会进入知识库。
            </p>
          ) : null}
        </div>
      ) : (
        <DataTable
          columns={columns}
          data={entries}
          rowKey={(row) => row.id}
          loading={loading}
          emptyText={EMPTY_COPY}
        />
      )}

      <Dialog open={folderOpen} onOpenChange={setFolderOpen}>
        <DialogContent>
          <DialogTitle>新建文件夹</DialogTitle>
          <Input
            value={folderName}
            onChange={(event) => setFolderName(event.target.value)}
            placeholder="文件夹名称"
          />
          <div className={DIALOG_FOOTER_CLASS}>
            <UIButton variant="outline" className={DIALOG_CANCEL_BUTTON_CLASS} onClick={() => setFolderOpen(false)}>
              取消
            </UIButton>
            <UIButton className={DIALOG_PRIMARY_BUTTON_CLASS} onClick={() => void createFolder()}>
              创建
            </UIButton>
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={Boolean(renameTarget)} onOpenChange={(open) => !open && setRenameTarget(null)}>
        <DialogContent>
          <DialogTitle>重命名</DialogTitle>
          <Input
            value={renameValue}
            onChange={(event) => setRenameValue(event.target.value)}
            placeholder="新名称"
          />
          <div className={DIALOG_FOOTER_CLASS}>
            <UIButton variant="outline" className={DIALOG_CANCEL_BUTTON_CLASS} onClick={() => setRenameTarget(null)}>
              取消
            </UIButton>
            <UIButton className={DIALOG_PRIMARY_BUTTON_CLASS} onClick={() => void renameEntry()}>
              保存
            </UIButton>
          </div>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={Boolean(deleteTarget)}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
        title={`删除「${deleteTarget?.name || ''}」？`}
        description="删除后无法从文件柜恢复，也没有版本可回滚。"
        onConfirm={() => void confirmDelete()}
      />
      <ConfirmDialog
        open={Boolean(overwriteFile)}
        onOpenChange={(open) => !open && setOverwriteFile(null)}
        title="同名文件已存在，是否覆盖？"
        description={overwriteFile ? `覆盖后「${overwriteFile.name}」只保留最新内容。` : undefined}
        confirmText="覆盖"
        onConfirm={() => overwriteFile && void uploadFile(overwriteFile, true)}
      />
    </div>
  );
}
