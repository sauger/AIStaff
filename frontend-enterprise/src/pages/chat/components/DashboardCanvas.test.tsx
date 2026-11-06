// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { DashboardSpec } from '../dashboardTypes';

import DashboardCanvas from './DashboardCanvas';

vi.mock('./dashboardEchartsRuntime', () => ({
  echarts: {
    init: () => ({ setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn() }),
  },
}));

vi.mock('./dashboardEcharts', () => ({
  CHART_HEIGHT: 240,
  buildChartOption: () => ({}),
}));

const spec: DashboardSpec = {
  spec_version: 1,
  title: '华东区 Q3 经营看板',
  subtitle: '来源：ERP',
  as_of: '2026-09-30',
  theme: 'light',
  kpis: [
    { label: '销售额', value: '1,284 万', unit: '元', delta: 0.12, hint: '环比' },
    { label: '订单数', value: 3821 },
  ],
  charts: [
    {
      id: 'trend',
      title: '月度趋势',
      type: 'line',
      encoding: { x: 'month', y: 'sales' },
      data: {
        columns: [
          { name: 'month', type: 'string' },
          { name: 'sales', type: 'number' },
        ],
        rows: [
          ['2026-07', 420],
          ['2026-08', 431],
          ['2026-09', 433],
        ],
      },
      note: '仅 2 个数据点，折线会误示连续趋势，已改为柱状',
    },
    {
      id: 'detail',
      title: '明细',
      type: 'table',
      encoding: {},
      data: {
        columns: [
          { name: '门店', type: 'string' },
          { name: '金额', type: 'number' },
        ],
        rows: [['静安店', 1200]],
      },
    },
  ],
  filters: [{ id: 'region', label: '区域', type: 'select', default: '华东' }],
  meta: { source: 'erp.sales' },
};

afterEach(() => {
  cleanup();
});

describe('DashboardCanvas', () => {
  it('renders title, kpis, charts and provenance', () => {
    render(<DashboardCanvas spec={spec} />);

    expect(screen.getByText('华东区 Q3 经营看板')).toBeTruthy();
    expect(screen.getByText('销售额')).toBeTruthy();
    expect(screen.getByText('1,284 万')).toBeTruthy();
    expect(screen.getByText('月度趋势')).toBeTruthy();
    expect(screen.getByText(/数据截止/)).toBeTruthy();
    expect(screen.getByText(/来源 erp\.sales/)).toBeTruthy();
    expect(screen.getByText(/区域：华东/)).toBeTruthy();
  });

  it('shows the chart adjustment note', () => {
    render(<DashboardCanvas spec={spec} />);
    expect(screen.getByText(/仅 2 个数据点/)).toBeTruthy();
  });

  it('renders table charts as a real table', () => {
    render(<DashboardCanvas spec={spec} />);
    const table = screen.getByRole('table');
    expect(table).toBeTruthy();
    expect(screen.getByText('静安店')).toBeTruthy();
  });

  it('renders the tech theme shell and wide panels', () => {
    const techSpec: DashboardSpec = {
      ...spec,
      theme: 'tech',
      charts: [{ ...spec.charts[0], span: 2 }],
    };
    const { container } = render(<DashboardCanvas spec={techSpec} />);
    const root = container.firstElementChild as HTMLElement;
    expect(root.getAttribute('data-theme')).toBe('tech');
    expect(container.innerHTML).toContain('border-cyan-400');
  });

  it('marks a missing data source instead of inventing one', () => {
    render(<DashboardCanvas spec={{ ...spec, meta: {} }} />);
    expect(screen.getByText(/未标注来源/)).toBeTruthy();
  });
});


const v2Spec: DashboardSpec = {
  spec_version: 2,
  title: 'v2 看板',
  theme: 'tech',
  kpis: [{ id: 'kpi_a', label: '销售额', value: 128 }],
  charts: [
    {
      id: 'rank',
      title: '区域排名',
      type: 'bar',
      role: 'ranking',
      orientation: 'horizontal',
      confidence: 0.4,
      encoding: { x: 'region', y: 'sales' },
      data: {
        columns: [{ name: 'region' }, { name: 'sales', type: 'number' }],
        rows: [['上海', 10], ['江苏', 8]],
      },
      note: '已按金额降序',
    },
  ],
  filters: [],
  warnings: [{ code: 'TRUNCATED', message: '已省略 2 个块' }],
  layout: {
    template: 'overview',
    grid: { columns: 12, rows: 12, width: 1920, height: 1080 },
    overflow: 'fit',
    slots: [
      { id: 'kpi_a', kind: 'kpi', region: 'kpi_row', x: 0, y: 1, w: 3, h: 1 },
      { id: 'rank', kind: 'chart', region: 'hero', x: 0, y: 2, w: 12, h: 4 },
    ],
  },
};

describe('DashboardCanvas v2', () => {
  it('renders v2 slots, warnings and the low-confidence badge', () => {
    const { container } = render(<DashboardCanvas spec={v2Spec} />);
    expect(container.querySelector('[data-spec-version="2"]')).toBeTruthy();
    expect(screen.getByText('已省略 2 个块')).toBeTruthy();
    expect(screen.getByText('自动判定')).toBeTruthy();
    expect(container.querySelector('[data-kpi-id="kpi_a"]')).toBeTruthy();
    expect(container.querySelector('[data-chart-id="rank"]')).toBeTruthy();
  });

  it('renders wall slots at their absolute grid rectangles', () => {
    const { container } = render(<DashboardCanvas spec={v2Spec} mode="wall" />);
    const slot = container.querySelector('[data-slot-id="rank"]') as HTMLElement;
    expect(slot.style.top).toBe('186px');
    expect(slot.style.height).toBe('348px');
    expect(container.querySelector('[data-mode="wall"]')).toBeTruthy();
  });

  it('keeps the v1 layout when a v2 payload has no layout', () => {
    const { container } = render(<DashboardCanvas spec={{ ...v2Spec, layout: null }} />);
    expect(container.querySelector('[data-spec-version="2"]')).toBeNull();
    expect(screen.getByText('区域排名')).toBeTruthy();
  });
});
