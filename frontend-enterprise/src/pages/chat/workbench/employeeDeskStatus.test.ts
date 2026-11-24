import { describe, expect, it } from 'vitest';

import { compareDeskStatus, employeeDeskStatus } from './employeeDeskStatus';

describe('employeeDeskStatus', () => {
  it('shows a running employee at the laptop', () => {
    expect(employeeDeskStatus({ online: true, running: true, unread: false })).toBe('working');
  });

  it('shows an offline employee even if a session is still marked running', () => {
    expect(employeeDeskStatus({ online: false, running: true, unread: true })).toBe('offline');
  });

  it('shows a finished unread result ahead of the idle pose', () => {
    expect(employeeDeskStatus({ online: true, running: false, unread: true })).toBe('unread');
  });

  it('keeps a running session ahead of an unread one', () => {
    expect(employeeDeskStatus({ online: true, running: true, unread: true })).toBe('working');
  });

  it('sorts offline employees after everyone else', () => {
    expect(compareDeskStatus('idle', 'offline')).toBeLessThan(0);
    expect(compareDeskStatus('working', 'unread')).toBe(0);
  });
});
