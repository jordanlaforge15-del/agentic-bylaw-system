// Subscribe to a CSS media-query result. Returns `false` on the server
// and on the first client render (to keep SSR/hydration consistent),
// then snaps to the real value after mount. Components that need to
// branch layout off a breakpoint should pair this with conditional
// rendering AFTER mount; render the SSR-safe (mobile) variant first
// to avoid layout flash on tablets/desktops.
//
// Tailwind v4 breakpoint constants live next to it for ergonomics.

"use client";

import { useEffect, useState } from "react";

export const BREAKPOINTS = {
  // ≥ 640 → tablet
  sm: "(min-width: 640px)",
  // ≥ 768 → larger tablets / small laptops
  md: "(min-width: 768px)",
  // ≥ 1024 → desktop
  lg: "(min-width: 1024px)",
  xl: "(min-width: 1280px)",
} as const;

export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(false);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const mql = window.matchMedia(query);
    const update = () => setMatches(mql.matches);
    update();
    // Modern API; older Safari needs addListener but Next 16 + React 19
    // already implies a recent enough runtime that we can ignore the
    // legacy fallback.
    mql.addEventListener("change", update);
    return () => mql.removeEventListener("change", update);
  }, [query]);

  return matches;
}
