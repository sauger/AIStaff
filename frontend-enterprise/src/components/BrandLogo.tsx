import { cn } from '@/lib/utils';
import logoDark from '../assets/rapid-logo-dark.png';
import logoLight from '../assets/rapid-logo-light.png';

export type BrandLogoProps = {
  /** Hide the RapidStaff wordmark and only render the logo mark. */
  markOnly?: boolean;
  /** Size of the square logo mark in pixels. */
  markSize?: number;
  className?: string;
  /** Extra classes applied to the wordmark wrapper (e.g. to hide it responsively). */
  wordmarkClassName?: string;
};

/** Brand logo lockup (rpdnex.com Rapid mark + RapidStaff wordmark). */
export default function BrandLogo({
  markOnly = false,
  markSize = 28,
  className,
  wordmarkClassName,
}: BrandLogoProps) {
  return (
    <span className={cn('flex items-center gap-[8px] overflow-hidden p-[4px]', className)}>
      <img
        src={logoLight}
        alt="RapidStaff"
        className="shrink-0 object-contain in-data-[theme=dark]:hidden"
        style={{ width: markSize, height: markSize }}
      />
      <img
        src={logoDark}
        alt=""
        aria-hidden="true"
        className="hidden shrink-0 object-contain in-data-[theme=dark]:block"
        style={{ width: markSize, height: markSize }}
      />
      {!markOnly && (
        <span className={cn('flex flex-col items-center gap-[2px] leading-none', wordmarkClassName)}>
          <strong className="text-[17px] font-semibold leading-none text-[#18181a] in-data-[theme=dark]:text-[#f0f2f6]">
            RapidStaff
          </strong>
        </span>
      )}
    </span>
  );
}
