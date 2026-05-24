"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { RunState } from "../lib/types";
import { Dot } from "./dot";

const TABS = [
  { num: "01", label: "DASHBOARD", href: "/nm" },
  { num: "02", label: "ISSUE DETAIL", href: "/nm/issues" },
  { num: "03", label: "LAUNCH", href: "/nm/launch" },
  { num: "04", label: "REPORTS", href: "/nm/reports" },
] as const;

export function NavBar({ state }: { state: RunState | null }) {
  const pathname = usePathname();
  const issues = state ? Object.values(state.issues) : [];
  const merged = issues.filter((i) => i.status === "merged").length;
  const total = issues.length;
  const failed = issues.filter((i) => i.status === "failed").length;
  const active = issues.filter(
    (i) => i.status === "in_progress" || i.status === "reviewing",
  ).length;

  function isActive(href: string) {
    if (href === "/nm") return pathname === "/nm";
    return pathname.startsWith(href);
  }

  return (
    <div className="nm-nav">
      {TABS.map((tab) => (
        <Link
          key={tab.href}
          href={tab.href}
          className={`nm-nav__item ${isActive(tab.href) ? "nm-nav__item--active" : ""}`}
        >
          <span className="nm-nav__num">{tab.num}</span> {tab.label}
        </Link>
      ))}
      <div className="nm-nav__spacer" />
      <div className="nm-nav__right">
        {state && (
          <>
            <div className="nm-nav__kv">
              <Dot tone="info" pulse /> <span>{active} agents live</span>
            </div>
            <div className="nm-nav__kv">
              <Dot tone="ok" />{" "}
              <span>
                {merged}/{total} merged
              </span>
            </div>
            {failed > 0 && (
              <div className="nm-nav__kv">
                <Dot tone="err" /> <span>{failed} failed</span>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
