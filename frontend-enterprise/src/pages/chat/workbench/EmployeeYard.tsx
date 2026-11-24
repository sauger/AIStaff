import { useEffect, useState } from 'react';

import { ART_ASSETS } from './employeeArt';
import EmployeeTeamSelect, { type TeamSelectOption } from './EmployeeTeamSelect';
import EmployeeYardCard, { type YardEmployee } from './EmployeeYardCard';

const ALL_SEAT_LIMIT = 10;
const TEAM_SEAT_LIMIT = 8;

/** 工位网格满 10 个（团队 8 个）后不再补空闲工位；员工本身不截断。 */
function emptyDeskFits(count: number, teamSelected: boolean): boolean {
  return count < (teamSelected ? TEAM_SEAT_LIMIT : ALL_SEAT_LIMIT);
}

/**
 * 员工工位栏：380px，2×170 卡片网格；团队筛选跟随当前会话。
 * 工位数据全部来自后端员工列表，不做人数截断；
 * 只在网格还有空位时补一个空闲工位。
 */
export default function EmployeeYard({
  employees,
  teams,
  selectedId,
  onSelect,
  welcome = false,
  collapsed = false,
  onCollapsedChange,
  defaultTeamId = 'all',
  className,
}: {
  employees: YardEmployee[];
  teams: Array<{ id: string; name: string; memberIds: string[] }>;
  selectedId?: string;
  onSelect?: (employee: YardEmployee) => void;
  welcome?: boolean;
  collapsed?: boolean;
  onCollapsedChange?: (next: boolean) => void;
  defaultTeamId?: string;
  className?: string;
}) {
  const [filter, setFilter] = useState(defaultTeamId || 'all');
  useEffect(() => {
    setFilter(defaultTeamId || 'all');
  }, [defaultTeamId]);
  const team = teams.find((item) => item.id === filter);
  const matched = team
    ? employees.filter((employee) => team.memberIds.includes(employee.id))
    : employees;
  const visible = matched;

  const options: TeamSelectOption[] = [
    { id: 'all', name: '全部员工', count: employees.length },
    ...teams.map((item) => ({
      id: item.id,
      name: item.name,
      count: employees.filter((employee) => item.memberIds.includes(employee.id)).length,
    })),
  ];

  const showEmptyDesk = emptyDeskFits(visible.length, Boolean(team));

  if (welcome) {
    return (
      <aside className={`cw-employees-panel cw-welcome-yard ${className || ''}`} aria-label="数字员工">
        <div className="cw-employee-grid">
          {visible.map((employee, index) => (
            <EmployeeYardCard
              key={employee.id}
              employee={employee}
              onSelect={onSelect}
              phase={(index * 0.61) % 3}
            />
          ))}
          {showEmptyDesk && <EmptyDesk />}
        </div>
      </aside>
    );
  }

  return (
    <aside
      className={`cw-employees-panel ${collapsed ? 'cw-collapsed' : ''} ${className || ''}`}
      aria-label="数字员工"
    >
      <div className="cw-employee-toolbar">
        {!collapsed && (
          <EmployeeTeamSelect value={filter} options={options} onChange={setFilter} />
        )}
        <button
          type="button"
          className="cw-collapse-button"
          aria-label={collapsed ? '展开员工面板' : '收起员工面板'}
          aria-expanded={!collapsed}
          onClick={() => onCollapsedChange?.(!collapsed)}
        >
          <svg
            width="16"
            height="16"
            viewBox="0 0 16 16"
            aria-hidden="true"
            style={{ transform: collapsed ? 'rotate(180deg)' : undefined }}
          >
            <path d="M10 3L5 8l5 5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
      </div>
      {!collapsed && (
        <div className="cw-employee-grid">
          {visible.map((employee, index) => (
            <EmployeeYardCard
              key={employee.id}
              employee={employee}
              selected={selectedId === employee.id}
              onSelect={onSelect}
              phase={(index * 0.61) % 3}
            />
          ))}
          {showEmptyDesk && <EmptyDesk />}
          {visible.length === 0 && <p className="cw-yard-empty">该团队暂无员工</p>}
        </div>
      )}
    </aside>
  );
}

function EmptyDesk() {
  return (
    <div className="cw-empty-desk">
      <img src={ART_ASSETS.empty} alt="空闲工位" />
    </div>
  );
}
