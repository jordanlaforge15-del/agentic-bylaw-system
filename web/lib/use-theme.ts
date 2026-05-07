"use client";

import { useCallback, useEffect, useState } from "react";

export type ThemeMode = "light" | "dark";

const STORAGE_KEY = "abs:theme";

function readInitial(): ThemeMode {
  // Mirror the inline pre-paint script in app/layout.tsx — same key, same
  // default. The DOM has already been stamped with data-mode by the time
  // any client component mounts, so we just read it back rather than
  // re-deriving from localStorage.
  if (typeof document === "undefined") return "light";
  const attr = document.documentElement.getAttribute("data-mode");
  return attr === "dark" ? "dark" : "light";
}

export function useTheme() {
  const [mode, setMode] = useState<ThemeMode>("light");

  useEffect(() => {
    setMode(readInitial());
  }, []);

  const setAndPersist = useCallback((next: ThemeMode) => {
    setMode(next);
    document.documentElement.setAttribute("data-mode", next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Storage may be denied in private browsing; the data-mode attribute
      // is still updated, so the current page-load reflects the choice.
    }
  }, []);

  const toggle = useCallback(() => {
    setAndPersist(mode === "dark" ? "light" : "dark");
  }, [mode, setAndPersist]);

  return { mode, setMode: setAndPersist, toggle };
}
