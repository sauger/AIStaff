// @vitest-environment jsdom

import { describe, expect, it } from 'vitest';

import {
  EMPLOYEE_ART_IDS,
  INK_COLORS,
  artForRole,
  attachCompletedResult,
  inkMarkup,
  inkWorkstationMarkup,
  portraitBody,
  scopeSvg,
} from './employeeArt';

describe('employeeArt', () => {
  it('maps common roles to prototype art ids', () => {
    expect(artForRole('project-board', '项目管理员工')).toBe('pm');
    expect(artForRole('', '产品经理')).toBe('product');
    expect(artForRole('backend', '后端开发')).toBe('backend');
    expect(artForRole('', '测试工程师')).toBe('qa');
    expect(artForRole('', '销售总监')).toBe('sales');
  });

  it('hashes unknown roles into the nine art ids deterministically', () => {
    const first = artForRole('unknown-x', '神秘岗位');
    expect(EMPLOYEE_ART_IDS).toContain(first);
    expect(artForRole('unknown-x', '神秘岗位')).toBe(first);
  });

  it('suffixes both id= and url(#...) references when scoping', () => {
    const raw = '<svg><defs><linearGradient id="g1"/><filter id="f1"/></defs>'
      + '<rect fill="url(#g1)" filter="url(#f1)"/></svg>';
    const scoped = scopeSvg(raw, 'pm');
    expect(scoped).toContain('id="g1-pm"');
    expect(scoped).toContain('id="f1-pm"');
    expect(scoped).toContain('url(#g1-pm)');
    expect(scoped).toContain('url(#f1-pm)');
    expect(scoped).not.toContain('url(#g1)"');
    expect(scopeSvg(raw, 'pm')).toBe(scoped);
    expect(scopeSvg(raw, 'qa')).not.toBe(scoped);
  });

  it('colors the ink mascot and wraps the eyes in a gaze group', () => {
    const markup = inkMarkup('token');
    expect(markup).toContain(`fill="${INK_COLORS.token}"`);
    expect(markup).not.toContain('fill="black"');
    expect(markup).toContain('class="idle-gaze"');
    expect(markup).toContain('class="ink-eye"');
  });

  it('drops the ink arms while the character is typing', () => {
    expect(inkMarkup('sales', true, true)).not.toContain('ink-arm');
    expect(inkMarkup('sales')).toContain('ink-arm');
  });

  it('attaches the waving completed sign and the desk paper', () => {
    const markup = attachCompletedResult(inkWorkstationMarkup('sales'), '/assets/completed-sign.svg');
    expect(markup).toContain('class="summon-wave"');
    expect(markup).toContain('href="/assets/completed-sign.svg"');
    expect(markup).toContain('class="result-paper"');
    expect(markup).toContain('visibility="hidden"');
  });

  it('produces a portrait crop with a tightened viewBox when a body exists', () => {
    const raw = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="10 189 190 171">'
      + '<g class="idle-body"><path d="M0 0h10v10H0z"/></g></svg>';
    const portrait = portraitBody(raw, 'original', 'pm');
    expect(portrait).toContain('viewBox="65 194 85 90"');
    expect(portrait).toContain('idle-body');
  });
});
