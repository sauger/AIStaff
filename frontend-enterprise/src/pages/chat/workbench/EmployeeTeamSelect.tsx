import { useId, useRef, useState } from 'react';

export type TeamSelectOption = { id: string; name: string; count: number };

/**
 * 员工团队筛选：listbox 语义、人数徽标、方向键/Home/End 键盘导航。
 * 不依赖原生 popover（兼容测试环境），改为受控展开 + 外点关闭。
 */
export default function EmployeeTeamSelect({
  value,
  options,
  onChange,
}: {
  value: string;
  options: TeamSelectOption[];
  onChange: (next: string) => void;
}) {
  const menuId = useId();
  const rootRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const selected = options.find((option) => option.id === value) || options[0];

  const focusSelected = () => {
    window.requestAnimationFrame(() => {
      menuRef.current?.querySelector<HTMLElement>('[aria-selected="true"]')?.focus();
    });
  };

  const show = (focus = false) => {
    setOpen(true);
    if (focus) focusSelected();
  };

  const choose = (option: TeamSelectOption) => {
    onChange(option.id);
    setOpen(false);
    rootRef.current?.querySelector('button')?.focus();
  };

  const onMenuKeyDown = (event: React.KeyboardEvent) => {
    const buttons = Array.from(
      menuRef.current?.querySelectorAll<HTMLButtonElement>('[role="option"]') || [],
    );
    if (!buttons.length) return;
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const current = buttons.indexOf(document.activeElement as HTMLButtonElement);
    const next = event.key === 'Home'
      ? 0
      : event.key === 'End'
        ? buttons.length - 1
        : (current + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length;
    buttons[next]?.focus();
  };

  return (
    <div
      className="cw-filter-wrap"
      ref={rootRef}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node)) setOpen(false);
      }}
    >
      <button
        type="button"
        className="cw-team-select-trigger"
        aria-label="筛选员工"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={menuId}
        onClick={() => (open ? setOpen(false) : show())}
        onKeyDown={(event) => {
          if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            show(true);
          }
        }}
      >
        <span className="cw-filter-display">
          <span className="cw-filter-name">{selected?.name}</span>
          <span className="cw-count">{selected?.count}人</span>
        </span>
        <svg className="cw-chevron" width="12" height="12" viewBox="0 0 12 12" aria-hidden="true" style={{ transform: open ? 'rotate(180deg)' : undefined }}>
          <path d="M2 4l4 4 4-4" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
      {open && (
        <div
          ref={menuRef}
          id={menuId}
          className="cw-team-select-menu"
          role="listbox"
          aria-label="员工团队"
          onKeyDown={onMenuKeyDown}
        >
          {options.map((option, index) => (
            <div key={option.id} className="cw-team-option-row">
              {index === 1 && <div className="cw-team-select-label" role="presentation">团队</div>}
              <button
                type="button"
                role="option"
                aria-selected={value === option.id}
                tabIndex={value === option.id ? 0 : -1}
                onClick={() => choose(option)}
              >
                <span>{option.name}</span>
                <span className="cw-team-select-count">{option.count}人</span>
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
