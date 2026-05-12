// Generic side drawer. Used by the mobile /app shell to slide the
// session list in from the left, and by the marketing TopNav to slide
// the nav menu in on small screens. Hand-rolled (no Radix / Vaul) to
// keep the dependency surface flat and the visual language consistent
// with the rest of the brand (sharp corners, hairline borders, no
// drop shadows beyond a soft scrim glow).
//
// Behaviour:
//   - Mounts a portal-like fixed overlay; closes on scrim click + ESC.
//   - Locks body scroll while open so the scrim doesn't scroll the
//     content underneath.
//   - Renders nothing when `open === false` (so closed drawers cost
//     zero DOM beyond the trigger and the React subtree below).
//   - Slide direction defaults to "left"; pass `side="right"` for
//     right-anchored drawers if needed.
//
// We deliberately do NOT add gesture support here. Swipe-to-open /
// swipe-to-close is doable with pointer events but adds enough code
// (velocity threshold, edge-from detection, animation handoff) that
// it lives in its own follow-up. v1 ships with explicit triggers
// (hamburger, ✕ button, scrim click).

"use client";

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { useScrollLock } from "@/lib/use-scroll-lock";
import { cn } from "@/lib/cn";

type Props = {
  open: boolean;
  onClose: () => void;
  side?: "left" | "right";
  // Width when open. Mobile typically uses ~88vw (320px on a 375
  // viewport leaves ~55px of scrim — enough to feel optional, not
  // accidental). Tablet is fixed-width.
  width?: number | string;
  ariaLabel?: string;
  children: React.ReactNode;
};

export function Drawer({
  open,
  onClose,
  side = "left",
  width = 300,
  ariaLabel,
  children,
}: Props) {
  useScrollLock(open);

  // Portal target. We render through `document.body` so the drawer's
  // `position: fixed` is anchored to the viewport, not to whatever
  // ancestor created a containing block. This matters because any
  // ancestor with `transform`, `filter`, `backdrop-filter`,
  // `perspective`, `will-change`, or `contain` re-bases `fixed`
  // positioning to that ancestor's box. The marketing TopNav has
  // `backdrop-blur` (-> `backdrop-filter: blur(...)`) and the original
  // mount point made the drawer only as tall as the header, hiding
  // every nav link inside an `overflow-y-auto` zero-height container.
  // Portaling to body sidesteps the issue regardless of how chrome
  // around the trigger evolves.
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open || !mounted) return null;

  return createPortal(
    <div className="fixed inset-0 z-50 flex" aria-modal="true" role="dialog" aria-label={ariaLabel}>
      {/*
       * Scrim. Sits behind the drawer; click closes. We use the
       * `overlay` token so the scrim is theme-aware (~black on light,
       * a faint inverted wash on dark — see globals.css).
       */}
      <button
        type="button"
        aria-label="Close menu"
        onClick={onClose}
        className={cn(
          "absolute inset-0 bg-overlay cursor-default",
          // Animate the scrim fade-in. Drawer slide handled below.
          "animate-[abs-fade_180ms_ease-out]",
        )}
      />
      <aside
        className={cn(
          "relative bg-surface border-hair flex flex-col h-full safe-pt safe-pb",
          side === "left"
            ? "border-r mr-auto animate-[abs-slide-in-left_220ms_cubic-bezier(0.2,0.8,0.2,1)]"
            : "border-l ml-auto animate-[abs-slide-in-right_220ms_cubic-bezier(0.2,0.8,0.2,1)]",
        )}
        style={{
          width: typeof width === "number" ? `${width}px` : width,
          maxWidth: "88vw",
          // Soft glow against the scrim. Not a card shadow — just a
          // hint that the drawer floats above the content.
          boxShadow:
            side === "left"
              ? "12px 0 40px rgba(0,0,0,0.35)"
              : "-12px 0 40px rgba(0,0,0,0.35)",
        }}
      >
        {children}
      </aside>
    </div>,
    document.body,
  );
}
