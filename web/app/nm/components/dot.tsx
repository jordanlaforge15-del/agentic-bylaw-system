"use client";

export function Dot({
  tone = "queued",
  pulse,
}: {
  tone?: string;
  pulse?: boolean;
}) {
  return (
    <span
      className={`nm-dot nm-dot--${tone} ${pulse ? "nm-dot--pulse" : ""}`}
    />
  );
}
