// Mono caption — JetBrains Mono uppercase with wide tracking. Used
// everywhere for kickers, labels, metadata. Three colour modes: default
// (text), muted (text-muted), and accent (accent-ink).

import { cn } from "@/lib/cn";

type Props = {
  children: React.ReactNode;
  muted?: boolean;
  accent?: boolean;
  size?: number;
  className?: string;
  style?: React.CSSProperties;
};

export function Mono({
  children,
  muted,
  accent,
  size = 10,
  className,
  style,
}: Props) {
  return (
    <span
      className={cn(
        "font-mono uppercase",
        accent ? "text-accent-ink" : muted ? "text-text-muted" : "text-text",
        className,
      )}
      style={{ fontSize: size, letterSpacing: "0.14em", ...style }}
    >
      {children}
    </span>
  );
}
