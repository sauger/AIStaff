/**
 * Source: https://github.com/gcpaas/DataRoom (commit 1e04b9c)
 * Original: dataRoomFront/src/dataRoom/datav/components/BorderBox10/src/BorderBox10.tsx
 * License: Apache-2.0 (DataRoom), upstream DataV-Vue3 MIT
 * Modified for React: dynamic width/height via useAutoResize.
 */
import type { CSSProperties, ReactNode } from 'react';

import { mergedColor, useAutoResize } from './useAutoResize';

const CORNERS = ['left-top', 'right-top', 'left-bottom', 'right-bottom'] as const;
const DEFAULT_COLOR = ['#1d48c4', '#d3e1f8'];

type Props = {
  children?: ReactNode;
  className?: string;
  color?: string[];
  backgroundColor?: string;
};

export default function DvBorderBox10({ children, className = '', color, backgroundColor = 'transparent' }: Props) {
  const { ref, width, height } = useAutoResize<HTMLDivElement>();
  const [c0, c1] = mergedColor(DEFAULT_COLOR, color);

  return (
    <div
      ref={ref}
      className={`dv-border-box-10 ${className}`}
      style={{ boxShadow: `inset 0 0 25px 3px ${c0}` } as CSSProperties}
    >
      <svg className="dv-border-svg-container" width={width} height={height} aria-hidden="true">
        <polygon
          fill={backgroundColor}
          points={`4,0 ${width - 4},0 ${width},4 ${width},${height - 4} ${width - 4},${height} 4,${height} 0,${height - 4} 0,4`}
        />
      </svg>
      {CORNERS.map((item) => (
        <svg key={item} width="150" height="150" className={`${item} dv-border-svg-container`} aria-hidden="true">
          <polygon fill={c1} points="40,0 5,0 0,5 0,16 3,19 3,7 7,3 35,3" />
        </svg>
      ))}
      <div className="border-box-content">{children}</div>
    </div>
  );
}
