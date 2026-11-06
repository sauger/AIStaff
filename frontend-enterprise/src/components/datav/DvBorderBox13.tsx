/**
 * Source: https://github.com/gcpaas/DataRoom (commit 1e04b9c)
 * Original: dataRoomFront/src/dataRoom/datav/components/BorderBox13/src/BorderBox13.tsx
 * License: Apache-2.0 (DataRoom), upstream DataV-Vue3 MIT
 * Modified for React: JSX attributes normalized; geometry kept verbatim.
 */
import type { ReactNode } from 'react';

import { mergedColor, useAutoResize } from './useAutoResize';

const DEFAULT_COLOR = ['#6586ec', '#2cf7fe'];

type Props = {
  children?: ReactNode;
  className?: string;
  color?: string[];
  backgroundColor?: string;
};

export default function DvBorderBox13({ children, className = '', color, backgroundColor = 'transparent' }: Props) {
  const { ref, width, height } = useAutoResize<HTMLDivElement>();
  const [c0, c1] = mergedColor(DEFAULT_COLOR, color);

  return (
    <div ref={ref} className={`dv-border-box-13 ${className}`}>
      <svg className="dv-border-svg-container" width={width} height={height} aria-hidden="true">
        <path
          fill={backgroundColor}
          stroke={c0}
          d={`M 5 20 L 5 10 L 12 3 L 60 3 L 68 10 L ${width - 20} 10 L ${width - 5} 25 L ${width - 5} ${height - 5} L 20 ${height - 5} L 5 ${height - 20} L 5 20`}
        />
        <path
          fill="transparent"
          strokeWidth="3"
          strokeLinecap="round"
          strokeDasharray="10, 5"
          stroke={c0}
          d="M 16 9 L 61 9"
        />
        <path fill="transparent" stroke={c1} d="M 5 20 L 5 10 L 12 3 L 60 3 L 68 10" />
        <path fill="transparent" stroke={c1} d={`M ${width - 5} ${height - 30} L ${width - 5} ${height - 5} L ${width - 30} ${height - 5}`} />
      </svg>
      <div className="border-box-content">{children}</div>
    </div>
  );
}
