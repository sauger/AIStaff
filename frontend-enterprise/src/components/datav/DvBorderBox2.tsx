/**
 * Source: https://github.com/gcpaas/DataRoom (commit 1e04b9c)
 * Original: dataRoomFront/src/dataRoom/datav/components/BorderBox2/src/BorderBox2.tsx
 * License: Apache-2.0 (DataRoom), upstream DataV-Vue3 MIT
 * Modified for React: no SMIL; geometry kept verbatim.
 */
import type { ReactNode } from 'react';

import { mergedColor, useAutoResize } from './useAutoResize';

const DEFAULT_COLOR = ['#fff', 'rgba(255, 255, 255, 0.6)'];

type Props = {
  children?: ReactNode;
  className?: string;
  color?: string[];
  backgroundColor?: string;
};

export default function DvBorderBox2({ children, className = '', color, backgroundColor = 'transparent' }: Props) {
  const { ref, width, height } = useAutoResize<HTMLDivElement>();
  const [c0, c1] = mergedColor(DEFAULT_COLOR, color);

  return (
    <div ref={ref} className={`dv-border-box-2 ${className}`}>
      <svg className="dv-border-svg-container" width={width} height={height} aria-hidden="true">
        <polygon fill={backgroundColor} points={`7,7 ${width - 7},7 ${width - 7},${height - 7} 7,${height - 7}`} />
        <polyline
          fill="none"
          stroke={c0}
          strokeWidth="1"
          points={`2,2 ${width - 2},2 ${width - 2},${height - 2} 2,${height - 2} 2,2`}
        />
        <polyline
          fill="none"
          stroke={c1}
          strokeWidth="1"
          points={`6,6 ${width - 6},6 ${width - 6},${height - 6} 6,${height - 6} 6,6`}
        />
        <circle fill={c0} cx="11" cy="11" r="1" />
        <circle fill={c0} cx={width - 11} cy="11" r="1" />
        <circle fill={c0} cx={width - 11} cy={height - 11} r="1" />
        <circle fill={c0} cx="11" cy={height - 11} r="1" />
      </svg>
      <div className="border-box-content">{children}</div>
    </div>
  );
}
