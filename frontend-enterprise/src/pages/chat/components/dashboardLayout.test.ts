import { describe, expect, it } from 'vitest';

import type { DashboardLayout, DashboardSpec } from '../dashboardTypes';

import {
  bubbleBreakpoint,
  bubbleColumnSpan,
  bubbleRowSpans,
  gridOf,
  layoutOf,
  orderedSlots,
  slotRect,
  stageGeometry,
  wallChartHeight,
  wallContentHeight,
} from './dashboardLayout';

const layout: DashboardLayout = {
  template: 'overview',
  grid: { columns: 12, rows: 12, width: 1920, height: 1080 },
  overflow: 'fit',
  slots: [
    { id: 'kpi_a', kind: 'kpi', region: 'kpi_row', x: 0, y: 1, w: 3, h: 1 },
    { id: 'trend', kind: 'chart', region: 'hero', x: 0, y: 2, w: 12, h: 4 },
    { id: 'rank', kind: 'chart', region: 'grid', x: 0, y: 6, w: 6, h: 3 },
  ],
};

function specWithLayout(): DashboardSpec {
  return {
    spec_version: 2,
    title: 'v2',
    kpis: [{ id: 'kpi_a', label: '销售额', value: 1 }],
    charts: [],
    filters: [],
    layout,
  };
}

describe('dashboardLayout', () => {
  it('recognises a v2 layout and ignores malformed ones', () => {
    expect(layoutOf(specWithLayout())).toBe(layout);
    expect(layoutOf({ ...specWithLayout(), layout: null })).toBeNull();
    expect(layoutOf({ ...specWithLayout(), layout: { ...layout, slots: [{ id: 1 }] as never } })).toBeNull();
  });

  it('falls back to the fixed 12x12 grid when the payload is incomplete', () => {
    expect(gridOf({ ...layout, grid: { columns: 0, rows: 12, width: 1920, height: 1080 } })).toEqual({
      columns: 12, rows: 12, width: 1920, height: 1080,
    });
    expect(gridOf(layout)).toEqual(layout.grid);
  });

  it('orders slots by row then column', () => {
    const shuffled: DashboardLayout = {
      ...layout,
      slots: [layout.slots[2], layout.slots[0], layout.slots[1]],
    };
    expect(orderedSlots(shuffled).map((slot) => slot.id)).toEqual(['kpi_a', 'trend', 'rank']);
    expect(orderedSlots(shuffled, 'chart').map((slot) => slot.id)).toEqual(['trend', 'rank']);
  });

  it('maps breakpoints and column spans', () => {
    expect(bubbleBreakpoint(500)).toBe('narrow');
    expect(bubbleBreakpoint(700)).toBe('medium');
    expect(bubbleBreakpoint(900)).toBe('medium');
    expect(bubbleBreakpoint(1200)).toBe('wide');
    expect(bubbleColumnSpan(12, 500)).toBe(12);
    expect(bubbleColumnSpan(4, 700)).toBe(6);
    expect(bubbleColumnSpan(8, 700)).toBe(12);
    expect(bubbleColumnSpan(4, 1200)).toBe(4);
  });

  it('folds a pulse 4+8 row into two equal bubble columns', () => {
    const left = { id: 'left', kind: 'chart' as const, region: 'left' as const, x: 0, y: 2, w: 4, h: 5 };
    const right = { id: 'right', kind: 'chart' as const, region: 'right' as const, x: 4, y: 2, w: 8, h: 5 };
    expect(bubbleRowSpans([left, right], 700)).toEqual([6, 6]);
    expect(bubbleRowSpans([left, right], 500)).toEqual([12, 12]);
    expect(bubbleRowSpans([right], 700)).toEqual([12]);
  });

  it('converts slots to pixels and wall heights', () => {
    expect(slotRect(layout.slots[1])).toEqual({ left: 0, top: 180, width: 1920, height: 360 });
    expect(wallChartHeight(layout.slots[1])).toBe(304);
    expect(wallContentHeight(layout)).toBe(810);
  });

  it('letterboxes 1920x1080 and 1366x768', () => {
    expect(stageGeometry(1920, 1080, layout)).toEqual({
      scale: 1, offsetX: 0, offsetY: 0, stageHeight: 1080, scroll: false,
    });
    const small = stageGeometry(1366, 768, layout);
    expect(small.scale).toBeCloseTo(768 / 1080, 5);
    expect(small.offsetX).toBeCloseTo((1366 - 1920 * small.scale) / 2, 5);
    expect(small.offsetY).toBeCloseTo(0, 5);
  });

  it('fits width and scrolls when the layout overflows', () => {
    const scrolling = { ...layout, overflow: 'scroll' as const };
    const geometry = stageGeometry(960, 540, scrolling);
    expect(geometry.scroll).toBe(true);
    expect(geometry.scale).toBe(0.5);
    expect(geometry.stageHeight).toBeGreaterThanOrEqual(1080);
  });
});
