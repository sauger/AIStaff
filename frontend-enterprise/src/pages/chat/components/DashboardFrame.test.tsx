// @vitest-environment jsdom

import { cleanup, render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import DvBorderBox1 from '@/components/datav/DvBorderBox1';
import DvBorderBox2 from '@/components/datav/DvBorderBox2';
import DvBorderBox10 from '@/components/datav/DvBorderBox10';
import DvBorderBox13 from '@/components/datav/DvBorderBox13';
import DvDecoration5 from '@/components/datav/DvDecoration5';
import DvDecoration8 from '@/components/datav/DvDecoration8';
import DvDecoration11 from '@/components/datav/DvDecoration11';

import DashboardFrame from './DashboardFrame';

vi.mock('@/components/datav/datav.css', () => ({}));

afterEach(() => cleanup());

describe('datav assets', () => {
  it('renders all seven pieces without scripts or remote references', () => {
    const { container } = render(
      <div>
        <DvBorderBox1><span>a</span></DvBorderBox1>
        <DvBorderBox2><span>b</span></DvBorderBox2>
        <DvBorderBox10><span>c</span></DvBorderBox10>
        <DvBorderBox13><span>d</span></DvBorderBox13>
        <DvDecoration5 />
        <DvDecoration8 />
        <DvDecoration11><span>e</span></DvDecoration11>
      </div>,
    );
    expect(container.querySelectorAll('svg').length).toBeGreaterThanOrEqual(7);
    expect(container.querySelector('script')).toBeNull();
    expect(container.innerHTML).not.toContain('http://');
    expect(container.innerHTML).not.toContain('https://');
    expect(container.innerHTML).not.toContain('dangerously');
  });

  it('marks decorative SVGs aria-hidden', () => {
    const { container } = render(<DvBorderBox2 />);
    const svg = container.querySelector('svg');
    expect(svg?.getAttribute('aria-hidden')).toBe('true');
  });

  it('wraps the wall canvas in the frame', () => {
    const { container } = render(<DashboardFrame><div data-testid="inner" /></DashboardFrame>);
    expect(container.querySelector('.dash-frame')).toBeTruthy();
    expect(container.querySelector('.dash-frame-inner')).toBeTruthy();
  });
});
