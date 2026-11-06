/**
 * Source: https://github.com/gcpaas/DataRoom (commit 1e04b9c)
 * Original: dataRoomFront/src/dataRoom/datav/components/BorderBox1/src/BorderBox1.tsx
 * License: Apache-2.0 (DataRoom), upstream DataV-Vue3 MIT
 * Modified for React: SMIL fill animations replaced with CSS keyframes.
 */
import type { CSSProperties, ReactNode } from 'react';

import { mergedColor, useAutoResize } from './useAutoResize';

const BORDER = ['left-top', 'right-top', 'left-bottom', 'right-bottom'] as const;
const DEFAULT_COLOR = ['#4fd2dd', '#235fa7'];

type Props = {
  children?: ReactNode;
  className?: string;
  color?: string[];
  backgroundColor?: string;
};

export default function DvBorderBox1({ children, className = '', color, backgroundColor = 'transparent' }: Props) {
  const { ref, width, height } = useAutoResize<HTMLDivElement>();
  const [c0, c1] = mergedColor(DEFAULT_COLOR, color);

  return (
    <div
      ref={ref}
      className={`dv-border-box-1 ${className}`}
      style={{ '--dv-c1': c0, '--dv-c2': c1 } as CSSProperties}
    >
      <svg className="dv-border" width={width} height={height} aria-hidden="true">
        <polygon
          fill={backgroundColor}
          points={`10,27 10,${height - 27} 13,${height - 24} 13,${height - 21} 24,${height - 11} 38,${height - 11} 41,${height - 8} 73,${height - 8} 75,${height - 10} 81,${height - 10} 85,${height - 6} ${width - 85},${height - 6} ${width - 81},${height - 10} ${width - 75},${height - 10} ${width - 73},${height - 8} ${width - 41},${height - 8} ${width - 38},${height - 11} ${width - 10},${height - 27} ${width - 10},27 ${width - 13},25 ${width - 13},21 ${width - 24},11 ${width - 38},11 ${width - 41},8 ${width - 73},8 ${width - 75},10 ${width - 81},10 ${width - 85},6 85,6 81,10 75,10 73,8 41,8 38,11 24,11 13,21 13,24`}
        />
      </svg>

      {BORDER.map((item) => (
        <svg key={item} width="150" height="150" className={`${item} dv-border`} aria-hidden="true">
          <polygon
            className="dv-b1-pulse-a"
            fill={c0}
            points="6,66 6,18 12,12 18,12 24,6 27,6 30,9 36,9 39,6 84,6 81,9 75,9 73.2,7 40.8,7 37.8,10.2 24,10.2 12,21 12,24 9,27 9,51 7.8,54 7.8,63"
          />
          <polygon
            className="dv-b1-pulse-b"
            fill={c1}
            points="27.6,4.8 38.4,4.8 35.4,7.8 30.6,7.8"
          />
          <polygon
            className="dv-b1-fade"
            fill={c0}
            points="9,54 9,63 7.2,66 7.2,75 7.8,78 7.8,110 8.4,110 8.4,66 9.6,66 9.6,54"
          />
        </svg>
      ))}

      <div className="border-box-content">{children}</div>
    </div>
  );
}
