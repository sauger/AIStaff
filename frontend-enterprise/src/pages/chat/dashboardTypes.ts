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
export type DashboardChartRole = 'kpi' | 'trend' | 'composition' | 'ranking' | 'detail';
export type DashboardOrientation = 'vertical' | 'horizontal' | 'auto';
export type DashboardTemplate = 'overview' | 'compare' | 'pulse' | 'detail' | 'stack';
export type DashboardRegion = 'kpi_row' | 'hero' | 'left' | 'right' | 'grid' | 'detail';

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
  /** v2 only: block semantics, selection confidence and data provenance. */
  role?: DashboardChartRole | null;
  confidence?: number | null;
  source_path?: string | null;
  /** Only meaningful for `bar`; missing (v1) renders vertical bars. */
  orientation?: DashboardOrientation;
};

export type DashboardKpi = {
  /** v2 slot reference; v1 KPIs fall back to their index. */
  id?: string | null;
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

export type DashboardSlot = {
  id: string;
  kind: 'kpi' | 'chart';
  region: DashboardRegion;
  x: number;
  y: number;
  w: number;
  h: number;
};

export type DashboardGrid = {
  columns: number;
  rows: number;
  width: number;
  height: number;
};

export type DashboardLayout = {
  template: DashboardTemplate;
  grid: DashboardGrid;
  slots: DashboardSlot[];
  overflow: 'fit' | 'scroll';
};

export type DashboardWarning = {
  code: string;
  message: string;
  path?: string | null;
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
  meta?: { source?: string | null; generated_by?: string; generator_version?: string };
  /** v2 only; a missing layout renders the v1 grid. */
  layout?: DashboardLayout | null;
  warnings?: DashboardWarning[];
};

export type DashboardViewPayload = {
  id: string;
  title: string;
  spec: DashboardSpec;
  created_at: string;
  expires_at: string;
  /** Read-only link for people without an account; expires with the view. */
  share_url?: string | null;
  /** `local` when no public base URL is configured (link only works on this host/LAN). */
  share_scope?: 'public' | 'local';
};

export type PublicDashboardPayload = {
  title: string;
  spec: DashboardSpec;
  expires_at: string;
};

export type DashboardViewDescriptor = {
  view_id: string;
  tenant_id?: string | null;
  title: string;
  kpi_count: number;
  chart_count: number;
};
