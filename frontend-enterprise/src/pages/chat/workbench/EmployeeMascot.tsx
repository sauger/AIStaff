import { useId, useMemo } from 'react';

import {
  attachCompletedResult,
  inkWorkstationMarkup,
  portraitBody,
  rawArtFor,
  scopeSvg,
  type EmployeeArtId,
} from './employeeArt';
import { useEmployeeAppearance } from './useEmployeeAppearance';

export type EmployeeMotionState = 'idle' | 'thinking' | 'waiting' | 'clicked' | 'completed' | 'problem';

type EmployeeMascotProps = {
  artId: EmployeeArtId;
  /** 站位插画（工位全身）；portrait 为头像特写。 */
  portrait?: boolean;
  motion?: EmployeeMotionState;
  /** 工作完成未读：挥动完成标记，桌上放结果纸。 */
  resultReady?: boolean;
  label?: string;
  className?: string;
};

/**
 * 内联 SVG 员工插画。内联是为了让 CSS/JS 驱动呼吸、眨眼与眼珠跟随。
 * 渐变 id 按实例隔离，避免同屏多个相同角色串色。
 */
export default function EmployeeMascot({
  artId,
  portrait = false,
  motion = 'idle',
  resultReady = false,
  label,
  className = 'cw-employee-art',
}: EmployeeMascotProps) {
  const { appearance } = useEmployeeAppearance();
  const instanceId = useId().replace(/[^a-zA-Z0-9_-]/g, '');
  const markup = useMemo(() => {
    const raw = appearance === 'ink'
      ? inkWorkstationMarkup(artId, portrait)
      : rawArtFor(artId);
    const posed = portrait
      ? portraitBody(raw, appearance, artId)
      : (resultReady ? attachCompletedResult(raw) : raw);
    const scope = `${appearance}-${portrait ? `${artId}-portrait` : artId}-${resultReady ? 'result-' : ''}${instanceId}`;
    return scopeSvg(posed, scope);
  }, [artId, appearance, instanceId, portrait, resultReady]);

  return (
    <div
      className={className}
      data-motion={motion}
      data-appearance={appearance}
      data-result-ready={resultReady ? 'true' : undefined}
      aria-hidden={label ? undefined : true}
      role={label ? 'img' : undefined}
      aria-label={label}
      dangerouslySetInnerHTML={{ __html: markup }}
    />
  );
}
