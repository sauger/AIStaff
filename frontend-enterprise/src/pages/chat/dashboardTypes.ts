export type DashboardChartType =
  | 'line'
  | 'area'
  | 'column'
  | 'bar'
  | 'stacked_column'
  | 'pie'
  | 'donut'
  | 'scatter'
  | 'gauge'
  | 'funnel'
  | 'table';
export type DashboardColumnType = 'string' | 'number' | 'date' | 'datetime' | 'boolean';
export type DashboardScalar = string | number | boolean | null;

export type DashboardColumn = {
  name: string;
  type?: DashboardColumnType;
};

export type DashboardTableData = {
  columns: DashboardColumn[];
  rows: DashboardScalar[][];
};

export type DashboardChart = {
  id: string;
  title?: string | null;
  intent?: 'trend' | 'compare' | 'composition' | 'detail' | 'kpi' | null;
  type: DashboardChartType;
  encoding: Record<string, string>;
  data: DashboardTableData;
  note?: string | null;
  /** 1 = half row, 2 = full row on the dashboard grid. */
  span?: 1 | 2;
};

export type DashboardKpi = {
  label: string;
  value: DashboardScalar;
  unit?: string | null;
  delta?: number | null;
  hint?: string | null;
};

export type DashboardFilter = {
  id: string;
  label: string;
  type: 'select' | 'date_range' | 'text';
  options?: string[];
  default?: string | null;
};

export type DashboardSpec = {
  spec_version: number;
  title: string;
  subtitle?: string | null;
  as_of?: string | null;
  theme?: 'light' | 'dark' | 'tech';
  kpis: DashboardKpi[];
  charts: DashboardChart[];
  filters: DashboardFilter[];
  meta?: { source?: string | null; generated_by?: string };
};

export type DashboardViewPayload = {
  id: string;
  title: string;
  spec: DashboardSpec;
  created_at: string;
  expires_at: string;
};

export type DashboardViewDescriptor = {
  view_id: string;
  tenant_id?: string | null;
  title: string;
  kpi_count: number;
  chart_count: number;
};
