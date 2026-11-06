/**
 * Source: https://github.com/gcpaas/DataRoom (commit 1e04b9c)
 * Original: dataRoomFront/src/dataRoom/datav/components/Decoration8/src/index.vue
 * License: Apache-2.0 (DataRoom), upstream DataV-Vue3 MIT
 * Modified for React: stateless port, no SMIL.
 */
import { mergedColor, useAutoResize } from './useAutoResize';

const DEFAULT_COLOR = ['#3f96a5', '#3f96a5'];

type Props = {
  className?: string;
  color?: string[];
  reverse?: boolean;
};

export default function DvDecoration8({ className = '', color, reverse = false }: Props) {
  const { ref, width, height } = useAutoResize<HTMLDivElement>();
  const [c0, c1] = mergedColor(DEFAULT_COLOR, color);
  const xPos = (pos: number) => (reverse ? width - pos : pos);

  return (
    <div ref={ref} className={`dv-decoration-8 ${className}`} aria-hidden="true">
      <svg width={width} height={height}>
        <polyline
          stroke={c0}
          strokeWidth="2"
          fill="transparent"
          points={`${xPos(0)},0 ${xPos(30)},${height / 2}`}
        />
        <polyline
          stroke={c0}
          strokeWidth="2"
          fill="transparent"
          points={`${xPos(20)},0 ${xPos(50)},${height / 2} ${xPos(width)},${height / 2}`}
        />
        <polyline
          stroke={c1}
          fill="transparent"
          strokeWidth="3"
          points={`${xPos(0)},${height - 3} ${xPos(200)},${height - 3}`}
        />
      </svg>
    </div>
  );
}
