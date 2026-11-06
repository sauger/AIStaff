/**
 * Source: https://github.com/gcpaas/DataRoom (commit 1e04b9c)
 * Original: dataRoomFront/src/dataRoom/datav/components/Decoration11/src/index.vue
 * License: Apache-2.0 (DataRoom), upstream DataV-Vue3 MIT
 * Modified for React: @jiaminghi/color.fade replaced by local withAlpha.
 */
import type { ReactNode } from 'react';

import { mergedColor, useAutoResize, withAlpha } from './useAutoResize';

const DEFAULT_COLOR = ['#1a98fc', '#2cf7fe'];

type Props = {
  children?: ReactNode;
  className?: string;
  color?: string[];
};

export default function DvDecoration11({ children, className = '', color }: Props) {
  const { ref, width, height } = useAutoResize<HTMLDivElement>();
  const [c0, c1] = mergedColor(DEFAULT_COLOR, color);

  return (
    <div ref={ref} className={`dv-decoration-11 ${className}`} aria-hidden={children ? undefined : true}>
      <svg width={width} height={height}>
        <polygon fill={withAlpha(c1, 10)} stroke={c1} points="20 10, 25 4, 55 4 60 10" />
        <polygon fill={withAlpha(c1, 10)} stroke={c1} points={`20 ${height - 10}, 25 ${height - 4}, 55 ${height - 4} 60 ${height - 10}`} />
        <polygon fill={withAlpha(c1, 10)} stroke={c1} points={`${width - 20} 10, ${width - 25} 4, ${width - 55} 4 ${width - 60} 10`} />
        <polygon
          fill={withAlpha(c1, 10)}
          stroke={c1}
          points={`${width - 20} ${height - 10}, ${width - 25} ${height - 4}, ${width - 55} ${height - 4} ${width - 60} ${height - 10}`}
        />
        <polygon
          fill={withAlpha(c0, 20)}
          stroke={c0}
          points={`20 10, 5 ${height / 2} 20 ${height - 10} ${width - 20} ${height - 10} ${width - 5} ${height / 2} ${width - 20} 10`}
        />
        <polyline
          fill="transparent"
          stroke={withAlpha(c0, 70)}
          points={`25 18, 15 ${height / 2} 25 ${height - 18}`}
        />
        <polyline
          fill="transparent"
          stroke={withAlpha(c0, 70)}
          points={`${width - 25} 18, ${width - 15} ${height / 2} ${width - 25} ${height - 18}`}
        />
      </svg>
      {children ? <div className="decoration-content">{children}</div> : null}
    </div>
  );
}
