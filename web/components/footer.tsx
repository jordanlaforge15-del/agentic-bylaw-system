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
    <footer className="border-t border-hair px-8 pt-9 pb-7 flex flex-col gap-[22px] mt-20">
      <div className="grid gap-7" style={{ gridTemplateColumns: "1.4fr 1fr 1fr 1fr" }}>
        <div className="flex flex-col gap-2.5">
          <ABSLogo size={28} />
          <p
            className="text-[13px] leading-[1.5] text-text-muted m-0"
            style={{ maxWidth: 280 }}
          >
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
      <div className="flex justify-between items-center pt-[18px] border-t border-hair">
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
