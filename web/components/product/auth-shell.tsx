// The authorized shell for signed-in surfaces that aren't the full-bleed
// /app chat (ABS-334). AuthBar is the sticky top bar — logo (→ workspace),
// a hairline, the current section label, and the shared AccountMenu.
// AuthFooter is the slim footer that replaces the heavy marketing Footer.
// AuthShell composes the two around page content.
//
// The section label is keyed off the pathname (AUTH_LABELS); unknown
// routes fall back to "WORKSPACE".

"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ABSLogo } from "@/components/abs-logo";
import { Mono } from "@/components/mono";
import { AccountMenu } from "@/components/product/account-menu";

const AUTH_LABELS: { test: (p: string) => boolean; label: string }[] = [
  { test: (p) => p.startsWith("/cases/new"), label: "ACCOUNT · NEW CASE" },
  { test: (p) => p.startsWith("/app/billing"), label: "ACCOUNT · BILLING" },
  { test: (p) => p === "/app", label: "WORKSPACE · READINGS" },
  { test: (p) => p.startsWith("/coverage"), label: "REFERENCE · COVERAGE" },
  { test: (p) => p.startsWith("/changelog"), label: "REFERENCE · CHANGELOG" },
  { test: (p) => p.startsWith("/support"), label: "REFERENCE · SUPPORT" },
];

function sectionLabel(pathname: string): string {
  return AUTH_LABELS.find((l) => l.test(pathname))?.label ?? "WORKSPACE";
}

export function AuthBar() {
  const pathname = usePathname() || "/";
  return (
    <header
      className="sticky top-0 z-30 bg-surface border-b border-hair backdrop-blur flex items-center justify-between gap-5 safe-pt safe-px"
      style={{ padding: "11px 32px" }}
    >
      <div className="flex items-center gap-[18px] min-w-0">
        {/* Signed in, the logo means "go to your workspace", not marketing home. */}
        <Link href="/app" aria-label="Workspace" className="inline-flex items-center">
          <ABSLogo size={20} />
        </Link>
        <span className="w-px h-4 bg-hair flex-shrink-0" />
        <Mono muted size={10} className="truncate">
          {sectionLabel(pathname)}
        </Mono>
      </div>
      <AccountMenu />
    </header>
  );
}

export function AuthFooter() {
  return (
    <footer
      className="border-t border-hair flex items-center justify-between gap-4 flex-wrap safe-px safe-pb"
      style={{ marginTop: 64, padding: "18px 32px" }}
    >
      <div className="flex items-center gap-[18px]">
        <Mono muted size={9.5}>
          © 2026 ABS · HALIFAX STUDIO
        </Mono>
        <Link href="/support">
          <Mono muted size={9.5} className="hover:text-text transition-colors">
            SUPPORT
          </Mono>
        </Link>
        <Link href="/changelog">
          <Mono muted size={9.5} className="hover:text-text transition-colors">
            CHANGELOG
          </Mono>
        </Link>
      </div>
      <Mono muted size={9.5}>
        NOT LEGAL ADVICE · VERIFY WITH HRM PLANNING
      </Mono>
    </footer>
  );
}

// Convenience wrapper: AuthBar + scrollable content + AuthFooter, for the
// non-app authorized pages (Open a case, Billing).
export function AuthShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-dvh flex flex-col bg-surface text-text">
      <AuthBar />
      <main className="flex-1">{children}</main>
      <AuthFooter />
    </div>
  );
}
