// The workspace menu — the single, consistent authorized navigation
// control (ABS-334). One dropdown, same contents on every signed-in
// surface: compact in the dense /app header, full inside the AuthBar on
// the other authorized pages. Replaces the per-page top-right buttons
// (the /app "Billing" button + ad-hoc theme toggle) with one repeatable
// control; the theme toggle now lives inside this menu.
//
// Identity mirrors the sidebar's Clerk/placeholder split: when Clerk is
// configured we show the signed-in user's email; otherwise the static
// "Halifax Studio" practice card (dev / fallback).

"use client";

import { useEffect, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useClerk, useUser } from "@clerk/nextjs";
import { Mono } from "@/components/mono";
import { ThemeToggle } from "@/components/theme-toggle";
import { cn } from "@/lib/cn";

// Same guard the marketing TopNav + sidebar use: only touch Clerk's
// hooks for real identity when a real publishable key is inlined. In
// fallback mode useUser() never resolves to a signed-in user, so we'd
// otherwise show an empty identity.
const _PK = process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY ?? "";
const CLERK_ENABLED =
  /^pk_(test|live)_/.test(_PK) && _PK.length > 40 && !_PK.includes("replace");

type NavItem = { href: string; label: string; hint: string };

// The authorized destinations, grouped exactly as the handoff specifies.
// hrefs are the real app routes (the prototype's hash ids map onto these).
const NAV: { section: string; items: NavItem[] }[] = [
  {
    section: "WORKSPACE",
    items: [
      { href: "/cases/new", label: "Open a case", hint: "Start a new reading" },
      { href: "/app", label: "Readings", hint: "Chat + parcel readings" },
      { href: "/app/billing", label: "Billing", hint: "Plan, seats & invoices" },
    ],
  },
  {
    section: "REFERENCE",
    items: [
      { href: "/coverage", label: "Coverage", hint: "Indexed jurisdictions" },
      { href: "/changelog", label: "Changelog", hint: "Release register" },
      { href: "/support", label: "Support", hint: "Help & contact" },
    ],
  },
];

// Readings (/app) must not stay highlighted on its /app/billing child —
// match it exactly. Everything else matches the route or its subtree.
function isActive(pathname: string, href: string): boolean {
  if (href === "/app") return pathname === "/app";
  return pathname === href || pathname.startsWith(href + "/");
}

type Props = {
  compact?: boolean;
  showNav?: boolean;
};

export function AccountMenu({ compact = false, showNav = true }: Props) {
  const pathname = usePathname() || "/";
  const router = useRouter();
  const { user } = useUser();
  const { signOut } = useClerk();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // Bind dismissal listeners only while open (outside mousedown + Escape).
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const identity =
    CLERK_ENABLED && user
      ? {
          name:
            user.fullName || user.username || "Workspace",
          email: user.primaryEmailAddress?.emailAddress ?? "",
          initials: initialsFrom(
            user.fullName || user.username || user.primaryEmailAddress?.emailAddress || "HS",
          ),
        }
      : {
          name: "Halifax Studio",
          email: "billing@halifaxstudio.co",
          initials: "HS",
        };

  function nav(href: string) {
    setOpen(false);
    router.push(href);
  }

  function logout() {
    setOpen(false);
    if (CLERK_ENABLED) {
      void signOut(() => router.push("/"));
    } else {
      router.push("/");
    }
  }

  return (
    <div ref={ref} className="relative">
      {/* Trigger chip */}
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Workspace menu"
        onClick={() => setOpen((o) => !o)}
        className={cn(
          "inline-flex items-center gap-[11px] bg-surface cursor-pointer font-sans border transition-colors",
          open ? "border-text" : "border-hair",
          compact ? "py-1 pr-2 pl-1" : "py-1 pr-3 pl-1",
        )}
      >
        <Avatar size={compact ? 24 : 26} initials={identity.initials} />
        {!compact && (
          <span className="flex flex-col items-start leading-[1.15]">
            <span className="text-[12.5px] font-semibold text-text tracking-[-0.01em] max-w-[140px] truncate">
              {identity.name}
            </span>
            <Mono muted size={9} className="!tracking-[0.08em]">
              PRACTICE · 4 SEATS
            </Mono>
          </span>
        )}
        <span
          className="text-text-muted text-[10px] transition-transform duration-150"
          style={{
            transform: open ? "rotate(180deg)" : "none",
            marginLeft: compact ? 0 : 2,
          }}
        >
          ▾
        </span>
      </button>

      {/* Panel */}
      {open && (
        <div
          role="menu"
          className="absolute right-0 z-[60] w-[278px] bg-surface border-[1.5px] border-text flex flex-col"
          style={{ top: "calc(100% + 8px)" }}
        >
          {/* Identity header */}
          <div className="flex items-center gap-[11px] px-[14px] py-[14px] border-b border-hair">
            <Avatar size={34} initials={identity.initials} />
            <div className="flex-1 min-w-0">
              <div className="text-[14px] font-semibold tracking-[-0.01em] truncate">
                {identity.name}
              </div>
              {identity.email && (
                <div className="text-[11.5px] text-text-muted truncate">
                  {identity.email}
                </div>
              )}
            </div>
            <span
              className="bg-accent text-on-accent font-mono"
              style={{ padding: "3px 7px", fontSize: 8.5, letterSpacing: "0.12em" }}
            >
              PRACTICE
            </span>
          </div>

          {showNav &&
            NAV.map(({ section, items }) => (
              <div key={section} className="pt-[10px] pb-[8px] border-b border-hair">
                <Mono muted size={9} className="block px-[14px] pb-[6px]">
                  {section}
                </Mono>
                {items.map((item) => (
                  <MenuRow
                    key={item.href}
                    item={item}
                    active={isActive(pathname, item.href)}
                    onClick={() => nav(item.href)}
                  />
                ))}
              </div>
            ))}

          {/* Appearance */}
          <div className="flex items-center justify-between px-[14px] py-[11px] border-b border-hair">
            <Mono muted size={9}>
              APPEARANCE
            </Mono>
            <ThemeToggle size="sm" />
          </div>

          {/* Log out */}
          <button
            type="button"
            role="menuitem"
            onClick={logout}
            className="flex items-center justify-between px-[14px] py-[12px] bg-transparent border-none cursor-pointer font-sans text-[13px] font-medium text-text text-left hover:bg-surface-alt transition-colors"
          >
            <span>Log out</span>
            <span className="text-text-muted">→</span>
          </button>
        </div>
      )}
    </div>
  );
}

function MenuRow({
  item,
  active,
  onClick,
}: {
  item: NavItem;
  active: boolean;
  onClick: () => void;
}) {
  const [hover, setHover] = useState(false);
  return (
    <button
      type="button"
      role="menuitem"
      aria-current={active ? "page" : undefined}
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      className={cn(
        "grid items-center gap-[10px] w-full text-left cursor-pointer font-sans border-none transition-colors",
        active || hover ? "bg-surface-alt" : "bg-transparent",
      )}
      style={{
        gridTemplateColumns: "1fr auto",
        padding: "9px 14px 9px 13px",
        borderLeft: `2px solid ${active ? "var(--accent)" : "transparent"}`,
      }}
    >
      <span className="flex flex-col gap-[2px]">
        <span
          className={cn(
            "text-[13.5px] tracking-[-0.01em] text-text",
            active ? "font-semibold" : "font-medium",
          )}
        >
          {item.label}
        </span>
        <span className="text-[11px] text-text-muted tracking-[-0.005em]">
          {item.hint}
        </span>
      </span>
      {active ? (
        <Mono accent size={9}>
          OPEN
        </Mono>
      ) : (
        <span
          className="text-text-muted transition-opacity duration-100"
          style={{ opacity: hover ? 1 : 0 }}
        >
          →
        </span>
      )}
    </button>
  );
}

// Square avatar (no border-radius is part of the brand). Inverts: text
// fill on surface ink.
function Avatar({ size, initials }: { size: number; initials: string }) {
  return (
    <div
      aria-hidden
      className="bg-text text-surface flex items-center justify-center font-mono font-semibold flex-shrink-0"
      style={{ width: size, height: size, fontSize: size * 0.4, letterSpacing: "0.02em" }}
    >
      {initials}
    </div>
  );
}

function initialsFrom(source: string): string {
  const parts = source.trim().split(/\s+/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  const s = parts[0] ?? "";
  return (s.slice(0, 2) || "HS").toUpperCase();
}
