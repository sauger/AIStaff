import { cn } from '@/lib/utils';
import logoMarkDark from '../assets/rapid-logo-mark-dark.svg';
import logoMarkLight from '../assets/rapid-logo-mark.svg';
import logoWordmarkDark from '../assets/rapid-logo-wordmark-dark.svg';
import logoWordmarkLight from '../assets/rapid-logo-wordmark.svg';

export type BrandLogoProps = {
  /** Hide the RapidStaff wordmark and only render the logo mark. */
  markOnly?: boolean;
  /** Size of the square logo mark in pixels. */
  markSize?: number;
  className?: string;
  /** Extra classes applied to the wordmark wrapper (e.g. to hide it responsively). */
  wordmarkClassName?: string;
};

/** Brand logo lockup (mascot mark + RapidStaff wordmark). */
export default function BrandLogo({
  markOnly = false,
  markSize = 28,
  className,
  wordmarkClassName,
}: BrandLogoProps) {
  const wordmarkSize = Math.round(markSize * 0.62);

  return (
    <span
      className={cn('flex w-max shrink-0 items-center gap-[8px] overflow-hidden p-[4px]', className)}
    >
      <img
        src={logoMarkLight}
        alt="RapidStaff"
        className="shrink-0 object-contain in-data-[theme=dark]:hidden"
        style={{ height: markSize, width: 'auto' }}
      />
      <img
        src={logoMarkDark}
        alt=""
        aria-hidden="true"
        className="hidden shrink-0 object-contain in-data-[theme=dark]:block"
        style={{ height: markSize, width: 'auto' }}
      />
      {!markOnly && (
        <span
          data-testid="brand-wordmark"
          className={cn('flex flex-col items-center gap-[2px] leading-none', wordmarkClassName)}
        >
          <img
            src={logoWordmarkLight}
            alt=""
            aria-hidden="true"
            className="block object-contain in-data-[theme=dark]:hidden"
            style={{ height: wordmarkSize, width: 'auto' }}
          />
          <img
            src={logoWordmarkDark}
            alt=""
            aria-hidden="true"
            className="hidden object-contain in-data-[theme=dark]:block"
            style={{ height: wordmarkSize, width: 'auto' }}
          />
        </span>
      )}
    </span>
  );
}
