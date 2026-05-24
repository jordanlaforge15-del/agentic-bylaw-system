"use client";

export function NightManagerMark({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none">
      <circle cx="8" cy="8" r="6.5" stroke="currentColor" strokeWidth="1" />
      <path
        d="M8 1.5 V 4 M8 12 V 14.5 M1.5 8 H 4 M12 8 H 14.5"
        stroke="currentColor"
        strokeWidth="1"
      />
      <circle cx="8" cy="8" r="2" fill="currentColor" />
    </svg>
  );
}
