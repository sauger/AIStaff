import type { EChartsCoreOption } from 'echarts/core';

import type { DashboardChart, DashboardScalar, DashboardTableData } from '../dashboardTypes';

export const CHART_HEIGHT = 240;

const PALETTE = ['#22d3ee', '#3b82f6', '#a78bfa', '#34d399', '#fbbf24', '#fb7185', '#60a5fa', '#f472b6'];

function toNumber(value: DashboardScalar): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value.replace(/,/g, ''));
    if (Number.isFinite(parsed)) return parsed;
  }
  return null;
}

function columnIndex(data: DashboardTableData, name: string | undefined): number {
  if (!name) return -1;
  return data.columns.findIndex((column) => column.name === name);
}

function fallbackMeasureIndex(data: DashboardTableData, excluded: number): number {
  for (let index = 0; index < data.columns.length; index += 1) {
    if (index === excluded) continue;
    const column = data.columns[index];
    const sample = data.rows.find((row) => row[index] !== null && row[index] !== undefined);
    if (column.type === 'number' || (sample !== undefined && toNumber(sample[index]) !== null)) {
      return index;
    }
  }
  return -1;
}

type SeriesBucket = { values: Map<string, number> };

/** Aggregates rows into categories (x axis) and optional series dimension. */
function bucketRows(chart: DashboardChart) {
  const data = chart.data;
  const xIndex = Math.max(columnIndex(data, chart.encoding.x), 0) || 0;
  const yIndex = columnIndex(data, chart.encoding.y);
  const measure = yIndex >= 0 ? yIndex : fallbackMeasureIndex(data, xIndex);
  const seriesIndex = columnIndex(data, chart.encoding.series);

  const categories: string[] = [];
  const buckets = new Map<string, SeriesBucket>();

  for (const row of data.rows) {
    const category = String(row[xIndex] ?? '');
    if (!categories.includes(category)) categories.push(category);
    const seriesName = seriesIndex >= 0 ? String(row[seriesIndex] ?? '') : '';
    let bucket = buckets.get(seriesName);
    if (!bucket) {
      bucket = { values: new Map<string, number>() };
      buckets.set(seriesName, bucket);
    }
    const value = measure >= 0 ? toNumber(row[measure]) : null;
    if (value === null) continue;
    bucket.values.set(category, (bucket.values.get(category) ?? 0) + value);
  }

  return { categories, buckets, xIndex, measure };
}

function axisLabel(color: string, rotate: number) {
  return { color, fontSize: 11, rotate, hideOverlap: true };
}

function themeTokens(dark: boolean) {
  return {
    textColor: dark ? '#cbd5e1' : '#5f6675',
    splitColor: dark ? 'rgba(148,163,184,0.16)' : '#eceef1',
    palette: PALETTE,
  };
}

function pieEntries(chart: DashboardChart) {
  const { categories, buckets } = bucketRows(chart);
  return categories.map((category) => {
    let total = 0;
    for (const bucket of buckets.values()) total += bucket.values.get(category) ?? 0;
    return { name: category, value: total };
  });
}

function cartesianSeries(chart: DashboardChart, categories: string[], buckets: Map<string, SeriesBucket>) {
  const seriesNames = Array.from(buckets.keys());
  return seriesNames.map((name) => {
    const values = categories.map((category) => buckets.get(name)?.values.get(category) ?? null);
    const base = {
      name: name || chart.title || '',
      data: values,
      barMaxWidth: 28,
    };
    if (chart.type === 'line') {
      return { ...base, type: 'line', smooth: false, symbolSize: 5 };
    }
    if (chart.type === 'area') {
      return {
        ...base,
        type: 'line',
        smooth: true,
        symbolSize: 4,
        areaStyle: { opacity: 0.28 },
      };
    }
    if (chart.type === 'stacked_column') {
      return { ...base, type: 'bar', stack: 'total' };
    }
    if (chart.type === 'bar') {
      return { ...base, type: 'bar' };
    }
    return { ...base, type: 'bar' };
  });
}

type ChartOptionOptions = { height?: number };

function truncateLabel(value: string, width = 12): string {
  return value.length > width ? `${value.slice(0, width)}…` : value;
}

function horizontalBarOption(chart: DashboardChart, dark: boolean): EChartsCoreOption {
  const { textColor, splitColor, palette } = themeTokens(dark);
  const { categories, buckets } = bucketRows(chart);
  const entries = categories
    .map((name) => {
      let total = 0;
      for (const bucket of buckets.values()) total += bucket.values.get(name) ?? 0;
      return { name, value: total };
    })
    .sort((a, b) => b.value - a.value);
  const shown = entries.slice(0, 20);
  const more = entries.length > shown.length;
  return {
    color: palette,
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 8, right: 24, top: 8, bottom: 8, containLabel: true },
    xAxis: {
      type: 'value',
      axisLabel: { color: textColor, fontSize: 11 },
      splitLine: { lineStyle: { color: splitColor } },
    },
    yAxis: {
      type: 'category',
      inverse: true,
      data: shown.map((entry) => entry.name),
      axisLabel: {
        color: textColor,
        fontSize: 11,
        formatter: (value: string) => truncateLabel(value),
      },
      axisTick: { show: false },
      axisLine: { lineStyle: { color: splitColor } },
    },
    series: [{ type: 'bar', data: shown.map((entry) => entry.value), barMaxWidth: 18 }],
    ...(more
      ? {
          graphic: [
            {
              type: 'text',
              right: 8,
              bottom: 2,
              style: {
                text: `仅展示前 20 项（共 ${entries.length} 项）`,
                fill: textColor,
                fontSize: 10,
              },
            },
          ],
        }
      : {}),
  };
}

export function buildChartOption(
  chart: DashboardChart,
  dark = false,
  opts: ChartOptionOptions = {},
): EChartsCoreOption {
  const { textColor, splitColor, palette } = themeTokens(dark);
  const { categories, buckets, xIndex, measure } = bucketRows(chart);

  // v1 bars have no orientation and must stay vertical; only v2 ranking blocks
  // opt into the horizontal form.
  if (chart.type === 'bar' && chart.orientation === 'horizontal') {
    return horizontalBarOption(chart, dark);
  }

  if (chart.type === 'pie' || chart.type === 'donut') {
    return {
      color: palette,
      tooltip: { trigger: 'item' },
      legend: {
        type: 'scroll',
        bottom: 0,
        show: opts.height === undefined || opts.height >= 160,
        textStyle: { color: textColor, fontSize: 11 },
      },
      series: [
        {
          type: 'pie',
          radius: chart.type === 'donut' ? ['42%', '68%'] : ['18%', '66%'],
          center: ['50%', '44%'],
          avoidLabelOverlap: true,
          itemStyle: { borderColor: dark ? '#0b1220' : '#ffffff', borderWidth: 2 },
          label: { color: textColor, fontSize: 11 },
          data: pieEntries(chart),
        },
      ],
    };
  }

  if (chart.type === 'gauge') {
    const first = chart.data.rows[0] ?? [];
    const value = measure >= 0 ? toNumber(first[measure]) ?? 0 : 0;
    const max = Math.max(100, Math.ceil(Math.abs(value) * 1.2));
    return {
      series: [
        {
          type: 'gauge',
          min: 0,
          max,
          progress: { show: true, width: 12, itemStyle: { color: palette[0] } },
          axisLine: { lineStyle: { width: 12, color: [[1, dark ? 'rgba(148,163,184,0.2)' : '#eceef1']] } },
          axisTick: { show: false },
          splitLine: { show: false },
          axisLabel: { show: false },
          pointer: { show: true, width: 3, itemStyle: { color: palette[0] } },
          anchor: { show: true, size: 10, itemStyle: { color: palette[0] } },
          title: { show: false },
          detail: {
            valueAnimation: true,
            offsetCenter: [0, '62%'],
            fontSize: 20,
            color: textColor,
            formatter: (value: number) => `${value}`,
          },
          data: [{ value }],
        },
      ],
    };
  }

  if (chart.type === 'funnel') {
    const entries = pieEntries(chart).sort((a, b) => b.value - a.value);
    return {
      color: palette,
      tooltip: { trigger: 'item', formatter: '{b}: {c}' },
      series: [
        {
          type: 'funnel',
          left: '6%',
          right: '6%',
          top: 8,
          bottom: 8,
          minSize: '18%',
          sort: 'descending',
          gap: 2,
          label: { color: textColor, fontSize: 11, position: 'inside' },
          data: entries,
        },
      ],
    };
  }

  if (chart.type === 'scatter') {
    const points: Array<[number, number]> = [];
    const yIndex = measure;
    for (const row of chart.data.rows) {
      const x = toNumber(row[xIndex] ?? null);
      const y = yIndex >= 0 ? toNumber(row[yIndex] ?? null) : null;
      if (x === null || y === null) continue;
      points.push([x, y]);
    }
    return {
      color: palette,
      tooltip: { trigger: 'item' },
      grid: { left: 8, right: 14, top: 16, bottom: 8, containLabel: true },
      xAxis: {
        type: 'value',
        axisLabel: { color: textColor, fontSize: 11 },
        splitLine: { lineStyle: { color: splitColor } },
      },
      yAxis: {
        type: 'value',
        axisLabel: { color: textColor, fontSize: 11 },
        splitLine: { lineStyle: { color: splitColor } },
      },
      series: [{ type: 'scatter', symbolSize: 9, data: points }],
    };
  }

  const seriesNames = Array.from(buckets.keys());
  const multiSeries = seriesNames.length > 1 || (seriesNames.length === 1 && seriesNames[0] !== '');
  return {
    color: palette,
    tooltip: { trigger: 'axis' },
    legend: multiSeries
      ? { top: 0, type: 'scroll', textStyle: { color: textColor, fontSize: 11 } }
      : undefined,
    grid: { left: 8, right: 12, top: multiSeries ? 32 : 16, bottom: 8, containLabel: true },
    xAxis: {
      type: 'category',
      data: categories,
      axisLabel: axisLabel(textColor, categories.length > 8 ? 30 : 0),
      axisLine: { lineStyle: { color: splitColor } },
      axisTick: { show: false },
    },
    yAxis: {
      type: 'value',
      axisLabel: { color: textColor, fontSize: 11 },
      splitLine: { lineStyle: { color: splitColor } },
    },
    series: cartesianSeries(chart, categories, buckets),
  };
}
