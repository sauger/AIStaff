import { useRef } from 'react';

import EmployeeMascot from './EmployeeMascot';
import type { EmployeeArtId } from './employeeArt';
import { useGazeFollow } from './useGazeFollow';

/** 头像特写（.idle-body 收紧取景），复用待机动效与眼珠跟随。 */
export default function EmployeeIdleAvatar({
  artId,
  color,
  name,
  className,
}: {
  artId: EmployeeArtId;
  color?: string;
  name: string;
  className?: string;
}) {
  const rootRef = useRef<HTMLSpanElement>(null);
  useGazeFollow(rootRef, { enabled: true, eyeOffsetY: 0.32 });
  return (
    <span ref={rootRef} className={`cw-employee-idle-avatar ${className || ''}`} role="img" aria-label={`${name}待机中`}>
      <EmployeeMascot artId={artId} color={color} portrait className="cw-employee-art cw-idle-portrait-art" />
    </span>
  );
}
