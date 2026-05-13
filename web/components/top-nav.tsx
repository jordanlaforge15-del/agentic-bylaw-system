// Sticky top navigation for the marketing shell. The product route at
// /app bypasses this — see app/(product)/app/layout.tsx. The active nav
// link gets a 2px accent bar underneath; non-active links use muted text.
//
// Responsive contract:
//   - base (< 640): logo + theme toggle + hamburger. Tapping the
//     hamburger opens a left drawer with the full nav stack and the
//     "Get an invite" CTA at the bottom.
//   - sm (≥ 640): tablet layout — full nav inline, no kicker yet.
//   - lg (≥ 1024): desktop — adds the kicker beside the logo.

"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { UserButton, useAuth } from "@clerk/nextjs";
import { ABSLogo } from "./abs-logo";
import { Btn } from "./btn";
import { Drawer } from "./drawer";
import { Mono } from "./mono";
import { ThemeToggle } from "./theme-toggle";
import { cn } from "@/lib/cn";

type NavItem = {
  href: string;
  label: string;
};

// Inlined at build time. We use this to decide whether to render
// Clerk's <SignedIn>/<SignedOut> components at all — when Clerk is
// in fallback mode (no real publishable key) those components have
// no provider context and would render their default "signed out"
// branch on every render, which is misleading. The same shape lives
// in components/product/sidebar.tsx — keep them in sync.
const _PK = process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY ?? "";
const CLERK_ENABLED =
  /^pk_(test|live)_/.test(_PK) && _PK.length > 40 && !_PK.includes("replace");

// Always-visible nav items. We hide /sign-in and /signup conditionally
// below depending on auth state — those are auth CTAs, not navigation.
const NAV: NavItem[] = [
  { href: "/", label: "Home" },
  { href: "/pricing", label: "Pricing" },
  { href: "/app", label: "App" },
  { href: "/billing", label: "Billing" },
];

export function TopNav() {
  const pathname = usePathname() || "/";
  const [drawerOpen, setDrawerOpen] = useState(false);
  // @clerk/nextjs v7 unified <SignedIn>/<SignedOut> into <Show>
  // (a server component). In this client TopNav we use the
  // useAuth() hook instead; isLoaded gates against the SSR flash
  // before Clerk hydrates. When Clerk isn't configured, useAuth
  // still returns a no-op shape so the hook call is safe; we just
  // skip the conditional and render the signed-out CTAs.
  const { isLoaded, isSignedIn } = useAuth();
  // SSR + first paint: useAuth hasn't loaded yet (isLoaded=false). We
  // optimistically show signed-out CTAs in that window so the public
  // homepage isn't blank between server render and client hydration.
  // If the user is actually signed in, the swap to signed-in CTAs
  // happens once Clerk's SDK resolves — typically <100ms.
  const showSignedInCtas = CLERK_ENABLED && isLoaded && isSignedIn;
  const showSignedOutCtas = !showSignedInCtas;

  return (
    <header className="sticky top-0 z-30 bg-surface border-b border-hair px-5 sm:px-8 py-3 sm:py-3.5 flex items-center justify-between backdrop-blur safe-pt">
      <div className="flex items-center gap-3 sm:gap-[22px] min-w-0">
        <Link
          href="/"
          aria-label="ABS home"
          className="inline-flex items-center"
        >
          <ABSLogo size={20} />
        </Link>
        <span className="hidden lg:inline-block w-px h-4 bg-hair" />
        <Mono muted size={10.5} className="hidden lg:inline">
          HRM · PRIVATE BETA
        </Mono>
      </div>

      {/* Tablet+ inline nav. Mobile uses the drawer below. */}
      <nav className="hidden sm:flex items-center gap-1">
        {NAV.map((n) => {
          const active = isActive(pathname, n.href);
          return (
            <Link
              key={n.href}
              href={n.href}
              className={cn(
                "relative px-2 lg:px-3 py-2 text-[12.5px] lg:text-[13.5px] tracking-[-0.005em]",
                active
                  ? "font-semibold text-text"
                  : "font-medium text-text-muted hover:text-text",
              )}
            >
              {n.label}
              {active && (
                <span className="absolute left-2 right-2 lg:left-3 lg:right-3 -bottom-[2px] h-[2px] bg-accent" />
              )}
            </Link>
          );
        })}
      </nav>

      <div className="flex items-center gap-2 sm:gap-3">
        <ThemeToggle />
        {/* Signed-in: "Open app →" + UserButton (avatar with menu).
            Signed-out: "Log in" link + "Get an invite" CTA (private
            beta — self-serve signup is intentionally not offered).
            During the brief pre-hydration window neither branch
            renders, which keeps the bar from flashing the wrong
            shape before useAuth() resolves. */}
        {showSignedInCtas && (
          <>
            <Link href="/app" className="hidden sm:contents">
              <Btn variant="primary" size="sm">
                Open app →
              </Btn>
            </Link>
            <div className="hidden sm:inline-flex">
              <UserButton
                appearance={{
                  elements: {
                    avatarBox: "w-8 h-8 rounded-none",
                  },
                }}
              />
            </div>
          </>
        )}
        {showSignedOutCtas && (
          <>
            {/* Sign-in is primary for approved invitees — that's the
                action a returning user takes. "Get an invite" is the
                secondary CTA for new visitors who don't yet have an
                allowlisted email. Order matters: primary on the
                outside (right edge) per typical web nav convention. */}
            <Link href="/signup" className="hidden sm:contents">
              <Btn variant="ghost" size="sm">
                Get an invite
              </Btn>
            </Link>
            {CLERK_ENABLED && (
              <Link href="/sign-in" className="hidden sm:contents">
                <Btn variant="primary" size="sm">
                  Sign in →
                </Btn>
              </Link>
            )}
          </>
        )}
        {/* Mobile-only hamburger. */}
        <button
          type="button"
          aria-label="Open menu"
          aria-expanded={drawerOpen}
          onClick={() => setDrawerOpen(true)}
          className="sm:hidden inline-flex flex-col justify-between bg-transparent border-none cursor-pointer p-1 -mr-1"
          style={{ width: 28, height: 22 }}
        >
          <span className="w-full h-[1.5px] bg-text" />
          <span className="w-full h-[1.5px] bg-text" />
          <span className="w-full h-[1.5px] bg-text" />
        </button>
      </div>

      <Drawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        side="left"
        width={300}
        ariaLabel="Site navigation"
      >
        <div className="px-5 py-4 border-b border-hair flex items-center justify-between">
          <ABSLogo size={20} />
          <button
            type="button"
            aria-label="Close menu"
            onClick={() => setDrawerOpen(false)}
            className="bg-transparent border-none text-text-muted cursor-pointer"
            style={{ fontSize: 18 }}
          >
            ✕
          </button>
        </div>
        <nav className="flex-1 overflow-y-auto flex flex-col">
          {NAV.map((n) => {
            const active = isActive(pathname, n.href);
            return (
              <Link
                key={n.href}
                href={n.href}
                onClick={() => setDrawerOpen(false)}
                className={cn(
                  "px-5 py-3.5 text-[15px] border-b border-hair flex items-center justify-between",
                  active
                    ? "font-semibold text-text bg-surface-alt"
                    : "font-medium text-text",
                )}
                style={{
                  borderLeft: active
                    ? "2px solid var(--accent)"
                    : "2px solid transparent",
                }}
              >
                <span>{n.label}</span>
                {active && (
                  <Mono accent size={9}>
                    HERE
                  </Mono>
                )}
              </Link>
            );
          })}
        </nav>
        <div className="border-t border-hair p-5 flex flex-col gap-3">
          {/* Mobile drawer auth CTAs. Same auth-state branching as
              the desktop bar above, stacked. */}
          {showSignedInCtas && (
            <Link
              href="/app"
              onClick={() => setDrawerOpen(false)}
              className="contents"
            >
              <Btn variant="accent" size="md" className="w-full">
                Open app →
              </Btn>
            </Link>
          )}
          {showSignedOutCtas && (
            <>
              {CLERK_ENABLED && (
                <Link
                  href="/sign-in"
                  onClick={() => setDrawerOpen(false)}
                  className="contents"
                >
                  <Btn variant="accent" size="md" className="w-full">
                    Sign in →
                  </Btn>
                </Link>
              )}
              <Link
                href="/signup"
                onClick={() => setDrawerOpen(false)}
                className="contents"
              >
                <Btn variant="ghost" size="md" className="w-full">
                  Get an invite
                </Btn>
              </Link>
            </>
          )}
        </div>
      </Drawer>
    </header>
  );
}

function isActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(href + "/");
}
