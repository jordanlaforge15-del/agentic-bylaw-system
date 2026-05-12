// Marketing footer. Brand block + three link columns + a bottom legal
// row.
//
// Responsive contract:
//   - base (< 640): brand block on top, link columns stack vertically
//     beneath it. Bottom legal row stacks the two captions.
//   - sm (≥ 640): brand block + columns flow into a 2-col grid (brand
//     spans both, columns wrap into a 3-col sub-grid).
//   - lg (≥ 1024): the original four-column [1.4fr 1fr 1fr 1fr] grid.

import Link from "next/link";
import { ABSLogo } from "./abs-logo";
import { Mono } from "./mono";

type Column = {
  h: string;
  items: Array<{ label: string; href: string }>;
};

const COLUMNS: Column[] = [
  {
    h: "Product",
    items: [
      { label: "Home", href: "/" },
      { label: "Pricing", href: "/pricing" },
      { label: "Changelog", href: "/changelog" },
      { label: "Coverage", href: "/coverage" },
    ],
  },
  {
    h: "Account",
    items: [
      { label: "Log in", href: "/sign-in" },
      { label: "Get an invite", href: "/signup" },
      { label: "Billing", href: "/billing" },
      { label: "Support", href: "/support" },
    ],
  },
  {
    h: "Company",
    items: [
      { label: "About", href: "/about" },
      { label: "Privacy", href: "/privacy" },
      { label: "Terms", href: "/terms" },
      { label: "hello@abs.app", href: "mailto:hello@abs.app" },
    ],
  },
];

export function Footer() {
  return (
    <footer className="border-t border-hair px-5 sm:px-8 pt-8 sm:pt-9 pb-6 sm:pb-7 flex flex-col gap-5 sm:gap-[22px] mt-12 sm:mt-16 lg:mt-20 safe-pb">
      <div className="grid gap-6 sm:gap-7 grid-cols-1 sm:grid-cols-3 lg:[grid-template-columns:1.4fr_1fr_1fr_1fr]">
        <div className="flex flex-col gap-2.5 sm:col-span-3 lg:col-span-1">
          <ABSLogo size={28} />
          <p className="text-[13px] leading-[1.5] text-text-muted m-0 max-w-[280px]">
            An expert planner integrated into your workflow. Built in Halifax.
          </p>
        </div>
        {COLUMNS.map((c) => (
          <div key={c.h} className="flex flex-col gap-2">
            <Mono muted>{c.h}</Mono>
            {c.items.map((i) => (
              <Link
                key={i.label}
                href={i.href}
                className="text-[13px] text-text"
              >
                {i.label}
              </Link>
            ))}
          </div>
        ))}
      </div>
      <div className="flex flex-col sm:flex-row sm:justify-between sm:items-center gap-2 pt-4 sm:pt-[18px] border-t border-hair">
        <Mono muted size={9.5}>
          © 2026 ABS · HALIFAX REGIONAL MUNICIPALITY
        </Mono>
        <Mono muted size={9.5}>
          NOT LEGAL ADVICE · VERIFY WITH HRM PLANNING
        </Mono>
      </div>
    </footer>
  );
}
