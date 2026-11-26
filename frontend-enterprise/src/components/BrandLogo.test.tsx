// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import BrandLogo from './BrandLogo';

afterEach(cleanup);

describe('BrandLogo', () => {
  it('renders the RapidStaff wordmark and light mark by default', () => {
    render(<BrandLogo />);

    expect(screen.getByAltText('RapidStaff')).toBeTruthy();
    expect(screen.getByTestId('brand-wordmark')).toBeTruthy();
  });

  it('hides the wordmark when markOnly is set', () => {
    render(<BrandLogo markOnly />);

    expect(screen.queryByTestId('brand-wordmark')).toBeNull();
    expect(screen.getByAltText('RapidStaff')).toBeTruthy();
  });
});
