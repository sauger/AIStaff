import { Suspense, lazy, useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';

import type { DashboardSpec } from '../dashboardTypes';

import DashboardCanvas from './DashboardCanvas';
import { gridOf, layoutOf, stageGeometry } from './dashboardLayout';

const DashboardFrame = lazy(() => import('./DashboardFrame'));

type DashboardStageProps = {
  spec: DashboardSpec;
  onClose: () => void;
};

export default function DashboardStage({ spec, onClose }: DashboardStageProps) {
  const layout = useMemo(() => layoutOf(spec), [spec]);
  const [viewport, setViewport] = useState(() => ({
    width: typeof window === 'undefined' ? 1920 : window.innerWidth,
    height: typeof window === 'undefined' ? 1080 : window.innerHeight,
  }));

  useEffect(() => {
    const onResize = () => setViewport({ width: window.innerWidth, height: window.innerHeight });
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  useEffect(() => {
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = previous;
    };
  }, []);

  if (!layout) return null;

  const grid = gridOf(layout);
  const geometry = stageGeometry(viewport.width, viewport.height, layout);
  const stageWidth = grid.width * geometry.scale;
  const stageHeight = geometry.stageHeight * geometry.scale;

  return createPortal(
    <div
      className="dash-stage-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label="数据大屏全屏"
    >
      <div
        className="dash-stage-scroll"
        style={{ overflowY: geometry.scroll ? 'auto' : 'hidden' }}
      >
        <div
          className="dash-stage-spacer"
          style={{ width: stageWidth, height: stageHeight }}
          aria-hidden="true"
        />
        <div
          className="dash-stage-canvas"
          style={{
            width: grid.width,
            height: geometry.stageHeight,
            transform: `translate(${geometry.offsetX}px, ${geometry.offsetY}px) scale(${geometry.scale})`,
          }}
        >
          {spec.theme === 'tech' ? (
            <Suspense fallback={null}>
              <DashboardFrame>
                <DashboardCanvas spec={spec} mode="wall" />
              </DashboardFrame>
            </Suspense>
          ) : (
            <DashboardCanvas spec={spec} mode="wall" />
          )}
        </div>
      </div>
      <button
        type="button"
        className="dash-stage-close"
        aria-label="退出全屏"
        onClick={onClose}
      >
        ✕
      </button>
    </div>,
    document.body,
  );
}
