import { useEffect, useState } from 'react';

import { ApiError, api } from '@/api/client';
import StaffdeckIcon from '@/components/StaffdeckIcon';

import type { DashboardSpec, DashboardViewDescriptor, DashboardViewPayload } from '../dashboardTypes';

import DashboardCanvas from './DashboardCanvas';

type DashboardViewProps = {
  descriptor: DashboardViewDescriptor;
};

export default function DashboardView({ descriptor }: DashboardViewProps) {
  const [spec, setSpec] = useState<DashboardSpec | null>(null);
  const [error, setError] = useState('');
  const [expired, setExpired] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let active = true;
    setError('');
    setExpired(false);
    const query = new URLSearchParams({ tenant_id: descriptor.tenant_id || '' });
    const path = `/api/chat/dashboard-views/${encodeURIComponent(descriptor.view_id)}?${query.toString()}`;
    api
      .get<DashboardViewPayload>(path)
      .then((payload) => {
        if (!active) return;
        setSpec(payload.spec);
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

  if (!spec) {
    return <div className="mt-2 text-xs text-[#858b9c]">正在加载大屏…</div>;
  }

  return (
    <section className="mt-2" aria-label="数据大屏">
      <DashboardCanvas spec={spec} />
    </section>
  );
}
