// @vitest-environment jsdom

import { cleanup, render } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import EmployeeMascot from './EmployeeMascot';
import { APPEARANCE_STORAGE_KEY, EmployeeAppearanceProvider } from './useEmployeeAppearance';

afterEach(cleanup);

function renderMascot(props: Parameters<typeof EmployeeMascot>[0]) {
  return render(
    <EmployeeAppearanceProvider>
      <EmployeeMascot {...props} />
    </EmployeeAppearanceProvider>,
  );
}

describe('EmployeeMascot', () => {
  it('renders inline svg artwork marked aria-hidden with motion state', () => {
    const { container } = renderMascot({ artId: 'pm', motion: 'thinking' });
    const host = container.firstElementChild as HTMLElement;
    expect(host.getAttribute('aria-hidden')).toBe('true');
    expect(host.getAttribute('data-motion')).toBe('thinking');
    expect(host.querySelector('svg')).toBeTruthy();
  });

  it('keeps idle motion by default and records the appearance', () => {
    const { container } = renderMascot({ artId: 'qa' });
    const host = container.firstElementChild as HTMLElement;
    expect(host.getAttribute('data-motion')).toBe('idle');
    expect(['ink', 'original']).toContain(host.getAttribute('data-appearance'));
  });

  it('scopes gradient ids per instance so multiple mascots do not collide', () => {
    const { container } = render(
      <EmployeeAppearanceProvider>
        <EmployeeMascot artId="pm" />
        <EmployeeMascot artId="pm" portrait />
        <EmployeeMascot artId="qa" />
      </EmployeeAppearanceProvider>,
    );
    const ids = Array.from(container.querySelectorAll('svg [id]')).map((el) => el.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it('waves the completed sign when a result is ready', () => {
    const { container } = renderMascot({ artId: 'sales', resultReady: true, motion: 'completed', label: '商务总监有未读完成结果' });
    const host = container.firstElementChild as HTMLElement;
    expect(host.getAttribute('data-result-ready')).toBe('true');
    expect(host.getAttribute('data-motion')).toBe('completed');
    expect(host.querySelector('.summon-wave')).toBeTruthy();
    expect(host.querySelector('.result-paper')).toBeNull();
  });

  it('raises the horizontal arm holding the sign in the original appearance', () => {
    window.localStorage.setItem(APPEARANCE_STORAGE_KEY, 'original');
    try {
      const { container } = renderMascot({ artId: 'sales', resultReady: true, motion: 'completed' });
      const host = container.firstElementChild as HTMLElement;
      expect(host.getAttribute('data-appearance')).toBe('original');
      expect(host.querySelector('.completed-arm')).toBeTruthy();
      expect(host.querySelector('.summon-wave image')).toBeTruthy();
    } finally {
      window.localStorage.removeItem(APPEARANCE_STORAGE_KEY);
    }
  });
});
