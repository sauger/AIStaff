import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from 'react';
import { createPortal } from 'react-dom';

import EmployeeIdentityCard, { type IdentityCardEmployee } from './EmployeeIdentityCard';
import EmployeeMascot from './EmployeeMascot';
import EmployeeWorkScene from './EmployeeWorkScene';
import { ART_ASSETS, type EmployeeArtId } from './employeeArt';
import { useGazeFollow } from './useGazeFollow';

import type { EmployeeDeskStatus } from './employeeDeskStatus';

export type YardEmployee = IdentityCardEmployee & {
  id: string;
  /** 每个员工独立的墨团主色。 */
  color?: string;
  /** 工位画法：工作中 / 离线 / 完成未读 / 空闲站立。 */
  deskStatus?: EmployeeDeskStatus;
};

/**
 * 员工工位卡：170×170，悬停/聚焦 160ms 弹身份卡（右侧优先，放不下翻到左侧），
 * Escape / 滚动关闭；空闲时眼珠跟随指针；运行中切换为工作场景。
 */
export default function EmployeeYardCard({
  employee,
  selected,
  onSelect,
  phase = 0,
}: {
  employee: YardEmployee;
  selected?: boolean;
  onSelect?: (employee: YardEmployee) => void;
  phase?: number;
}) {
  const triggerRef = useRef<HTMLButtonElement>(null);
  const hoverTimer = useRef<number>(0);
  const [position, setPosition] = useState<{ left: number; top: number } | null>(null);
  const deskStatus = employee.deskStatus ?? 'idle';

  useGazeFollow(triggerRef, { enabled: deskStatus === 'idle' });

  const close = useCallback(() => {
    window.clearTimeout(hoverTimer.current);
    setPosition(null);
  }, []);

  const open = useCallback((delay = 160) => {
    window.clearTimeout(hoverTimer.current);
    hoverTimer.current = window.setTimeout(() => {
      const rect = triggerRef.current?.getBoundingClientRect();
      if (!rect) return;
      const width = 393;
      const height = 350;
      const gap = 4;
      let left = rect.right + gap;
      if (left + width > window.innerWidth - 8) left = rect.left - width - gap;
      left = Math.max(8, Math.min(left, window.innerWidth - width - 8));
      setPosition({
        left,
        top: Math.max(8, Math.min(rect.top + rect.height / 2 - height / 2, window.innerHeight - height - 8)),
      });
    }, delay);
  }, []);

  useEffect(() => {
    const dismiss = (event: Event) => {
      if (
        event.type === 'scroll'
        && document.activeElement === triggerRef.current
        && triggerRef.current?.matches(':focus-visible')
      ) {
        open(0);
        return;
      }
      if (event.type !== 'keydown' || (event as KeyboardEvent).key === 'Escape') close();
    };
    window.addEventListener('scroll', dismiss, true);
    window.addEventListener('resize', dismiss);
    window.addEventListener('keydown', dismiss);
    return () => {
      window.clearTimeout(hoverTimer.current);
      window.removeEventListener('scroll', dismiss, true);
      window.removeEventListener('resize', dismiss);
      window.removeEventListener('keydown', dismiss);
    };
  }, [close, open]);

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        className={deskStatus === 'offline' ? 'cw-employee is-offline' : 'cw-employee'}
        data-motion={deskStatus === 'working' ? 'working' : 'idle'}
        aria-pressed={selected}
        aria-label={deskStatus === 'offline' ? `${employee.name}离线` : employee.name}
        style={{ ['--cw-phase' as string]: phase }}
        onClick={() => onSelect?.(employee)}
        onPointerEnter={(event) => {
          if (event.pointerType === 'mouse') open();
        }}
        onPointerLeave={close}
        onFocus={() => {
          if (triggerRef.current?.matches(':focus-visible')) open(0);
        }}
        onBlur={close}
      >
        <span className="cw-employee-name">
          <span className="cw-employee-name-text">{employee.name}</span>
          {selected && <img className="cw-selected-mark" src={ART_ASSETS.tick} alt="已选择" />}
        </span>
        {deskStatus === 'working' ? (
          <div className="cw-employee-working-art">
            <EmployeeWorkScene artId={employee.artId} name={employee.name} color={employee.color} />
          </div>
        ) : (
          <EmployeeMascot
            artId={employee.artId}
            color={employee.color}
            motion={deskStatus === 'unread' ? 'completed' : 'idle'}
            resultReady={deskStatus === 'unread'}
            label={deskStatus === 'unread' ? `${employee.name}有未读完成结果` : undefined}
          />
        )}
      </button>
      {position
        && createPortal(
          <EmployeeIdentityCard employee={employee} position={position} />,
          document.body,
        )}
    </>
  );
}

export type { EmployeeArtId };
