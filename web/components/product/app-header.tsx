// Top bar inside /app. Replaces the marketing TopNav. Carries the
// hamburger trigger (mobile/tablet), the logo, the current reading's
// address/zone, the "verified" timestamp, the theme toggle, and an
// Account button that links back to /billing.
//
// Responsive contract:
//   - base (< 640): minimal — hamburger + logo + zone code + accent
//     dot + theme toggle. Address/verified text move into the address
//     pill below the header (rendered by the page).
//   - sm (≥ 640): adds the full READING string and Account button.
//   - lg (≥ 1024): adds VERIFIED date and theme toggle stays visible.
//
// `onMenuClick` is wired by the parent page; absent on desktop where
// the sidebar is always visible. The hamburger is rendered up to lg
// (mobile = drawer, tablet = collapsible rail trigger).

"use client";

import Link from "next/link";
import { ABSLogo } from "@/components/abs-logo";
import { Btn } from "@/components/btn";
import { Mono } from "@/components/mono";
import { ThemeToggle } from "@/components/theme-toggle";

type Props = {
  reading: { addr: string; zone: string };
  onMenuClick?: () => void;
};

export function AppHeader({ reading, onMenuClick }: Props) {
  return (
    <div className="border-b border-hair bg-surface flex items-center justify-between px-3 sm:px-5 py-2.5 sm:py-3 safe-pt safe-px gap-2">
      <div className="flex items-center gap-2.5 sm:gap-3.5 min-w-0">
        {onMenuClick && (
          <button
            type="button"
            aria-label="Open menu"
            onClick={onMenuClick}
            className="lg:hidden inline-flex flex-col justify-between bg-transparent border-none cursor-pointer p-1 -ml-1 flex-shrink-0"
            style={{ width: 26, height: 20 }}
          >
            <span className="w-full h-[1.5px] bg-text" />
            <span className="w-full h-[1.5px] bg-text" />
            <span className="w-full h-[1.5px] bg-text" />
          </button>
        )}
        <Link href="/" aria-label="ABS home" className="flex-shrink-0">
          <ABSLogo size={20} />
        </Link>
        <span className="hidden sm:inline-block w-px h-4 bg-hair flex-shrink-0" />
        {/* Tablet+ shows the full reading string. Mobile shows just the
         * zone code and accent dot — the full address lives in the
         * AddressPill below the header. */}
        <Mono muted className="hidden sm:inline truncate min-w-0">
          READING · {reading.addr.toUpperCase()} · {reading.zone}
        </Mono>
        <Mono muted className="sm:hidden flex-shrink-0">
          {reading.zone}
        </Mono>
        <span
          className="bg-accent rounded-full flex-shrink-0"
          style={{ width: 6, height: 6 }}
        />
      </div>
      <div className="flex items-center gap-2 sm:gap-2.5 flex-shrink-0">
        <Mono muted className="hidden lg:inline">
          VERIFIED 2026·05·06
        </Mono>
        <ThemeToggle size="sm" />
        <Link href="/billing" className="hidden sm:contents">
          <Btn variant="quiet" size="sm">
            Account
          </Btn>
        </Link>
      </div>
    </div>
  );
}
