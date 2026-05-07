// Sticky top navigation for the marketing shell. The product route at
// /app bypasses this — see app/(product)/app/layout.tsx. The active nav
// link gets a 2px accent bar underneath; non-active links use muted text.

"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ABSLogo } from "./abs-logo";
import { Btn } from "./btn";
import { Mono } from "./mono";
import { ThemeToggle } from "./theme-toggle";
import { cn } from "@/lib/cn";

type NavItem = {
  href: string;
  label: string;
};

const NAV: NavItem[] = [
  { href: "/", label: "Home" },
  { href: "/pricing", label: "Pricing" },
  { href: "/app", label: "App" },
  { href: "/login", label: "Log in" },
  { href: "/signup", label: "Get an invite" },
  { href: "/billing", label: "Billing" },
];

export function TopNav() {
  const pathname = usePathname() || "/";
  return (
    <header className="sticky top-0 z-30 bg-surface border-b border-hair px-8 py-3.5 flex items-center justify-between backdrop-blur">
      <div className="flex items-center gap-[22px]">
        <Link
          href="/"
          aria-label="ABS home"
          className="inline-flex items-center"
        >
          <ABSLogo size={22} />
        </Link>
        <span className="w-px h-4 bg-hair" />
        <Mono muted size={10.5}>
          HRM · PRIVATE BETA
        </Mono>
      </div>

      <nav className="flex items-center gap-1">
        {NAV.map((n) => {
          const active = isActive(pathname, n.href);
          return (
            <Link
              key={n.href}
              href={n.href}
              className={cn(
                "relative px-3 py-2 text-[13.5px] tracking-[-0.005em]",
                active
                  ? "font-semibold text-text"
                  : "font-medium text-text-muted hover:text-text",
              )}
            >
              {n.label}
              {active && (
                <span className="absolute left-3 right-3 -bottom-[2px] h-[2px] bg-accent" />
              )}
            </Link>
          );
        })}
      </nav>

      <div className="flex items-center gap-3">
        <ThemeToggle />
        <Link href="/signup">
          <Btn variant="primary" size="sm">
            Get an invite →
          </Btn>
        </Link>
      </div>
    </header>
  );
}

function isActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(href + "/");
}
