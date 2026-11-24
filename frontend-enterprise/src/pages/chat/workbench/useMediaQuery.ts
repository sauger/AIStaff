import { useEffect, useState } from 'react';

/** 订阅媒体查询；SSR/测试环境（无 matchMedia）返回 fallback。 */
export function useMediaQuery(query: string, fallback = false): boolean {
  const [matches, setMatches] = useState(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return fallback;
    return window.matchMedia(query).matches;
  });

  useEffect(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return;
    const media = window.matchMedia(query);
    const listener = (event: MediaQueryListEvent) => setMatches(event.matches);
    setMatches(media.matches);
    media.addEventListener('change', listener);
    return () => media.removeEventListener('change', listener);
  }, [query]);

  return matches;
}

export function useWorkbenchBreakpoints() {
  return {
    isTablet: useMediaQuery('(max-width: 1023px)'),
    isMobile: useMediaQuery('(max-width: 767px)'),
  };
}
