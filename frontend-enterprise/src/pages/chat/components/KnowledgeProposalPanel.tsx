import { useEffect, useState } from 'react';

import { api } from '@/api/client';
import { notify } from '@/components/ui/app-toast';
import type { KnowledgeIngestJobRead, KnowledgeProposalRead } from '@/types';

type KnowledgeProposalPanelProps = {
  tenantId: string;
  sessionId: string;
};

export default function KnowledgeProposalPanel({
  tenantId,
  sessionId,
}: KnowledgeProposalPanelProps) {
  const [proposals, setProposals] = useState<KnowledgeProposalRead[]>([]);
  const [confirming, setConfirming] = useState('');
  const [submitted, setSubmitted] = useState<Record<string, { proposal: KnowledgeProposalRead; job: KnowledgeIngestJobRead }>>({});

  useEffect(() => {
    if (!tenantId || !sessionId) {
      setProposals([]);
      return;
    }
    let cancelled = false;
    async function load() {
      try {
        const rows = await api.get<KnowledgeProposalRead[]>(
          `/api/chat/sessions/${encodeURIComponent(sessionId)}/knowledge-proposals`
            + `?tenant_id=${encodeURIComponent(tenantId)}`,
        );
        if (!cancelled) setProposals(rows);
      } catch {
        // A proposal is an enhancement; do not interrupt the chat with a toast.
      }
    }
    void load();
    const timer = window.setInterval(() => void load(), 4000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [sessionId, tenantId]);

  useEffect(() => {
    const entries = Object.entries(submitted).filter(
      ([, item]) => !['succeeded', 'failed', 'cancelled'].includes(item.job.status),
    );
    if (entries.length === 0) return;
    let cancelled = false;
    const timer = window.setInterval(() => {
      entries.forEach(([proposalId, item]) => {
        void api
          .get<KnowledgeIngestJobRead>(
            `/api/enterprise/knowledge/jobs/${encodeURIComponent(item.job.id)}`
              + `?tenant_id=${encodeURIComponent(tenantId)}`,
          )
          .then((job) => {
            if (!cancelled) {
              setSubmitted((current) => ({
                ...current,
                [proposalId]: { ...current[proposalId], job },
              }));
            }
          })
          .catch(() => undefined);
      });
    }, 2500);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [submitted, tenantId]);

  if (proposals.length === 0 && Object.keys(submitted).length === 0) return null;

  async function confirm(
    proposal: KnowledgeProposalRead,
    action: 'create' | 'replace',
    candidate?: KnowledgeProposalRead['candidates'][number],
  ) {
    const confirmKey = candidate ? `${proposal.id}:${candidate.document_id}` : proposal.id;
    setConfirming(confirmKey);
    try {
      const response = await api.post<{ job_id: string }>(
        `/api/chat/sessions/${encodeURIComponent(sessionId)}/knowledge-proposals/`
          + `${encodeURIComponent(proposal.id)}/confirm?tenant_id=${encodeURIComponent(tenantId)}`,
        {
          action,
          target_document_id: candidate?.document_id,
          expected_updated_at: candidate?.expected_updated_at,
        },
      );
      setProposals((current) => current.filter((item) => item.id !== proposal.id));
      setSubmitted((current) => ({
        ...current,
        [proposal.id]: {
          proposal,
          job: {
            id: response.job_id,
            tenant_id: tenantId,
            knowledge_base_id: proposal.target_knowledge_base_id,
            status: 'queued',
            stage: 'queued',
            progress: 0,
            filename: proposal.filename,
            metadata: {},
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
          },
        },
      }));
      notify.success(action === 'replace' ? '已提交覆盖，处理进度会显示在这里' : '已提交知识库入库，处理进度会显示在这里');
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '知识库入库确认失败');
    } finally {
      setConfirming('');
    }
  }

  return (
    <div className="mx-[auto] grid w-full max-w-[760px] gap-[10px] px-[24px] pb-[10px]">
      {proposals.map((proposal) => (
        <section
          className="grid gap-[10px] rounded-[12px] border border-[#dce4f2] bg-[#f8faff] p-[14px]"
          key={proposal.id}
        >
          <div className="grid gap-[3px]">
            <div className="text-[12px] font-semibold text-[#464c5e]">知识库更新提案</div>
            <div className="text-[13px] text-[#18181a]">
              {proposal.filename}
              <span className="text-[#858b9c]"> → {proposal.target_knowledge_base_name}</span>
            </div>
            <div className="text-[11px] text-[#858b9c]">
              {proposal.proposed_action === 'create'
                ? '识别为新增文档，确认后开始入库。'
                : '发现可能相关的已有文档，请确认新增；覆盖流程将在下一步开放。'}
            </div>
          </div>
          {proposal.candidates.length > 0 && (
            <div className="grid gap-[4px] rounded-[8px] border border-[#e8edf7] bg-white p-[9px]">
              <div className="text-[11px] font-medium text-[#757f9c]">可能相关的文档</div>
              {proposal.candidates.map((candidate) => (
                <div className="flex items-center justify-between gap-[12px] text-[12px]" key={candidate.document_id}>
                  <span className="truncate text-[#464c5e]">{candidate.title}</span>
                  <span className="flex shrink-0 items-center gap-[8px] text-[11px] text-[#858b9c]">
                    {candidate.updated_at.slice(0, 10)}
                    <button
                      className="rounded-[7px] border border-[#dce4f2] bg-white px-[8px] py-[4px] text-[11px] text-[#464c5e] disabled:opacity-60"
                      type="button"
                      disabled={confirming === `${proposal.id}:${candidate.document_id}`}
                      onClick={() => void confirm(proposal, 'replace', candidate)}
                    >
                      覆盖
                    </button>
                  </span>
                </div>
              ))}
            </div>
          )}
          <div className="flex items-center gap-[8px]">
            <button
              className="rounded-[8px] bg-[#18181a] px-[12px] py-[7px] text-[12px] font-medium text-white disabled:opacity-60"
              type="button"
              disabled={confirming === proposal.id}
              onClick={() => void confirm(proposal, 'create')}
            >
              {confirming === proposal.id
                ? '提交中…'
                : proposal.candidates.length > 0
                  ? '另存为新增'
                  : '确认新增'}
            </button>
            <span className="text-[11px] text-[#a2a8b8]">
              操作 {proposal.id.slice(-8)} · 30 分钟内有效
            </span>
          </div>
        </section>
      ))}
      {Object.entries(submitted).map(([proposalId, item]) => (
        <section
          className="grid gap-[6px] rounded-[12px] border border-[#e3e7f1] bg-white p-[14px]"
          key={proposalId}
        >
          <div className="text-[12px] font-semibold text-[#464c5e]">
            知识库入库 · {item.proposal.filename}
          </div>
          <div className="text-[12px] text-[#757f9c]">
            {item.job.status === 'succeeded'
              ? '已完成，现在可以通过自然语言检索。'
              : item.job.status === 'failed'
                ? `入库失败：${item.job.error || '请查看知识库任务'}`
                : `处理中：${item.job.stage || item.job.status}（${Math.round((item.job.progress || 0) * 100)}%）`}
          </div>
        </section>
      ))}
    </div>
  );
}
