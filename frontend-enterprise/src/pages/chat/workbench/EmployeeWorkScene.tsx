import { useMemo } from 'react';

import workDeskUrl from '@/assets/employee-art/work-desk.svg';

import { INK_COLORS, WORK_SCENE_ASSETS, inkMarkup, type EmployeeArtId } from './employeeArt';
import { useEmployeeAppearance } from './useEmployeeAppearance';

type EmployeeWorkSceneProps = {
  artId: EmployeeArtId;
  name: string;
  color?: string;
  paused?: boolean;
  replayKey?: string | number;
};

function inkColorFor(artId: keyof typeof INK_COLORS, color?: string): string {
  return color || INK_COLORS[artId];
}

/** 员工「正在使用电脑工作」：取出笔记本、低头、双手敲击。 */
export default function EmployeeWorkScene({
  artId,
  name,
  color,
  paused = false,
  replayKey,
}: EmployeeWorkSceneProps) {
  const { appearance } = useEmployeeAppearance();
  const { actorMarkup, handsMarkup } = useMemo(() => {
    if (appearance === 'ink') {
      return {
        actorMarkup: inkMarkup(artId, true, true, color),
        handsMarkup: WORK_SCENE_ASSETS.hands.replace(/fill="black"/g, `fill="${inkColorFor(artId)}"`),
      };
    }
    return { actorMarkup: WORK_SCENE_ASSETS.body, handsMarkup: WORK_SCENE_ASSETS.hands };
  }, [appearance, artId, color]);
  const sweatMarkup = useMemo(
    () => WORK_SCENE_ASSETS.sweat
      .replace(/stroke-opacity="[^"]*"/g, '')
      .replace('<svg ', '<svg class="cw-work-sweat" aria-hidden="true" '),
    [],
  );

  return (
    <div
      key={replayKey}
      className={['cw-work-scene', 'is-working', paused ? 'is-paused' : ''].filter(Boolean).join(' ')}
      style={{ ['--cw-scarf' as string]: inkColorFor(artId, color) }}
      role="img"
      aria-label={`${name}正在使用电脑工作`}
    >
      <img className="cw-work-desk" src={workDeskUrl} alt="" />
      <div className="cw-work-actor-mask">
        <div
          className={['cw-work-actor', appearance === 'ink' ? 'cw-ink-work-actor' : ''].filter(Boolean).join(' ')}
          data-appearance={appearance}
          dangerouslySetInnerHTML={{ __html: actorMarkup + (paused ? '' : sweatMarkup) }}
        />
      </div>
      <div className="cw-work-hands-layer" dangerouslySetInnerHTML={{ __html: handsMarkup }} />
      <div className="cw-work-computer" dangerouslySetInnerHTML={{ __html: WORK_SCENE_ASSETS.laptop }} />
    </div>
  );
}
