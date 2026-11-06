import { Suspense, lazy, useEffect, useRef, useState } from 'react';

import type { DashboardSpec } from '../dashboardTypes';

import DashboardCanvas from './DashboardCanvas';
import { gridOf, layoutOf, wallContentHeight } from './dashboardLayout';

const DashboardFrame = lazy(() => import('./DashboardFrame'));

/** Inline 1920×1080 preview used by the public share page. */
export default function DashboardWallView({ spec }: { spec: DashboardSpec }) {
  const layout = layoutOf(spec);
  const ref = useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(1440);

  useEffect(() => {
    const node = ref.current;
    if (!node) return undefined;
    const update = () => setWidth(node.clientWidth || 1440);
    update();
    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', update);
      return () => window.removeEventListener('resize', update);
    }
    const observer = new ResizeObserver(update);
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  if (!layout) {
    return <DashboardCanvas spec={spec} />;
  }

  const grid = gridOf(layout);
  const scale = Math.max(0.2, width / grid.width);
  const stageHeight = layout.overflow === 'scroll'
    ? Math.max(grid.height, wallContentHeight(layout) + 12)
    : grid.height;

  return (
    <div ref={ref} className="dash-public-wall" style={{ height: stageHeight * scale }}>
      <div
        style={{
          width: grid.width,
          height: stageHeight,
          transform: `scale(${scale})`,
          transformOrigin: 'top left',
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
  );
}
