// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { DashboardSpec } from '../dashboardTypes';

import DashboardStage from './DashboardStage';

vi.mock('./DashboardCanvas', () => ({
  default: ({ mode }: { mode?: string }) => <div data-testid="wall-canvas" data-mode={mode} />,
}));

const spec: DashboardSpec = {
  spec_version: 2,
  title: '全屏看板',
  kpis: [{ id: 'kpi_a', label: '销售额', value: 1 }],
  charts: [],
  filters: [],
  layout: {
    template: 'overview',
    grid: { columns: 12, rows: 12, width: 1920, height: 1080 },
    overflow: 'fit',
    slots: [{ id: 'kpi_a', kind: 'kpi', region: 'kpi_row', x: 0, y: 1, w: 3, h: 1 }],
  },
};

beforeEach(() => {
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1920 });
  Object.defineProperty(window, 'innerHeight', { configurable: true, value: 1080 });
});

afterEach(() => {
  cleanup();
  document.body.style.overflow = '';
});

describe('DashboardStage', () => {
  it('portals a fixed 1920x1080 stage into body and restores body scroll', () => {
    const onClose = vi.fn();
    const { unmount } = render(<DashboardStage spec={spec} onClose={onClose} />);

    const dialog = screen.getByRole('dialog', { name: '数据大屏全屏' });
    expect(dialog).toBeTruthy();
    expect(screen.getByTestId('wall-canvas').getAttribute('data-mode')).toBe('wall');
    const canvas = dialog.querySelector('.dash-stage-canvas') as HTMLElement;
    expect(canvas.style.transform).toContain('scale(1)');
    expect(document.body.style.overflow).toBe('hidden');

    unmount();
    expect(document.body.style.overflow).toBe('');
  });

  it('closes on Escape and on the close button', () => {
    const onClose = vi.fn();
    render(<DashboardStage spec={spec} onClose={onClose} />);

    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole('button', { name: '退出全屏' }));
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it('scales down for a 1366x768 viewport', () => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1366 });
    Object.defineProperty(window, 'innerHeight', { configurable: true, value: 768 });
    render(<DashboardStage spec={spec} onClose={vi.fn()} />);
    const canvas = screen.getByRole('dialog').querySelector('.dash-stage-canvas') as HTMLElement;
    expect(canvas.style.transform).toContain('scale(0.7');
  });
});
