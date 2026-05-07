// Button atom. Four variants (primary | accent | ghost | quiet) × three
// sizes. Sharp corners, 1.5px borders, brief press transform on mousedown.

"use client";

import { cn } from "@/lib/cn";

type Variant = "primary" | "accent" | "ghost" | "quiet";
type Size = "sm" | "md" | "lg";

type Props = Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, "size"> & {
  variant?: Variant;
  size?: Size;
};

const SIZES: Record<Size, string> = {
  sm: "px-3 py-2 text-[12.5px]",
  md: "px-[18px] py-[11px] text-[13.5px]",
  lg: "px-[22px] py-[14px] text-[14.5px]",
};

const VARIANTS: Record<Variant, string> = {
  // Inverts on the surface for marketing CTAs.
  primary: "bg-text text-surface border-text",
  // Brand-accent fills — used for the focal CTA on each screen.
  accent: "bg-accent text-on-accent border-accent",
  // Outline only; reads on either theme.
  ghost: "bg-transparent text-text border-text",
  // Minimal — for in-app utility buttons (e.g. account).
  quiet: "bg-transparent text-text-muted border-hair",
};

export function Btn({
  variant = "primary",
  size = "md",
  className,
  children,
  onMouseDown,
  onMouseUp,
  onMouseLeave,
  ...rest
}: Props) {
  return (
    <button
      {...rest}
      className={cn(
        "inline-flex items-center justify-center cursor-pointer font-sans font-semibold",
        "border-[1.5px] transition-[transform,opacity] duration-100",
        "tracking-[-0.01em]",
        SIZES[size],
        VARIANTS[variant],
        className,
      )}
      onMouseDown={(e) => {
        e.currentTarget.style.transform = "translateY(1px)";
        onMouseDown?.(e);
      }}
      onMouseUp={(e) => {
        e.currentTarget.style.transform = "";
        onMouseUp?.(e);
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.transform = "";
        onMouseLeave?.(e);
      }}
    >
      {children}
    </button>
  );
}
