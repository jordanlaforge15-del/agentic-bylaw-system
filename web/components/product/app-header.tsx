// Top bar inside /app. Replaces the marketing TopNav. Carries the logo,
// the current reading's address/zone, the "verified" timestamp, the
// theme toggle, and an Account button that links back to /billing.

"use client";

import Link from "next/link";
import { ABSLogo } from "@/components/abs-logo";
import { Btn } from "@/components/btn";
import { Mono } from "@/components/mono";
import { ThemeToggle } from "@/components/theme-toggle";

type Props = {
  reading: { addr: string; zone: string };
};

export function AppHeader({ reading }: Props) {
  return (
    <div className="border-b border-hair bg-surface flex items-center justify-between px-5 py-3">
      <div className="flex items-center gap-3.5">
        <Link href="/" aria-label="ABS home">
          <ABSLogo size={20} />
        </Link>
        <span className="w-px h-4 bg-hair" />
        <Mono muted>
          READING · {reading.addr.toUpperCase()} · {reading.zone}
        </Mono>
        <span
          className="bg-accent rounded-full"
          style={{ width: 6, height: 6 }}
        />
      </div>
      <div className="flex items-center gap-2.5">
        <Mono muted>VERIFIED 2026·05·06</Mono>
        <ThemeToggle size="sm" />
        <Link href="/billing">
          <Btn variant="quiet" size="sm">
            Account
          </Btn>
        </Link>
      </div>
    </div>
  );
}
