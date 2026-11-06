/**
 * Source: https://github.com/gcpaas/DataRoom (commit 1e04b9c)
 * Original: dataRoomFront/src/dataRoom/datav/components/Decoration5/src/index.vue
 * License: Apache-2.0 (DataRoom), upstream DataV-Vue3 MIT
 * Modified for React: SMIL stroke animation replaced with CSS stroke-dashoffset.
 */
import { useMemo } from 'react';
import type { CSSProperties } from 'react';

import { mergedColor, useAutoResize } from './useAutoResize';

const DEFAULT_COLOR = ['#3f96a5', '#3f96a5'];

type Point = { x: number; y: number };

function pointsToString(points: Point[]): string {
  return points.map((point) => `${point.x},${point.y}`).join(' ');
}

function polylineLength(points: Point[]): number {
  let total = 0;
  for (let index = 1; index < points.length; index += 1) {
    total += Math.hypot(points[index].x - points[index - 1].x, points[index].y - points[index - 1].y);
  }
  return Math.round(total * 100) / 100;
}

type Props = {
  className?: string;
  color?: string[];
  dur?: number;
};

export default function DvDecoration5({ className = '', color, dur = 1.2 }: Props) {
  const { ref, width, height } = useAutoResize<HTMLDivElement>();
  const [c0, c1] = mergedColor(DEFAULT_COLOR, color);

  const { line1, line2, length1, length2 } = useMemo(() => {
    const w = width || 300;
    const h = height || 40;
    const first: Point[] = [
      { x: 0, y: h * 0.2 }, { x: w * 0.18, y: h * 0.2 },
      { x: w * 0.2, y: h * 0.4 }, { x: w * 0.25, y: h * 0.4 },
      { x: w * 0.27, y: h * 0.6 }, { x: w * 0.72, y: h * 0.6 },
      { x: w * 0.75, y: h * 0.4 }, { x: w * 0.8, y: h * 0.4 },
      { x: w * 0.82, y: h * 0.2 }, { x: w, y: h * 0.2 },
    ];
    const second: Point[] = [
      { x: w * 0.3, y: h * 0.8 }, { x: w * 0.7, y: h * 0.8 },
    ];
    return {
      line1: pointsToString(first),
      line2: pointsToString(second),
      length1: polylineLength(first),
      length2: polylineLength(second),
    };
  }, [width, height]);

  return (
    <div
      ref={ref}
      className={`dv-decoration-5 ${className}`}
      style={{ '--dv-dur': `${dur}s`, '--dv-len1': length1, '--dv-len2': length2 } as CSSProperties}
      aria-hidden="true"
    >
      <svg width={width} height={height}>
        <polyline
          className="dv-d5-line dv-d5-line1"
          fill="transparent"
          stroke={c0}
          strokeWidth="3"
          points={line1}
          strokeDasharray={`${length1 / 2} ${length1 / 2}`}
        />
        <polyline
          className="dv-d5-line dv-d5-line2"
          fill="transparent"
          stroke={c1}
          strokeWidth="2"
          points={line2}
          strokeDasharray={`${length2 / 2} ${length2 / 2}`}
        />
      </svg>
    </div>
  );
}
