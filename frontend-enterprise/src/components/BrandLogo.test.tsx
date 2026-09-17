// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import BrandLogo from './BrandLogo';

afterEach(cleanup);

describe('BrandLogo', () => {
  it('renders the RapidStaff wordmark and light mark by default', () => {
    render(<BrandLogo />);

    expect(screen.getByText('RapidStaff')).toBeTruthy();
    expect(screen.getByAltText('RapidStaff')).toBeTruthy();
    expect(screen.queryByText('StaffDeck')).toBeNull();
  });

  it('hides the wordmark when markOnly is set', () => {
    render(<BrandLogo markOnly />);

    expect(screen.queryByText('RapidStaff')).toBeNull();
    expect(screen.getByAltText('RapidStaff')).toBeTruthy();
  });
});
