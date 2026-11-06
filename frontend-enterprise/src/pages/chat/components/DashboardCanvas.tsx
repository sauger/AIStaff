import { useEffect, useRef, useState } from 'react';

import type {
  DashboardChart,
  DashboardKpi,
  DashboardLayout,
  DashboardScalar,
  DashboardSlot,
  DashboardSpec,
  DashboardTableData,
} from '../dashboardTypes';

import { buildChartOption, CHART_HEIGHT } from './dashboardEcharts';
import {
  bubbleRowSpans,
  gridOf,
  kpiKey,
  layoutOf,
  orderedSlots,
  slotRect,
  wallChartHeight,
} from './dashboardLayout';

type ChartInstance = {
  setOption: (option: unknown, opts?: { notMerge?: boolean }) => void;
  resize: () => void;
  dispose: () => void;
};

type CanvasMode = 'bubble' | 'wall';

const frameQueue: Array<() => void> = [];
let frameHandle: number | null = null;

function flushFrameQueue() {
  frameHandle = null;
  const task = frameQueue.shift();
  if (task) task();
  if (frameQueue.length > 0) scheduleFrame();
}

function scheduleFrame() {
  if (frameHandle !== null) return;
  if (typeof window !== 'undefined' && typeof window.requestAnimationFrame === 'function') {
    frameHandle = window.requestAnimationFrame(flushFrameQueue);
  } else {
    frameHandle = window.setTimeout(flushFrameQueue, 16);
  }
}

function enqueueFrame(task: () => void) {
  frameQueue.push(task);
  scheduleFrame();
}

function formatScalar(value: DashboardScalar): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'number') {
    return Number.isInteger(value) ? value.toLocaleString('zh-CN') : value.toFixed(2);
  }
  return String(value);
}

function ChartCanvas({ chart, dark, height = CHART_HEIGHT }: { chart: DashboardChart; dark: boolean; height?: number }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const instanceRef = useRef<ChartInstance | null>(null);
  const latestRef = useRef({ chart, dark, height });
  latestRef.current = { chart, dark, height };

  useEffect(() => {
    const node = containerRef.current;
    if (!node) return undefined;
    let disposed = false;
    let observer: ResizeObserver | null = null;

    // ECharts is heavy and dashboards are occasional: keep it in its own chunk.
    enqueueFrame(() => {
      if (disposed || !containerRef.current) return;
      void import('./dashboardEchartsRuntime').then(({ echarts }) => {
        if (disposed || !containerRef.current) return;
        const instance = echarts.init(containerRef.current, undefined, { renderer: 'canvas' }) as ChartInstance;
        instanceRef.current = instance;
        const current = latestRef.current;
        instance.setOption(
          buildChartOption(current.chart, current.dark, { height: current.height }),
          { notMerge: true },
        );
        if (typeof ResizeObserver !== 'undefined') {
          observer = new ResizeObserver(() => instanceRef.current?.resize());
          observer.observe(containerRef.current);
        }
      });
    });

    return () => {
      disposed = true;
      observer?.disconnect();
      instanceRef.current?.dispose();
      instanceRef.current = null;
    };
  }, []);

  useEffect(() => {
    instanceRef.current?.setOption(buildChartOption(chart, dark, { height }), { notMerge: true });
  }, [chart, dark, height]);

  useEffect(() => {
    instanceRef.current?.resize();
  }, [height]);

  return <div ref={containerRef} className="w-full" style={{ height }} />;
}

function DataTable({ data, dark, maxHeight = 240 }: { data: DashboardTableData; dark: boolean; maxHeight?: number }) {
  if (data.columns.length === 0) {
    return <p className="px-1 py-6 text-xs text-[#858b9c]">暂无数据</p>;
  }
  const headClass = dark
    ? 'sticky top-0 bg-[#0d1728] text-slate-400'
    : 'sticky top-0 bg-[#fafbfc] text-[#5f6675]';
  const cellClass = dark
    ? 'border-[#1e2c44] text-slate-200'
    : 'border-[#f2f4f6] text-[#3f4653]';
  return (
    <div
      className={`overflow-auto rounded-lg border ${dark ? 'border-[#1e2c44]' : 'border-[#eceef1]'}`}
      style={{ maxHeight }}
    >
      <table className="w-full border-collapse text-xs">
        <thead className={headClass}>
          <tr>
            {data.columns.map((column) => (
              <th
                key={column.name}
                className={`whitespace-nowrap border-b px-2 py-1.5 text-left font-medium ${cellClass}`}
              >
                {column.name}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.rows.map((row, rowIndex) => (
            <tr key={`row-${rowIndex}`}>
              {row.map((cell, cellIndex) => (
                <td
                  key={`cell-${rowIndex}-${cellIndex}`}
                  className={`whitespace-nowrap border-b px-2 py-1.5 ${cellClass}`}
                >
                  {formatScalar(cell)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Panel({
  chart,
  dark,
  wide,
  height,
  wall,
}: {
  chart: DashboardChart;
  dark: boolean;
  wide?: boolean;
  height?: number;
  wall?: boolean;
}) {
  const panelClass = wall
    ? dark
      ? 'relative h-full overflow-hidden border border-cyan-400/25 bg-[#0c1626]/95 p-3'
      : 'relative h-full overflow-hidden border border-[#e4e8ec] bg-white p-3'
    : dark
      ? 'relative rounded-lg border border-cyan-400/20 bg-[#0c1626]/90 p-3'
      : 'relative rounded-lg border border-[#e4e8ec] bg-white p-3';
  const titleClass = dark ? 'text-slate-100' : 'text-[#2f3542]';
  const noteClass = dark ? 'text-slate-400' : 'text-[#9aa1ad]';
  const lowConfidence = typeof chart.confidence === 'number' && chart.confidence < 0.6;
  return (
    <section
      className={`${panelClass} ${!wall && wide ? 'col-span-1 lg:col-span-2' : ''}`}
      data-chart-id={chart.id}
      data-chart-type={chart.type}
      data-chart-role={chart.role || undefined}
    >
      {dark ? (
        <>
          <span className="pointer-events-none absolute left-0 top-0 size-3 border-l-2 border-t-2 border-cyan-400/60" />
          <span className="pointer-events-none absolute right-0 top-0 size-3 border-r-2 border-t-2 border-cyan-400/60" />
          <span className="pointer-events-none absolute bottom-0 left-0 size-3 border-b-2 border-l-2 border-cyan-400/60" />
          <span className="pointer-events-none absolute bottom-0 right-0 size-3 border-b-2 border-r-2 border-cyan-400/60" />
        </>
      ) : null}
      <header className="mb-1 flex items-start justify-between gap-2">
        <h4 className={`flex items-center gap-1.5 ${wall ? 'text-[15px]' : 'text-[13px]'} font-medium ${titleClass}`}>
          {dark ? <span className="inline-block h-3 w-[3px] rounded bg-cyan-400" /> : null}
          <span className="truncate">{chart.title || chart.id}</span>
          {lowConfidence ? (
            <span
              className="shrink-0 rounded border border-amber-400/40 px-1 text-[10px] font-normal text-amber-500"
              title={chart.note || '自动判定'}
            >
              自动判定
            </span>
          ) : null}
        </h4>
        {chart.note ? (
          <span
            className={`shrink-0 cursor-help text-[11px] ${noteClass}`}
            title={chart.note}
            aria-label={chart.note}
          >
            ⓘ
          </span>
        ) : null}
      </header>
      {chart.type === 'table' ? (
        <DataTable data={chart.data} dark={dark} maxHeight={height ?? 240} />
      ) : (
        <ChartCanvas chart={chart} dark={dark} height={height ?? CHART_HEIGHT} />
      )}
      {chart.note && !wall ? <p className={`mt-1 text-[11px] leading-4 ${noteClass}`}>{chart.note}</p> : null}
    </section>
  );
}

function KpiCard({ kpi, dark, wall }: { kpi: DashboardKpi; dark: boolean; wall?: boolean }) {
  const delta = typeof kpi.delta === 'number' ? kpi.delta : null;
  const cardClass = wall
    ? dark
      ? 'relative flex h-full flex-col justify-center overflow-hidden border border-cyan-400/25 bg-gradient-to-br from-cyan-500/10 to-transparent px-3'
      : 'relative flex h-full flex-col justify-center overflow-hidden rounded-lg border border-[#e4e8ec] bg-white px-3'
    : dark
      ? 'relative overflow-hidden rounded-lg border border-cyan-400/25 bg-gradient-to-br from-cyan-500/10 to-transparent px-3 py-2.5'
      : 'rounded-lg border border-[#e4e8ec] bg-white px-3 py-2.5';
  const labelClass = dark ? 'text-slate-400' : 'text-[#858b9c]';
  const valueClass = dark ? 'text-cyan-300' : 'text-[#1f2430]';
  return (
    <div className={cardClass} data-kpi-id={kpi.id || undefined}>
      <p className={`text-[11px] ${labelClass}`}>{kpi.label}</p>
      <p className={`mt-1 text-lg font-semibold leading-6 ${valueClass}`}>
        {formatScalar(kpi.value)}
        {kpi.unit ? <span className={`ml-1 text-xs font-normal ${labelClass}`}>{kpi.unit}</span> : null}
      </p>
      <p className={`mt-0.5 text-[11px] ${labelClass}`}>
        {delta === null
          ? kpi.hint || ''
          : `${delta >= 0 ? '▲' : '▼'} ${Math.abs(delta * 100).toFixed(1)}%${kpi.hint ? ` · ${kpi.hint}` : ''}`}
      </p>
    </div>
  );
}

function BoardHeader({ spec, dark }: { spec: DashboardSpec; dark: boolean }) {
  const source = spec.meta?.source || '';
  return (
    <header className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
      <div className="min-w-0">
        <h3 className={`truncate text-sm font-semibold ${dark ? 'text-slate-50' : 'text-[#1f2430]'}`}>
          {spec.title}
        </h3>
        {spec.subtitle ? (
          <p className={`truncate text-[11px] ${dark ? 'text-slate-400' : 'text-[#858b9c]'}`}>
            {spec.subtitle}
          </p>
        ) : null}
      </div>
      <div className={`flex items-center gap-2 text-[11px] ${dark ? 'text-slate-400' : 'text-[#9aa1ad]'}`}>
        {spec.as_of ? <span>数据截止 {spec.as_of}</span> : null}
        <span className={`rounded-full px-2 py-0.5 ${dark ? 'bg-cyan-500/10 text-cyan-300' : 'bg-[#f2f4f6]'}`}>
          {source ? `来源 ${source}` : '未标注来源'}
        </span>
      </div>
    </header>
  );
}

function FilterChips({ spec, dark }: { spec: DashboardSpec; dark: boolean }) {
  if (spec.filters.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-1.5">
      {spec.filters.map((filter) => (
        <span
          key={filter.id}
          className={`rounded-md border px-2 py-0.5 text-[11px] ${
            dark
              ? 'border-cyan-400/25 bg-cyan-500/5 text-cyan-200'
              : 'border-[#e4e8ec] bg-[#fafbfc] text-[#5f6675]'
          }`}
          title={filter.type}
        >
          {filter.label}
          {filter.default ? `：${filter.default}` : ''}
        </span>
      ))}
    </div>
  );
}

function Warnings({ spec, dark }: { spec: DashboardSpec; dark: boolean }) {
  const warnings = spec.warnings ?? [];
  if (warnings.length === 0) return null;
  const shown = warnings.slice(0, 2);
  const rest = warnings.length - shown.length;
  const restText = rest > 0 ? `另有 ${rest} 条提示` : '';
  return (
    <div
      className={`rounded-md border px-2 py-1 text-[11px] ${
        dark ? 'border-amber-400/25 bg-amber-500/5 text-amber-300' : 'border-amber-200 bg-amber-50 text-amber-700'
      }`}
      data-testid="dashboard-warnings"
    >
      {shown.map((warning) => (
        <p key={`${warning.code}-${warning.message}`} className="truncate" title={warning.message}>
          {warning.message}
        </p>
      ))}
      {restText ? <p>{restText}</p> : null}
    </div>
  );
}

function LegacyCanvas({ spec, dark }: { spec: DashboardSpec; dark: boolean }) {
  const hasBody = spec.kpis.length > 0 || spec.charts.length > 0;
  const shellClass = dark
    ? 'rounded-xl border border-cyan-400/20 bg-[#080f1c] p-3 text-slate-100'
    : 'flex flex-col gap-2';

  return (
    <div className={`${shellClass} flex flex-col gap-2`} data-theme={spec.theme || 'light'}>
      <BoardHeader spec={spec} dark={dark} />
      <FilterChips spec={spec} dark={dark} />

      {!hasBody ? (
        <p className={`rounded-lg border border-dashed px-3 py-6 text-center text-xs ${
          dark ? 'border-cyan-400/20 text-slate-400' : 'border-[#e4e8ec] text-[#858b9c]'
        }`}>
          暂无数据
        </p>
      ) : null}

      {spec.kpis.length > 0 ? (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
          {spec.kpis.map((kpi, index) => (
            <KpiCard key={`${kpi.label}-${index}`} kpi={kpi} dark={dark} />
          ))}
        </div>
      ) : null}

      {spec.charts.length > 0 ? (
        <div className="grid grid-cols-1 gap-2 lg:grid-cols-2">
          {spec.charts.map((chart) => (
            <Panel key={chart.id} chart={chart} dark={dark} wide={chart.span === 2} />
          ))}
        </div>
      ) : null}

      <p className={`text-[11px] ${dark ? 'text-slate-500' : 'text-[#5f6675]'}`}>
        <span>{spec.charts.length} 个图表</span>
        <span> · </span>
        <span>{spec.kpis.length} 个指标</span>
      </p>
    </div>
  );
}

function useContainerWidth() {
  const ref = useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(760);
  useEffect(() => {
    const node = ref.current;
    if (!node) return undefined;
    const update = () => setWidth(node.clientWidth || 760);
    update();
    if (typeof ResizeObserver !== 'undefined') {
      const observer = new ResizeObserver(update);
      observer.observe(node);
      return () => observer.disconnect();
    }
    window.addEventListener('resize', update);
    return () => window.removeEventListener('resize', update);
  }, []);
  return { ref, width };
}

function chartMap(spec: DashboardSpec): Map<string, DashboardChart> {
  return new Map(spec.charts.map((chart) => [chart.id, chart]));
}

function kpiMap(spec: DashboardSpec): Map<string, DashboardKpi> {
  const map = new Map<string, DashboardKpi>();
  spec.kpis.forEach((kpi, index) => map.set(kpiKey(kpi, index), kpi));
  return map;
}

function BubbleLayout({ spec, layout, dark }: { spec: DashboardSpec; layout: DashboardLayout; dark: boolean }) {
  const { ref, width } = useContainerWidth();
  const charts = chartMap(spec);
  const kpis = kpiMap(spec);
  const kpiSlots = orderedSlots(layout, 'kpi');
  const chartSlots = orderedSlots(layout, 'chart');
  const spans = new Map<string, number>();
  const rows = new Map<number, DashboardSlot[]>();
  for (const slot of chartSlots) {
    const row = rows.get(slot.y) || [];
    row.push(slot);
    rows.set(slot.y, row);
  }
  for (const row of rows.values()) {
    const values = bubbleRowSpans(row, width);
    row.forEach((slot, index) => spans.set(slot.id, values[index]));
  }
  const placedCharts = new Set(chartSlots.map((slot) => slot.id));
  const extraCharts = spec.charts.filter((chart) => !placedCharts.has(chart.id));
  const shellClass = dark
    ? 'rounded-xl border border-cyan-400/20 bg-[#080f1c] p-3 text-slate-100'
    : 'flex flex-col gap-2';

  return (
    <div
      ref={ref}
      className={`${shellClass} flex flex-col gap-2`}
      data-theme={spec.theme || 'light'}
      data-spec-version="2"
      data-mode="bubble"
    >
      <BoardHeader spec={spec} dark={dark} />
      <Warnings spec={spec} dark={dark} />
      <FilterChips spec={spec} dark={dark} />

      {kpiSlots.length > 0 ? (
        <div
          className="grid gap-2"
          style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))' }}
        >
          {kpiSlots.map((slot: DashboardSlot) => {
            const kpi = kpis.get(slot.id);
            return kpi ? <KpiCard key={slot.id} kpi={kpi} dark={dark} /> : null;
          })}
        </div>
      ) : null}

      {chartSlots.length > 0 || extraCharts.length > 0 ? (
        <div className="grid gap-2" style={{ gridTemplateColumns: 'repeat(12, minmax(0, 1fr))' }}>
          {chartSlots.map((slot) => {
            const chart = charts.get(slot.id);
            if (!chart) return null;
            return (
              <div key={slot.id} style={{ gridColumn: `span ${spans.get(slot.id) ?? bubbleRowSpans([slot], width)[0]}` }}>
                <Panel chart={chart} dark={dark} height={240} />
              </div>
            );
          })}
          {extraCharts.map((chart) => (
            <div key={chart.id} style={{ gridColumn: `span ${chart.span === 2 ? 12 : 6}` }}>
              <Panel chart={chart} dark={dark} height={240} />
            </div>
          ))}
        </div>
      ) : null}

      <p className={`text-[11px] ${dark ? 'text-slate-500' : 'text-[#5f6675]'}`}>
        <span>{spec.charts.length} 个图表</span>
        <span> · </span>
        <span>{spec.kpis.length} 个指标</span>
      </p>
    </div>
  );
}

function WallLayout({ spec, layout, dark }: { spec: DashboardSpec; layout: DashboardLayout; dark: boolean }) {
  const grid = gridOf(layout);
  const charts = chartMap(spec);
  const kpis = kpiMap(spec);
  const slots = orderedSlots(layout);

  return (
    <div
      className="dash-wall"
      data-theme={spec.theme || 'light'}
      data-spec-version="2"
      data-mode="wall"
      style={{ position: 'relative', width: grid.width, height: grid.height }}
    >
      <div
        className="dash-wall-header"
        style={{ position: 'absolute', left: 24, right: 24, top: 8, height: 74, display: 'flex', alignItems: 'center' }}
      >
        <div className="w-full">
          <BoardHeader spec={spec} dark={dark} />
        </div>
      </div>
      {slots.map((slot) => {
        const rect = slotRect(slot, grid);
        const chart = slot.kind === 'chart' ? charts.get(slot.id) : undefined;
        const kpi = slot.kind === 'kpi' ? kpis.get(slot.id) : undefined;
        return (
          <div
            key={slot.id}
            data-slot-id={slot.id}
            data-slot-region={slot.region}
            style={{
              position: 'absolute',
              left: rect.left + 8,
              top: rect.top + 6,
              width: rect.width - 16,
              height: rect.height - 12,
            }}
          >
            {chart ? (
              <Panel chart={chart} dark={dark} height={wallChartHeight(slot, grid)} wall />
            ) : null}
            {kpi ? <KpiCard kpi={kpi} dark={dark} wall /> : null}
          </div>
        );
      })}
    </div>
  );
}

export default function DashboardCanvas({ spec, mode = 'bubble' }: { spec: DashboardSpec; mode?: CanvasMode }) {
  const dark = spec.theme === 'tech' || spec.theme === 'dark';
  const layout = layoutOf(spec);
  if (!layout) return <LegacyCanvas spec={spec} dark={dark} />;
  return mode === 'wall' ? (
    <WallLayout spec={spec} layout={layout} dark={dark} />
  ) : (
    <BubbleLayout spec={spec} layout={layout} dark={dark} />
  );
}
