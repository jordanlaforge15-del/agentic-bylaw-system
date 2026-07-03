// Top bar inside /app. Replaces the marketing TopNav. Carries the
// hamburger trigger (mobile/tablet), the logo, the current reading's
// address/zone, the "verified" timestamp, and the shared workspace menu.
//
// Responsive contract:
//   - base (< 640): minimal — hamburger + logo + zone code + accent
//     dot + workspace menu (compact). Address/verified text move into
//     the address pill below the header (rendered by the page).
//   - sm (≥ 640): adds the full READING string.
//   - lg (≥ 1024): adds the VERIFIED date.
//
// `onMenuClick` is wired by the parent page; absent on desktop where
// the sidebar is always visible. The hamburger is rendered up to lg
// (mobile = drawer, tablet = collapsible rail trigger).
//
// ABS-334: the right cluster carries <AccountMenu compact/> — the single
// authorized nav control — in place of the old ad-hoc theme toggle +
// "Billing" button. The theme toggle now lives inside that menu, so
// appearance control is consistent across every authorized surface. The
// logo routes to /app (the workspace), not the marketing home.

"use client";

import Link from "next/link";
import { ABSLogo } from "@/components/abs-logo";
import { Mono } from "@/components/mono";
import { AccountMenu } from "@/components/product/account-menu";

type Props = {
  reading: { addr: string; zone: string };
  onMenuClick?: () => void;
  // ABS-344: the workspace-state label that prefixes the reading string —
  // "REPORT" / "CONVERSATION" / "GENERATING" so the header tracks which
  // face of the unified case workspace the center pane is showing. Defaults
  // to "CONVERSATION" (a plain chat case with no report).
  label?: string;
};

export function AppHeader({ reading, onMenuClick, label = "CONVERSATION" }: Props) {
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
        <Link href="/app" aria-label="Workspace" className="flex-shrink-0">
          <ABSLogo size={20} />
        </Link>
        <span className="hidden sm:inline-block w-px h-4 bg-hair flex-shrink-0" />
        {/* Tablet+ shows the full reading string. Mobile shows just the
         * zone code and accent dot — the full address lives in the
         * AddressPill below the header. */}
        <Mono
          muted
          data-testid="workspace-label"
          className="hidden sm:inline truncate min-w-0"
        >
          {label} · {reading.addr.toUpperCase()} · {reading.zone}
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
        <AccountMenu compact />
      </div>
    </div>
  );
}
