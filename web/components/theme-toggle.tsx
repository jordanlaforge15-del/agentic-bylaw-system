// Two-cell pill toggle that reads "04 / 03" — design-time labels intended
// to evoke film stock or revision marks rather than sun/moon glyphs. The
// active cell inverts (text fill, surface text); the inactive cell sits
// transparent with muted text.

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
      >
        04
      </span>
      <span
        className={cn(
          "transition-all duration-150",
          pad,
          isDark
            ? "bg-text text-surface"
            : "bg-transparent text-text-muted",
        )}
      >
        03
      </span>
    </button>
  );
}
