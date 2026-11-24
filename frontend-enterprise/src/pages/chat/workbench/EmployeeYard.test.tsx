// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import EmployeeYard from './EmployeeYard';
import { EmployeeAppearanceProvider } from './useEmployeeAppearance';
import type { YardEmployee } from './EmployeeYardCard';

afterEach(cleanup);

const employees: YardEmployee[] = [
  {
    id: 'agent-1', name: '项目经理', artId: 'pm', roleName: '项目管理员工',
    intro: ['统筹项目目标与团队分工，', '跟踪进度与交付。'],
    stats: [{ value: 0, label: '资料' }, { value: 4, label: '技能' }, { value: 0, label: 'SOP' }],
  },
  {
    id: 'agent-2', name: '测试工程师', artId: 'qa', roleName: '测试',
    intro: ['验证功能与关键业务流程，', '发现并跟踪问题。'],
    stats: [{ value: 1, label: '资料' }, { value: 2, label: '技能' }, { value: 0, label: 'SOP' }],
  },
];

const teams = [{ id: 'team-1', name: '增长团队', memberIds: ['agent-1'] }];

function renderYard(props: Partial<Parameters<typeof EmployeeYard>[0]> = {}) {
  return render(
    <EmployeeAppearanceProvider>
      <EmployeeYard
        employees={employees}
        teams={teams}
        {...props}
      />
    </EmployeeAppearanceProvider>,
  );
}

describe('EmployeeYard', () => {
  it('renders one workstation card per employee with an idle desk placeholder', () => {
    renderYard();
    expect(screen.getByRole('button', { name: '项目经理' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '测试工程师' })).toBeTruthy();
    expect(screen.getByAltText('空闲工位')).toBeTruthy();
  });

  it('marks the selected employee as pressed and shows the tick', () => {
    renderYard({ selectedId: 'agent-1' });
    const selected = screen.getByRole('button', { name: '项目经理' });
    expect(selected.getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByAltText('已选择')).toBeTruthy();
    expect(screen.getByRole('button', { name: '测试工程师' }).getAttribute('aria-pressed')).toBe('false');
  });

  it('invokes openDraftForAgent selection handler on click', () => {
    const onSelect = vi.fn();
    renderYard({ onSelect });
    fireEvent.click(screen.getByRole('button', { name: '测试工程师' }));
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ id: 'agent-2' }));
  });

  it('filters by team via the listbox', () => {
    renderYard();
    fireEvent.click(screen.getByRole('button', { name: '筛选员工' }));
    fireEvent.click(screen.getByRole('option', { name: /增长团队/ }));
    expect(screen.getByRole('button', { name: '项目经理' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: '测试工程师' })).toBeNull();
    expect(screen.queryByText('该团队暂无员工')).toBeNull();
  });

  it('starts on the active team and keeps one empty desk while the team has room', () => {
    renderYard({ defaultTeamId: 'team-1' });
    expect(screen.getByText('增长团队')).toBeTruthy();
    expect(screen.getByRole('button', { name: '项目经理' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: '测试工程师' })).toBeNull();
    expect(screen.getByAltText('空闲工位')).toBeTruthy();
  });

  it('drops the empty desk once the all-employee grid is full', () => {
    const many = Array.from({ length: 10 }, (_, index) => ({
      ...employees[0],
      id: `full-${index}`,
      name: `员工${index}`,
    }));
    renderYard({ employees: many });
    expect(screen.queryByAltText('空闲工位')).toBeNull();
    expect(screen.getAllByRole('button', { name: /^员工\d+$/ })).toHaveLength(10);
  });

  it('drops the empty desk once a team already fills eight seats', () => {
    const many = Array.from({ length: 8 }, (_, index) => ({
      ...employees[0],
      id: `team-seat-${index}`,
      name: `组员${index}`,
    }));
    renderYard({
      employees: many,
      teams: [{ id: 'team-1', name: '增长团队', memberIds: many.map((item) => item.id) }],
      defaultTeamId: 'team-1',
    });
    expect(screen.queryByAltText('空闲工位')).toBeNull();
    expect(screen.getAllByRole('button', { name: /^组员\d+$/ })).toHaveLength(8);
  });

  it('renders every employee returned by the backend without truncating', () => {
    const many = Array.from({ length: 12 }, (_, index) => ({
      ...employees[0],
      id: `backend-${index}`,
      name: `后端员工${index}`,
    }));
    renderYard({ employees: many });
    expect(screen.getAllByRole('button', { name: /^后端员工\d+$/ })).toHaveLength(12);
    expect(screen.getByRole('button', { name: '后端员工11' })).toBeTruthy();
    expect(screen.queryByAltText('空闲工位')).toBeNull();
  });

  it('does not add an eleventh welcome card for ten employees', () => {
    const many = Array.from({ length: 10 }, (_, index) => ({
      ...employees[0],
      id: `welcome-${index}`,
      name: `欢迎${index}`,
    }));
    renderYard({ welcome: true, employees: many });
    expect(screen.queryByAltText('空闲工位')).toBeNull();
    expect(screen.getAllByRole('button', { name: /^欢迎\d+$/ })).toHaveLength(10);
  });

  it('collapses to the narrow rail and hides the grid', () => {
    renderYard({ collapsed: true });
    expect(screen.getByRole('button', { name: '展开员工面板' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: '项目经理' })).toBeNull();
  });
});
