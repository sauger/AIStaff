import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';

import { ApiError, api } from '@/api/client';

import type { PublicDashboardPayload } from './chat/dashboardTypes';

import DashboardCanvas from './chat/components/DashboardCanvas';
import DashboardStage from './chat/components/DashboardStage';
import { layoutOf } from './chat/components/dashboardLayout';

type LoadState = 'loading' | 'ready' | 'invalid' | 'error';

export default function PublicDashboardPage() {
  const { token = '' } = useParams<{ token: string }>();
  const [state, setState] = useState<LoadState>('loading');
  const [payload, setPayload] = useState<PublicDashboardPayload | null>(null);
  const [error, setError] = useState('');
  const [reloadKey, setReloadKey] = useState(0);
  const [stageOpen, setStageOpen] = useState(false);

  useEffect(() => {
    let active = true;
    setState('loading');
    setError('');
    api
      .get<PublicDashboardPayload>(`/api/public/dashboards/${encodeURIComponent(token)}`)
      .then((next) => {
        if (!active) return;
        setPayload(next);
        setState('ready');
      })
      .catch((reason: unknown) => {
        if (!active) return;
        if (reason instanceof ApiError && (reason.status === 404 || reason.status === 410)) {
          setState('invalid');
          return;
        }
        setError(reason instanceof Error ? reason.message : '大屏加载失败');
        setState('error');
      });
    return () => {
      active = false;
    };
  }, [token, reloadKey]);

  if (state === 'loading') {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[#080f1c] text-sm text-slate-300">
        正在加载大屏…
      </div>
    );
  }

  if (state === 'invalid') {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-2 bg-[#080f1c] px-6 text-center text-slate-300">
        <p className="text-base font-medium">链接已失效</p>
        <p className="text-xs text-slate-500">该大屏可能已过期，请联系分享者重新生成。</p>
      </div>
    );
  }

  if (state === 'error' || !payload) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-3 bg-[#080f1c] px-6 text-center text-slate-300">
        <p className="text-sm">大屏加载失败：{error}</p>
        <button
          type="button"
          className="rounded-md border border-slate-600 px-3 py-1 text-xs text-slate-200"
          onClick={() => setReloadKey((value) => value + 1)}
        >
          重试
        </button>
      </div>
    );
  }

  const isV2 = layoutOf(payload.spec) !== null;

  return (
    <div className="min-h-screen bg-[#080f1c] px-4 py-5 text-slate-100 sm:px-8">
      <header className="mb-3 flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium">{payload.title}</p>
          <p className="text-[11px] text-slate-500">只读分享 · 有效期至 {payload.expires_at.slice(0, 10)}</p>
        </div>
        {isV2 ? (
          <button
            type="button"
            className="shrink-0 rounded-md border border-cyan-400/30 px-3 py-1 text-xs text-cyan-200"
            onClick={() => setStageOpen(true)}
          >
            全屏
          </button>
        ) : null}
      </header>
      <DashboardCanvas spec={payload.spec} />
      {stageOpen ? <DashboardStage spec={payload.spec} onClose={() => setStageOpen(false)} /> : null}
    </div>
  );
}
