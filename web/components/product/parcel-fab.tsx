// Tablet-only floating "Parcel" button — bottom-right of the chat pane.
// Toggles the parcel pane open as a side overlay (slides in from the
// right, anchored to the chat area).
//
// Hidden at base (mobile uses the AddressPill bottom-sheet instead) and
// at lg (desktop has a persistent right pane).
//
// Disabled when no parcel context is available — same UX as the
// desktop right-pane "Export PDF" button being disabled before a
// parcel is resolved. Empty state still opens though, so users can see
// the "no parcel yet" message in the overlay.

"use client";

type Props = {
  onClick: () => void;
  active: boolean;
};

export function ParcelFab({ onClick, active }: Props) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={active ? "Close parcel" : "Open parcel"}
      aria-pressed={active}
      className="
        hidden sm:inline-flex lg:hidden
        absolute bottom-24 sm:bottom-28 right-4 sm:right-5 z-20
        items-center gap-2
        bg-text text-surface
        border-[1.5px] border-text
        px-3.5 py-2.5
        font-sans font-semibold text-[12px]
        cursor-pointer
        transition-transform duration-100
        active:translate-y-[1px]
      "
      style={{ letterSpacing: "-0.005em" }}
    >
      <svg
        width="14"
        height="14"
        viewBox="0 0 14 14"
        fill="none"
        aria-hidden
      >
        <rect
          x="1"
          y="1"
          width="12"
          height="9"
          stroke="currentColor"
          strokeWidth="1.2"
        />
        <rect
          x="3.5"
          y="3.5"
          width="7"
          height="4"
          stroke="currentColor"
          strokeWidth="0.8"
        />
      </svg>
      <span>{active ? "Close" : "Parcel"}</span>
    </button>
  );
}
