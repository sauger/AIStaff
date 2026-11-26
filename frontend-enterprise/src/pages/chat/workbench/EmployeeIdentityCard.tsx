import { useId, type CSSProperties } from 'react';

import { employeeAvatarSrc, inkAvatarMarkup, type EmployeeArtId } from './employeeArt';
import { useEmployeeAppearance } from './useEmployeeAppearance';

export type IdentityCardEmployee = {
  name: string;
  artId: EmployeeArtId;
  roleName: string;
  intro: [string, string];
  stats: Array<{ value: string | number; label: string }>;
  /** 缺省视为在线，避免没传状态的卡片被画成离线。 */
  online?: boolean;
  /** 该员工的墨团主色，保证同屏颜色不重复。 */
  color?: string;
};

/** 393×350 portal 身份卡：毛玻璃小卡 + 头像 + 简介 + 资料/技能/SOP 统计。 */
export default function EmployeeIdentityCard({
  employee,
  position,
}: {
  employee: IdentityCardEmployee;
  position: { left: number; top: number };
}) {
  const tooltipId = useId();
  const { appearance } = useEmployeeAppearance();
  const online = employee.online !== false;

  return (
    <div id={tooltipId} role="tooltip" className="cw-employee-identity" style={position as CSSProperties}>
      <div className="cw-identity-surface" />
      <div className="cw-identity-avatar">
        {appearance === 'ink' ? (
          <span className="cw-ink-avatar" dangerouslySetInnerHTML={{ __html: inkAvatarMarkup(employee.artId, employee.color) }} />
        ) : (
          <img src={employeeAvatarSrc(employee.artId)} alt={`${employee.name}头像`} />
        )}
      </div>
      <div className="cw-identity-heading">
        <span className="cw-identity-name-row">
          <strong>{employee.name}</strong>
          <span className={`cw-identity-status ${online ? 'is-online' : 'is-offline'}`}>
            <i aria-hidden="true" />
            {online ? '在线' : '离线'}
          </span>
        </span>
        <span>{employee.roleName}</span>
      </div>
      <div className="cw-identity-rule" />
      <div className="cw-identity-intro">
        <span>员工简介</span>
        <p>
          {employee.intro[0]}
          <br />
          {employee.intro[1]}
        </p>
      </div>
      <div className="cw-identity-stats">
        {employee.stats.map((item) => (
          <div key={item.label}>
            <strong>{item.value}</strong>
            <span>{item.label}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
