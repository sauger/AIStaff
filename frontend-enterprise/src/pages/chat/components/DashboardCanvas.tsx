import { useEffect, useRef } from 'react';

import type {
  DashboardChart,
  DashboardKpi,
  DashboardScalar,
  DashboardSpec,
  DashboardTableData,
} from '../dashboardTypes';

import { buildChartOption, CHART_HEIGHT } from './dashboardEcharts';

type ChartInstance = {
  setOption: (option: unknown) => void;
  resize: () => void;
  dispose: () => void;
};

function formatScalar(value: DashboardScalar): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'number') {
    return Number.isInteger(value) ? value.toLocaleString('zh-CN') : value.toFixed(2);
  }
  return String(value);
}

function ChartCanvas({ chart, dark }: { chart: DashboardChart; dark: boolean }) {
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const node = containerRef.current;
    if (!node) return undefined;
    let instance: ChartInstance | null = null;
    let observer: ResizeObserver | null = null;
    let disposed = false;

    // ECharts is heavy and dashboards are occasional: keep it in its own chunk.
    void import('./dashboardEchartsRuntime').then(({ echarts }) => {
      if (disposed || !containerRef.current) return;
      instance = echarts.init(node, undefined, { renderer: 'canvas' }) as ChartInstance;
      instance.setOption(buildChartOption(chart, dark));
      if (typeof ResizeObserver !== 'undefined') {
        observer = new ResizeObserver(() => instance?.resize());
        observer.observe(node);
      }
    });

    return () => {
      disposed = true;
      observer?.disconnect();
      instance?.dispose();
    };
  }, [chart, dark]);

  return <div ref={containerRef} className="w-full" style={{ height: CHART_HEIGHT }} />;
}

function DataTable({ data, dark }: { data: DashboardTableData; dark: boolean }) {
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
    <div className={`max-h-[240px] overflow-auto rounded-lg border ${dark ? 'border-[#1e2c44]' : 'border-[#eceef1]'}`}>
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
}: {
  chart: DashboardChart;
  dark: boolean;
  wide: boolean;
}) {
  const panelClass = dark
    ? 'relative rounded-lg border border-cyan-400/20 bg-[#0c1626]/90 p-3'
    : 'relative rounded-lg border border-[#e4e8ec] bg-white p-3';
  const titleClass = dark ? 'text-slate-100' : 'text-[#2f3542]';
  const noteClass = dark ? 'text-slate-400' : 'text-[#9aa1ad]';
  return (
    <section
      className={`${panelClass} ${wide ? 'col-span-1 lg:col-span-2' : ''}`}
      data-chart-id={chart.id}
      data-chart-type={chart.type}
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
        <h4 className={`flex items-center gap-1.5 text-[13px] font-medium ${titleClass}`}>
          {dark ? <span className="inline-block h-3 w-[3px] rounded bg-cyan-400" /> : null}
          {chart.title || chart.id}
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
        <DataTable data={chart.data} dark={dark} />
      ) : (
        <ChartCanvas chart={chart} dark={dark} />
      )}
      {chart.note ? <p className={`mt-1 text-[11px] leading-4 ${noteClass}`}>{chart.note}</p> : null}
    </section>
  );
}

function KpiCard({ kpi, dark }: { kpi: DashboardKpi; dark: boolean }) {
  const delta = typeof kpi.delta === 'number' ? kpi.delta : null;
  const cardClass = dark
    ? 'relative overflow-hidden rounded-lg border border-cyan-400/25 bg-gradient-to-br from-cyan-500/10 to-transparent px-3 py-2.5'
    : 'rounded-lg border border-[#e4e8ec] bg-white px-3 py-2.5';
  const labelClass = dark ? 'text-slate-400' : 'text-[#858b9c]';
  const valueClass = dark ? 'text-cyan-300' : 'text-[#1f2430]';
  return (
    <div className={cardClass}>
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

export default function DashboardCanvas({ spec }: { spec: DashboardSpec }) {
  const dark = spec.theme === 'tech' || spec.theme === 'dark';
  const hasBody = spec.kpis.length > 0 || spec.charts.length > 0;
  const source = spec.meta?.source || '';
  const shellClass = dark
    ? 'rounded-xl border border-cyan-400/20 bg-[#080f1c] p-3 text-slate-100'
    : 'flex flex-col gap-2';

  return (
    <div className={`${shellClass} flex flex-col gap-2`} data-theme={spec.theme || 'light'}>
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

      {spec.filters.length > 0 ? (
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
      ) : null}

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
