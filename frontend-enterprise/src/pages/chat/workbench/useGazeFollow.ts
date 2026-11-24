import { useEffect } from 'react';

import { useEmployeeAppearance } from './useEmployeeAppearance';

/**
 * 眼珠跟随指针：指针进入约 210px 感应半径后两只瞳孔朝指针方向转动，
 * 贴近脸前偏移收小（像在看你），离开后恢复自动游移。
 * rAF 节流；触控与 prefers-reduced-motion 跳过。
 */
export function useGazeFollow(
  rootRef: React.RefObject<HTMLElement | null>,
  options: { enabled: boolean; eyeOffsetY?: number },
) {
  const { appearance } = useEmployeeAppearance();

  useEffect(() => {
    const el = rootRef.current;
    if (!el || !options.enabled) return;
    const art = el.querySelector('.cw-employee-art');
    if (!art) return;
    if (typeof window.matchMedia !== 'function'
      || window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      return;
    }
    const radius = 210;
    let raf = 0;
    let x = 0;
    let y = 0;
    let looking = false;

    const apply = () => {
      raf = 0;
      const rect = art.getBoundingClientRect();
      if (!rect.width) return;
      const cx = rect.left + rect.width / 2;
      const cy = rect.top + rect.height * (options.eyeOffsetY ?? 0.189);
      const dx = x - cx;
      const dy = y - cy;
      const dist = Math.hypot(dx, dy) || 1;
      const near = Math.min(1, dist / 80);
      const fade = Math.max(0, Math.min(1, (radius - dist) / 60));
      const magnitude = near * fade;
      if (magnitude < 0.02) {
        if (looking) {
          looking = false;
          el.classList.remove('cw-is-looking');
        }
        return;
      }
      el.style.setProperty(
        '--cw-look-x',
        `${((dx / dist) * (appearance === 'ink' ? 7 : 2.4) * magnitude).toFixed(2)}px`,
      );
      el.style.setProperty(
        '--cw-look-y',
        `${((dy / dist) * (appearance === 'ink' ? 6 : 2.5) * magnitude).toFixed(2)}px`,
      );
      if (!looking) {
        looking = true;
        el.classList.add('cw-is-looking');
      }
    };

    const onMove = (event: PointerEvent) => {
      if (event.pointerType === 'touch') return;
      x = event.clientX;
      y = event.clientY;
      if (!raf) raf = requestAnimationFrame(apply);
    };

    window.addEventListener('pointermove', onMove, { passive: true });
    return () => {
      window.removeEventListener('pointermove', onMove);
      if (raf) cancelAnimationFrame(raf);
      el.classList.remove('cw-is-looking');
    };
  }, [rootRef, options.enabled, options.eyeOffsetY, appearance]);
}
