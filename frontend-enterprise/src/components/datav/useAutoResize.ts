import { useEffect, useRef, useState } from 'react';

export type AutoSize = { width: number; height: number };

/**
 * React port of DataRoom's `utils/autoResize.ts`: observe the element, debounce
 * 200ms, and also react to window resizes. The SVG assets are drawn from real
 * pixel coordinates, so every border/decoration needs this.
 */
export function useAutoResize<T extends HTMLElement>() {
  const ref = useRef<T | null>(null);
  const [size, setSize] = useState<AutoSize>({ width: 0, height: 0 });

  useEffect(() => {
    const node = ref.current;
    if (!node) return undefined;
    let timer: number | null = null;

    const measure = () => {
      const rect = node.getBoundingClientRect();
      setSize({
        width: Math.max(0, Math.round(node.clientWidth || rect.width)),
        height: Math.max(0, Math.round(node.clientHeight || rect.height)),
      });
    };
    const schedule = () => {
      if (timer !== null) window.clearTimeout(timer);
      timer = window.setTimeout(measure, 200);
    };

    measure();
    let observer: ResizeObserver | null = null;
    if (typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(schedule);
      observer.observe(node);
    }
    window.addEventListener('resize', schedule);
    return () => {
      observer?.disconnect();
      window.removeEventListener('resize', schedule);
      if (timer !== null) window.clearTimeout(timer);
    };
  }, []);

  return { ref, width: size.width, height: size.height };
}

export function mergedColor(defaults: string[], color?: string[]): string[] {
  return color && color.length > 0 ? color : defaults;
}

function parseColor(color: string): [number, number, number] | null {
  const value = color.trim();
  if (value.startsWith('#')) {
    const hex = value.slice(1);
    const full = hex.length === 3
      ? hex.split('').map((char) => `${char}${char}`).join('')
      : hex;
    if (full.length !== 6) return null;
    return [
      Number.parseInt(full.slice(0, 2), 16),
      Number.parseInt(full.slice(2, 4), 16),
      Number.parseInt(full.slice(4, 6), 16),
    ];
  }
  const match = value.match(/^rgba?\(([^)]+)\)$/i);
  if (!match) return null;
  const parts = match[1].split(',').map((part) => Number.parseFloat(part.trim()));
  if (parts.length < 3 || parts.some((part) => Number.isNaN(part))) return null;
  return [parts[0], parts[1], parts[2]];
}

/** Local replacement for `@jiaminghi/color.fade(color, percent)` (0–100). */
export function withAlpha(color: string, percent: number): string {
  const rgb = parseColor(color);
  if (!rgb) return color;
  return `rgba(${rgb[0]}, ${rgb[1]}, ${rgb[2]}, ${Math.max(0, Math.min(1, percent / 100))})`;
}

export const DATAV_SOURCE = {
  repo: 'https://github.com/gcpaas/DataRoom',
  commit: '1e04b9c',
  license: 'Apache-2.0',
  upstream: 'DataV-Vue3 (MIT)',
};
