import { ReloadOutlined } from '../icons';
import { useEffect, useState } from 'react';
import {
  Button as UIButton,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  notify,
} from '@/components/ui';
import { DataTable, type DataTableColumn } from '@/components/DataTable';
import { Paginator } from '@/components/Paginator';
import { useClientPagination } from '../hooks/useClientPagination';
import { api, TENANT_ID } from '../api/client';
import type { TraceSummary } from '../types';

const TRACES_PAGE_SIZE = 10;

type TracesPageProps = {
  agentId?: string;
  agentName?: string;
};

export default function TracesPage({ agentId, agentName }: TracesPageProps = {}) {
  const [rows, setRows] = useState<TraceSummary[]>([]);
  const [detail, setDetail] = useState<Record<string, unknown> | null>(null);

  const scopeQuery = agentId ? `&agent_id=${encodeURIComponent(agentId)}` : '';
  const load = () =>
    api
      .get<TraceSummary[]>(`/api/enterprise/traces?tenant_id=${TENANT_ID}${scopeQuery}`)
      .then(setRows)
      .catch((error) => notify.error(error.message));

  useEffect(() => {
    void load();
  }, [agentId]);

  const pagination = useClientPagination(rows, TRACES_PAGE_SIZE);

  async function openDetail(row: TraceSummary) {
    const result = await api.get<Record<string, unknown>>(`/api/enterprise/traces/${row.session_id}?tenant_id=${TENANT_ID}`);
    setDetail(result);
  }

  const truncateCell = 'block truncate';
  const columns: DataTableColumn<TraceSummary>[] = [
    {
      key: 'last_message',
      title: '最近消息',
      width: 360,
      render: (row) => (
        <span className={truncateCell} title={row.last_message || row.title || row.session_id}>
          {row.last_message || row.title || '（无消息）'}
        </span>
      ),
    },
    {
      key: 'active_skill_id',
      title: '命中 SOP',
      width: 170,
      render: (row) => {
        const decisionSkill = typeof row.last_decision?.target_skill_id === 'string' ? row.last_decision.target_skill_id : '';
        return <span className={truncateCell} title={decisionSkill || undefined}>{row.active_skill_id || decisionSkill || '-'}</span>;
      },
    },
    {
      key: 'active_step_id',
      title: '当前步骤',
      width: 130,
      render: (row) => {
        const decisionStep = typeof row.last_decision?.target_step_id === 'string' ? row.last_decision.target_step_id : '';
        return <span className={truncateCell} title={decisionStep || undefined}>{row.active_step_id || decisionStep || '-'}</span>;
      },
    },
    { key: 'tool_call_count', title: '工具调用', width: 92, render: (row) => row.tool_call_count },
    { key: 'status', title: '状态', width: 92, render: (row) => row.status },
    { key: 'updated_at', title: '更新时间', width: 200, render: (row) => <span className={truncateCell}>{row.updated_at}</span> },
    {
      key: 'actions',
      title: '操作',
      width: 92,
      render: (row) => (
        <UIButton
          variant="outline"
          size="sm"
          className="h-[28px] rounded-[8px] px-[12px] text-[12px]"
          onClick={() => void openDetail(row)}
        >
          查看
        </UIButton>
      ),
    },
  ];

  const scopedTitle = agentName ? `「${agentName}」的运行轨迹` : '我的会话运行轨迹';
  const emptyText = agentName
    ? `「${agentName}」还没有运行记录：先在工作台和它对话，或从渠道/开放 API 发一条消息。`
    : '暂无运行记录';

  return (
    <>
      <div className="page-title">
        <h3>运行轨迹</h3>
        <UIButton variant="outline" onClick={() => void load()}>
          <ReloadOutlined />
          刷新
        </UIButton>
      </div>
      <Card className="data-card">
        <CardHeader>
          <CardTitle>{scopedTitle}</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-[16px]">
          <p className="m-0 text-[12px] leading-[20px] text-muted-foreground">
            每行是一次会话：最近消息、命中的 SOP 与步骤、工具调用次数、会话状态。仅列出你本人发起的会话；其他成员的会话在
            「对话日志」中按权限查看。
          </p>
          <div className="overflow-x-auto">
            <DataTable
              aria-label="会话运行轨迹"
              columns={columns}
              data={pagination.pagedItems}
              rowKey={(row) => row.session_id}
              className="min-w-[1136px]"
              emptyText={emptyText}
            />
          </div>
          {rows.length > 0 && (
            <Paginator
              aria-label="运行轨迹分页"
              className="mt-0"
              page={pagination.page}
              pageCount={pagination.pageCount}
              onChange={pagination.setPage}
            />
          )}
        </CardContent>
      </Card>
      <Sheet open={Boolean(detail)} onOpenChange={(next) => { if (!next) setDetail(null); }}>
        <SheetContent side="right" className="w-[720px] sm:max-w-[720px]">
          <SheetHeader>
            <SheetTitle>运行轨迹详情</SheetTitle>
          </SheetHeader>
          <div className="min-h-0 flex-1 overflow-auto px-[16px] pb-[16px]">
            <pre className="text-[12px] whitespace-pre-wrap">{JSON.stringify(detail, null, 2)}</pre>
          </div>
        </SheetContent>
      </Sheet>
    </>
  );
}
