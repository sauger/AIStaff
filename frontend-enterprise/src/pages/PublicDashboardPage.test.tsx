// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import PublicDashboardPage from './PublicDashboardPage';

const mocks = vi.hoisted(() => ({ get: vi.fn() }));

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

vi.mock('./chat/components/DashboardCanvas', () => ({
  default: () => <div data-testid="public-canvas" />,
}));

vi.mock('./chat/components/DashboardStage', () => ({
  default: () => <div data-testid="public-stage" />,
}));

const payload = {
  title: '经营看板',
  expires_at: '2026-11-12T00:00:00Z',
  spec: {
    spec_version: 2,
    title: '经营看板',
    kpis: [{ id: 'kpi_a', label: '销售额', value: 1 }],
    charts: [],
    filters: [],
    layout: {
      template: 'overview',
      grid: { columns: 12, rows: 12, width: 1920, height: 1080 },
      overflow: 'fit',
      slots: [{ id: 'kpi_a', kind: 'kpi', region: 'kpi_row', x: 0, y: 1, w: 3, h: 1 }],
    },
  },
};

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/s/token']}>
      <Routes>
        <Route path="/s/:token" element={<PublicDashboardPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => cleanup());

describe('PublicDashboardPage', () => {
  it('renders a shared board without login chrome', async () => {
    mocks.get.mockResolvedValue(payload);
    renderPage();
    expect(screen.getByText(/正在加载大屏/)).toBeTruthy();
    await waitFor(() => expect(screen.getByTestId('public-canvas')).toBeTruthy());
    expect(screen.getByText('只读分享', { exact: false })).toBeTruthy();
    expect(mocks.get.mock.calls[0][0]).toBe('/api/public/dashboards/token');
  });

  it('shows the invalid page for 404/410', async () => {
    const { ApiError } = await import('@/api/client');
    mocks.get.mockRejectedValue(new ApiError(410, '', 'Gone'));
    renderPage();
    await waitFor(() => expect(screen.getByText('链接已失效')).toBeTruthy());
  });

  it('offers a retry on transport errors', async () => {
    mocks.get.mockRejectedValueOnce(new Error('offline'));
    mocks.get.mockResolvedValueOnce(payload);
    renderPage();
    await waitFor(() => expect(screen.getByText(/大屏加载失败/)).toBeTruthy());
    screen.getByRole('button', { name: '重试' }).click();
    await waitFor(() => expect(screen.getByTestId('public-canvas')).toBeTruthy());
  });
});
