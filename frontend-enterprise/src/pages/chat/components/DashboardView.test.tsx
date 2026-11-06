// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { DashboardViewDescriptor } from '../dashboardTypes';

import DashboardView from './DashboardView';

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
}));

vi.mock('@/api/client', () => {
  class ApiError extends Error {
    status: number;
    body: string;
    constructor(status: number, body: string, statusText: string) {
      super(body || statusText);
      this.name = 'ApiError';
      this.status = status;
      this.body = body;
    }
  }
  return { api: { get: mocks.get }, ApiError };
});

vi.mock('./dashboardEchartsRuntime', () => ({
  echarts: {
    init: () => ({ setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn() }),
  },
}));

vi.mock('./dashboardEcharts', () => ({
  CHART_HEIGHT: 240,
  buildChartOption: () => ({}),
}));

const notifyMocks = vi.hoisted(() => ({
  success: vi.fn(),
  info: vi.fn(),
  error: vi.fn(),
}));

vi.mock('@/components/ui/app-toast', () => ({ notify: notifyMocks }));

const descriptor: DashboardViewDescriptor = {
  view_id: 'dashview_abc',
  tenant_id: 'tenant_demo',
  title: '经营看板',
  kpi_count: 1,
  chart_count: 1,
};

const payload = {
  id: 'dashview_abc',
  title: '经营看板',
  created_at: '2026-11-05T00:00:00Z',
  expires_at: '2026-11-12T00:00:00Z',
  spec: {
    spec_version: 1,
    title: '经营看板',
    kpis: [{ label: '销售额', value: 128 }],
    charts: [
      {
        id: 'trend',
        title: '趋势',
        type: 'table' as const,
        encoding: {},
        data: { columns: [{ name: '月份' }], rows: [['2026-09']] },
      },
    ],
    filters: [],
    meta: { source: 'erp.sales' },
  },
};

beforeEach(() => {
  mocks.get.mockReset();
});

afterEach(() => {
  cleanup();
});

describe('DashboardView', () => {
  it('loads the spec and renders the canvas', async () => {
    mocks.get.mockResolvedValue(payload);
    render(<DashboardView descriptor={descriptor} />);

    expect(screen.getByText(/正在加载大屏/)).toBeTruthy();
    await waitFor(() => expect(screen.getByText('销售额')).toBeTruthy());
    expect(screen.getByRole('region', { name: '数据大屏' })).toBeTruthy();
    expect(mocks.get.mock.calls[0][0]).toContain('/api/chat/dashboard-views/dashview_abc');
    expect(mocks.get.mock.calls[0][0]).toContain('tenant_id=tenant_demo');
  });

  it('shows the expired state on 410', async () => {
    const { ApiError } = await import('@/api/client');
    mocks.get.mockRejectedValue(new ApiError(410, '', 'Gone'));
    render(<DashboardView descriptor={descriptor} />);
    await waitFor(() => expect(screen.getByText(/该大屏已过期/)).toBeTruthy());
  });

  it('offers a retry after a transport failure', async () => {
    mocks.get.mockRejectedValueOnce(new Error('network down'));
    mocks.get.mockResolvedValueOnce(payload);
    render(<DashboardView descriptor={descriptor} />);

    await waitFor(() => expect(screen.getByText(/大屏加载失败/)).toBeTruthy());
    await userEvent.click(screen.getByRole('button', { name: '重试' }));
    await waitFor(() => expect(screen.getByText('销售额')).toBeTruthy());
    expect(mocks.get).toHaveBeenCalledTimes(2);
  });
});


const v2Payload = {
  ...payload,
  share_url: 'http://localhost:8000/s/dashview_abc.token',
  share_scope: 'public' as const,
  spec: {
    spec_version: 2,
    title: '经营看板',
    kpis: [{ id: 'kpi_a', label: '销售额', value: 128 }],
    charts: [],
    filters: [],
    layout: {
      template: 'overview' as const,
      grid: { columns: 12, rows: 12, width: 1920, height: 1080 },
      overflow: 'fit' as const,
      slots: [{ id: 'kpi_a', kind: 'kpi' as const, region: 'kpi_row' as const, x: 0, y: 1, w: 3, h: 1 }],
    },
  },
};

describe('DashboardView v2 actions', () => {
  it('copies the share link and opens the fullscreen stage', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    });
    mocks.get.mockResolvedValue(v2Payload);
    render(<DashboardView descriptor={descriptor} />);

    await waitFor(() => expect(screen.getByRole('button', { name: '复制链接' })).toBeTruthy());
    await userEvent.click(screen.getByRole('button', { name: '复制链接' }));
    expect(writeText).toHaveBeenCalledWith(v2Payload.share_url);
    expect(notifyMocks.success).toHaveBeenCalled();

    await userEvent.click(screen.getByRole('button', { name: '全屏' }));
    expect(screen.getByRole('dialog', { name: '数据大屏全屏' })).toBeTruthy();
  });

  it('does not render v2-only actions for a v1 view', async () => {
    mocks.get.mockResolvedValue(payload);
    render(<DashboardView descriptor={descriptor} />);
    await waitFor(() => expect(screen.getByText('销售额')).toBeTruthy());
    expect(screen.queryByRole('button', { name: '全屏' })).toBeNull();
    expect(screen.queryByRole('button', { name: '复制链接' })).toBeNull();
  });
});
