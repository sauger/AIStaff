// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import AboutPage from './AboutPage';

afterEach(() => cleanup());

describe('AboutPage', () => {
  it('lists third-party components and licenses', () => {
    render(<AboutPage />);
    expect(screen.getByText('关于')).toBeTruthy();
    expect(screen.getByText('ECharts')).toBeTruthy();
    expect(screen.getAllByText('Apache-2.0').length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText('DataV-Vue3')).toBeTruthy();
    expect(screen.getAllByText('MIT').length).toBeGreaterThanOrEqual(1);
  });

  it('does not render external links', () => {
    const { container } = render(<AboutPage />);
    expect(container.querySelector('a')).toBeNull();
  });
});
