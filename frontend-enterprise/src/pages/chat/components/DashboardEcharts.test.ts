import { describe, expect, it } from 'vitest';

import type { DashboardChart } from '../dashboardTypes';

import { buildChartOption } from './dashboardEcharts';

function rankingChart(count: number): DashboardChart {
  return {
    id: 'rank',
    title: '区域排名',
    type: 'bar',
    role: 'ranking',
    orientation: 'horizontal',
    encoding: { x: 'region', y: 'sales' },
    data: {
      columns: [{ name: 'region' }, { name: 'sales', type: 'number' }],
      rows: Array.from({ length: count }, (_, index) => [`区域 ${index + 1}`, index + 1]),
    },
  };
}

describe('buildChartOption', () => {
  it('sorts, truncates and reverses a horizontal ranking bar', () => {
    const option = buildChartOption(rankingChart(25), true) as any;
    expect(option.yAxis.type).toBe('category');
    expect(option.yAxis.inverse).toBe(true);
    expect(option.xAxis.type).toBe('value');
    expect(option.yAxis.data).toHaveLength(20);
    expect(option.series[0].data).toHaveLength(20);
    expect(option.yAxis.data[0]).toBe('区域 25');
    expect(option.series[0].data[0]).toBe(25);
    expect(JSON.stringify(option)).toContain('仅展示前 20 项');
  });

  it('keeps a v1 bar vertical when orientation is absent', () => {
    const chart = rankingChart(3);
    const legacy: DashboardChart = { ...chart, orientation: undefined, role: null };
    const option = buildChartOption(legacy, false) as any;
    expect(option.xAxis.type).toBe('category');
    expect(option.yAxis.type).toBe('value');
  });

  it('does not turn a vertical bar into a ranking bar', () => {
    const chart = rankingChart(3);
    const option = buildChartOption({ ...chart, orientation: 'vertical' }, false) as any;
    expect(option.xAxis.type).toBe('category');
  });

  it('hides the pie legend in very short wall slots', () => {
    const pie: DashboardChart = {
      id: 'share',
      type: 'donut',
      encoding: { x: 'channel', y: 'amount' },
      data: {
        columns: [{ name: 'channel' }, { name: 'amount', type: 'number' }],
        rows: [['直营', 10], ['经销', 5], ['线上', 3]],
      },
    };
    expect((buildChartOption(pie, true, { height: 120 }) as any).legend.show).toBe(false);
    expect((buildChartOption(pie, true, { height: 240 }) as any).legend.show).toBe(true);
  });
});
