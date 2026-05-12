// Bottom sheet — anchored to the bottom edge, fills up to a maxHeight
// of the viewport. Used by the mobile /app shell to surface the parcel
// pane without unmounting the chat thread underneath.
//
// Per design contract: 16px top corner radius (the only place radius
// is used in the brand — this is a platform affordance, not a brand
// mark), drag-handle indicator, scrim above the underlying content,
// closes on scrim tap or ESC. Drag-to-dismiss is a follow-up — v1
// uses the explicit ✕ control inside the sheet header.
//
// `maxHeightPct` defaults to 80 — the spec calls for the sheet to
// preserve a sliver of the underlying chat at the top so users
// remember they can dismiss it back to the conversation.

"use client";

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { useScrollLock } from "@/lib/use-scroll-lock";
import { cn } from "@/lib/cn";

type Props = {
  open: boolean;
  onClose: () => void;
  maxHeightPct?: number;
  ariaLabel?: string;
  children: React.ReactNode;
};

export function Sheet({
  open,
  onClose,
  maxHeightPct = 80,
  ariaLabel,
  children,
}: Props) {
  useScrollLock(open);

  // Same portal rationale as Drawer (see drawer.tsx). An ancestor with
  // `backdrop-filter` / `transform` / `filter` / `perspective` /
  // `will-change` / `contain` would re-base our `position: fixed` to
  // its own box, breaking full-viewport coverage. Render through
  // `document.body` to make the sheet immune to the ancestor chain.
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
    <div
      className="fixed inset-0 z-50 flex flex-col justify-end"
      aria-modal="true"
      role="dialog"
      aria-label={ariaLabel}
    >
      <button
        type="button"
        aria-label="Close"
        onClick={onClose}
        className={cn(
          "absolute inset-0 bg-overlay cursor-default",
          "animate-[abs-fade_180ms_ease-out]",
        )}
      />
      <aside
        className={cn(
          "relative bg-surface-alt border-t border-hair safe-pb flex flex-col",
          "animate-[abs-slide-up_240ms_cubic-bezier(0.2,0.8,0.2,1)]",
        )}
        style={{
          // dvh tracks the dynamic viewport (URL bar collapse-aware on
          // iOS) — without it, opening the sheet near the URL bar can
          // overshoot the visible area.
          maxHeight: `${maxHeightPct}dvh`,
          // Brand normally has zero radius. Bottom sheets get a 16px
          // top radius because every iOS / Android sheet does — it's
          // the affordance that says "this drags up from the bottom".
          borderTopLeftRadius: 16,
          borderTopRightRadius: 16,
          boxShadow: "0 -16px 40px rgba(0,0,0,0.35)",
        }}
      >
        {/* Drag handle. Static for v1 — no gesture wiring yet — but
         * the visual is the affordance the spec asks for. */}
        <div className="pt-2 pb-1 flex justify-center flex-shrink-0">
          <div
            className="bg-hair"
            style={{ width: 36, height: 4, borderRadius: 2 }}
            aria-hidden
          />
        </div>
        {children}
      </aside>
    </div>,
    document.body,
  );
}
