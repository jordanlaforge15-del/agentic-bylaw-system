// Mobile-only "address pill" — appears between the app header and the
// chat thread. Two jobs:
//   1. Always-visible context: tells the user which parcel they're
//      reading even when the sidebar drawer is closed and the parcel
//      sheet is dismissed.
//   2. Trigger: tapping opens the parcel bottom sheet.
//
// Hidden at `lg` (≥ 1024) — the desktop layout has a persistent right
// pane so this affordance isn't needed.

"use client";

import { Mono } from "@/components/mono";
import type { ParcelContext } from "@/lib/parcel";

type Props = {
  parcel: ParcelContext | null;
  onClick: () => void;
};

export function AddressPill({ parcel, onClick }: Props) {
  // No parcel = no pill. Mobile users see the chat thread immediately
  // when starting fresh; the pill appears the moment a parcel is
  // resolved.
  if (!parcel) return null;

  return (
    <button
      type="button"
      onClick={onClick}
      aria-label="Open parcel details"
      className="lg:hidden w-full border-b border-hair bg-surface-alt flex justify-between items-center px-4 py-2.5 text-left cursor-pointer"
    >
      <div className="flex flex-col gap-0.5 min-w-0">
        <span className="text-[13.5px] font-semibold truncate">
          {parcel.address.civic_number} {parcel.address.street}
        </span>
        <Mono muted size={9}>
          {parcel.zone?.code ?? "—"} · TAP FOR PARCEL
        </Mono>
      </div>
      <span
        aria-hidden
        className="text-text-muted text-[14px] ml-3 flex-shrink-0"
      >
        ▴
      </span>
    </button>
  );
}
