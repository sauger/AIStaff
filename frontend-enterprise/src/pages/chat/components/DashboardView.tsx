import { useEffect, useState } from 'react';

import { ApiError, api } from '@/api/client';
import StaffdeckIcon from '@/components/StaffdeckIcon';
import { notify } from '@/components/ui/app-toast';

import type { DashboardViewDescriptor, DashboardViewPayload } from '../dashboardTypes';

import DashboardCanvas from './DashboardCanvas';
import DashboardStage from './DashboardStage';
import { layoutOf } from './dashboardLayout';

type DashboardViewProps = {
  descriptor: DashboardViewDescriptor;
};

function fallbackCopy(value: string): boolean {
  try {
    const area = document.createElement('textarea');
    area.value = value;
    area.setAttribute('readonly', '');
    area.style.position = 'fixed';
    area.style.opacity = '0';
    document.body.appendChild(area);
    area.select();
    const copied = document.execCommand('copy');
    document.body.removeChild(area);
    return copied;
  } catch {
    return false;
  }
}

export default function DashboardView({ descriptor }: DashboardViewProps) {
  const [payload, setPayload] = useState<DashboardViewPayload | null>(null);
  const [error, setError] = useState('');
  const [expired, setExpired] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const [stageOpen, setStageOpen] = useState(false);
  const [manualCopy, setManualCopy] = useState('');

  useEffect(() => {
    let active = true;
    setError('');
    setExpired(false);
    const query = new URLSearchParams({ tenant_id: descriptor.tenant_id || '' });
    const path = `/api/chat/dashboard-views/${encodeURIComponent(descriptor.view_id)}?${query.toString()}`;
    api
      .get<DashboardViewPayload>(path)
      .then((next) => {
        if (!active) return;
        setPayload(next);
      })
      .catch((reason: unknown) => {
        if (!active) return;
        if (reason instanceof ApiError && reason.status === 410) {
          setExpired(true);
          return;
        }
        setError(reason instanceof Error ? reason.message : '大屏加载失败');
      });
    return () => {
      active = false;
    };
  }, [descriptor.view_id, descriptor.tenant_id, reloadKey]);

  if (expired) {
    return (
      <div className="mt-2 rounded-lg border border-[#eceef1] bg-[#fafbfc] px-3 py-2 text-xs text-[#858b9c]">
        该大屏已过期，可让智能体重新生成。
      </div>
    );
  }

  if (error) {
    return (
      <div className="mt-2 flex items-start gap-2 rounded-lg border border-[#eceef1] bg-[#fafbfc] px-3 py-2 text-xs text-[#858b9c]">
        <StaffdeckIcon name="warning" size={14} />
        <span className="flex-1">大屏加载失败：{error}</span>
        <button
          type="button"
          className="shrink-0 rounded-md border border-[#dfe5e2] px-2 py-0.5 text-[11px] text-[#3f4653]"
          onClick={() => setReloadKey((value) => value + 1)}
        >
          重试
        </button>
      </div>
    );
  }

  if (!payload) {
    return <div className="mt-2 text-xs text-[#858b9c]">正在加载大屏…</div>;
  }

  const isV2 = layoutOf(payload.spec) !== null;
  const canShare = Boolean(payload.share_url);

  async function copyLink() {
    const url = payload?.share_url;
    if (!url) return;
    let copied = false;
    try {
      await navigator.clipboard.writeText(url);
      copied = true;
    } catch {
      copied = fallbackCopy(url);
    }
    if (!copied) {
      setManualCopy(url);
      notify.error('复制失败，请手动复制链接');
      return;
    }
    setManualCopy('');
    notify.success('链接已复制，7 天内有效');
    if (payload?.share_scope === 'local') {
      notify.info('当前为本机地址，仅本机或局域网可打开');
    }
  }

  return (
    <section className="mt-2" aria-label="数据大屏">
      {(isV2 || canShare) ? (
        <div className="mb-1 flex justify-end gap-2" data-testid="dashboard-toolbar">
          {canShare ? (
            <button
              type="button"
              className="rounded-md border border-[#dfe5e2] px-2 py-0.5 text-[11px] text-[#3f4653]"
              onClick={() => void copyLink()}
            >
              复制链接
            </button>
          ) : null}
          {isV2 ? (
            <button
              type="button"
              className="rounded-md border border-[#dfe5e2] px-2 py-0.5 text-[11px] text-[#3f4653]"
              onClick={() => setStageOpen(true)}
            >
              全屏
            </button>
          ) : null}
        </div>
      ) : null}
      {manualCopy ? (
        <div className="mb-1 flex items-center gap-2 rounded-md border border-[#f0c36b] bg-[#fff8e8] px-2 py-1 text-[11px] text-[#7a5b16]">
          <input
            readOnly
            value={manualCopy}
            aria-label="大屏链接"
            className="min-w-0 flex-1 bg-transparent"
            onFocus={(event) => event.currentTarget.select()}
          />
          <span className="shrink-0">请手动复制链接</span>
        </div>
      ) : null}
      <DashboardCanvas spec={payload.spec} />
      {stageOpen ? <DashboardStage spec={payload.spec} onClose={() => setStageOpen(false)} /> : null}
    </section>
  );
}
