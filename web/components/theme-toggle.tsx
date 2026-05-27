// Two-cell pill toggle for light/dark mode. The active cell inverts (text
// fill, surface text); the inactive cell sits transparent with muted text.

"use client";

import { cn } from "@/lib/cn";
import { useTheme } from "@/lib/use-theme";

type Props = {
  size?: "sm" | "md";
};

export function ThemeToggle({ size = "md" }: Props) {
  const { mode, toggle } = useTheme();
  const isDark = mode === "dark";
  const pad = size === "sm" ? "px-2 py-[3px]" : "px-2.5 py-[5px]";
  const fontSize = size === "sm" ? 9.5 : 10;
  return (
    <button
      type="button"
      onClick={toggle}
      title="Toggle light / dark"
      aria-label={isDark ? "Switch to light mode" : "Switch to dark mode"}
      className="inline-flex items-center bg-surface-alt border border-hair p-[2px] cursor-pointer font-mono uppercase"
      style={{ fontSize, letterSpacing: "0.12em" }}
    >
      <span
        className={cn(
          "transition-all duration-150",
          pad,
          isDark
            ? "bg-transparent text-text-muted"
            : "bg-text text-surface",
        )}
        aria-hidden={isDark}
      >
        Light
      </span>
      <span
        className={cn(
          "transition-all duration-150",
          pad,
          isDark
            ? "bg-text text-surface"
            : "bg-transparent text-text-muted",
        )}
        aria-hidden={!isDark}
      >
        Dark
      </span>
    </button>
  );
}
