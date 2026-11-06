import type {
  DashboardGrid,
  DashboardLayout,
  DashboardSlot,
  DashboardSpec,
} from '../dashboardTypes';

/** Fixed design canvas of a v2 board (mirrors backend `GridSpec`). */
export const DEFAULT_GRID: DashboardGrid = { columns: 12, rows: 12, width: 1920, height: 1080 };
/** Title bar + padding inside a wall slot; the chart canvas gets the rest. */
export const WALL_SLOT_CHROME = 56;
export const WALL_SLOT_GAP = 12;
export const MIN_WALL_CHART_HEIGHT = 96;

export type BubbleBreakpoint = 'narrow' | 'medium' | 'wide';

export type SlotRect = { left: number; top: number; width: number; height: number };

export type StageGeometry = {
  scale: number;
  offsetX: number;
  offsetY: number;
  /** Unscaled stage height: 1080, or taller when a scrolling wall overflows. */
  stageHeight: number;
  scroll: boolean;
};

function isSlot(value: unknown): value is DashboardSlot {
  if (!value || typeof value !== 'object') return false;
  const slot = value as Record<string, unknown>;
  return (
    typeof slot.id === 'string'
    && (slot.kind === 'kpi' || slot.kind === 'chart')
    && ['x', 'y', 'w', 'h'].every((key) => typeof slot[key] === 'number')
  );
}

/** v2 boards carry a layout; anything else (v1, malformed) renders the v1 grid. */
export function layoutOf(spec: DashboardSpec): DashboardLayout | null {
  const layout = spec.layout;
  if (!layout || typeof layout !== 'object' || !Array.isArray(layout.slots)) return null;
  if (!layout.slots.every(isSlot)) return null;
  return layout;
}

export function gridOf(layout: DashboardLayout): DashboardGrid {
  const grid = layout.grid;
  if (!grid || !(grid.columns > 0) || !(grid.rows > 0) || !(grid.width > 0) || !(grid.height > 0)) {
    return DEFAULT_GRID;
  }
  return grid;
}

/** Reading order of the wall: top-to-bottom, then left-to-right. */
export function orderedSlots(layout: DashboardLayout, kind?: DashboardSlot['kind']): DashboardSlot[] {
  return layout.slots
    .filter((slot) => (kind ? slot.kind === kind : true))
    .slice()
    .sort((a, b) => a.y - b.y || a.x - b.x);
}

/** v1 KPIs have no id; their slot reference falls back to the list index. */
export function kpiKey(kpi: { id?: string | null }, index: number): string {
  return kpi.id || `kpi-${index}`;
}

export function bubbleBreakpoint(containerWidth: number): BubbleBreakpoint {
  if (containerWidth < 560) return 'narrow';
  if (containerWidth <= 1100) return 'medium';
  return 'wide';
}

/**
 * Column span (out of 12) of a slot inside a chat bubble: the wall's `w` is
 * folded into at most two columns below 1100px so nothing scrolls sideways.
 */
export function bubbleColumnSpan(slotWidth: number, containerWidth: number): number {
  const breakpoint = bubbleBreakpoint(containerWidth);
  if (breakpoint === 'narrow') return 12;
  if (breakpoint === 'medium') return slotWidth >= 8 ? 12 : 6;
  if (slotWidth <= 4) return 4;
  if (slotWidth <= 7) return 6;
  return 12;
}

export function slotRect(slot: DashboardSlot, grid: DashboardGrid = DEFAULT_GRID): SlotRect {
  const cellWidth = grid.width / grid.columns;
  const cellHeight = grid.height / grid.rows;
  return {
    left: slot.x * cellWidth,
    top: slot.y * cellHeight,
    width: slot.w * cellWidth,
    height: slot.h * cellHeight,
  };
}

/** Canvas height of a chart in a wall slot (`h × 90 − chrome`). */
export function wallChartHeight(slot: DashboardSlot, grid: DashboardGrid = DEFAULT_GRID): number {
  return Math.max(MIN_WALL_CHART_HEIGHT, slotRect(slot, grid).height - WALL_SLOT_CHROME);
}

/** Bottom edge of the lowest slot, in pixels of the unscaled stage. */
export function wallContentHeight(layout: DashboardLayout): number {
  const grid = gridOf(layout);
  const rows = Math.max(0, ...layout.slots.map((slot) => slot.y + slot.h));
  return rows * (grid.height / grid.rows);
}

/**
 * Letterboxed fit of the 1920×1080 stage into the viewport.  A scrolling wall
 * (rare overflow fallback) fits the width instead and scrolls vertically.
 */
export function stageGeometry(
  viewportWidth: number,
  viewportHeight: number,
  layout: DashboardLayout,
): StageGeometry {
  const grid = gridOf(layout);
  const width = Math.max(1, viewportWidth);
  const height = Math.max(1, viewportHeight);
  const scroll = layout.overflow === 'scroll';
  if (scroll) {
    const scale = width / grid.width;
    return {
      scale,
      offsetX: 0,
      offsetY: 0,
      stageHeight: Math.max(grid.height, wallContentHeight(layout) + WALL_SLOT_GAP),
      scroll,
    };
  }
  const scale = Math.min(width / grid.width, height / grid.height);
  return {
    scale,
    offsetX: (width - grid.width * scale) / 2,
    offsetY: (height - grid.height * scale) / 2,
    stageHeight: grid.height,
    scroll,
  };
}
