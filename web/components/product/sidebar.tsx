// Left pane of /app. Lists recent readings; active item gets a 2px accent
// left border + alt-surface background. Search input filters by addr+title.
// Footer row: 28×28 accent-square avatar + workspace + plan line.

"use client";

import { useMemo, useState } from "react";
import { Btn } from "@/components/btn";
import { Mono } from "@/components/mono";
import { SAMPLE_THREADS, type Thread } from "@/lib/mock";
import { cn } from "@/lib/cn";

type Props = {
  onNew: () => void;
};

export function Sidebar({ onNew }: Props) {
  const [q, setQ] = useState("");
  const filtered = useMemo<Thread[]>(
    () =>
      SAMPLE_THREADS.filter(
        (x) => !q || (x.addr + x.title).toLowerCase().includes(q.toLowerCase()),
      ),
    [q],
  );

  return (
    <aside
      className="border-r border-hair bg-surface flex flex-col min-h-0"
      style={{ width: 280 }}
    >
      <div className="border-b border-hair p-4 flex flex-col gap-3">
        <Btn variant="primary" size="sm" onClick={onNew} className="w-full">
          + New reading
        </Btn>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search readings…"
          className="bg-surface-alt text-text border border-hair font-sans outline-none px-2.5 py-2"
          style={{ fontSize: 12.5 }}
        />
      </div>
      <div className="px-4 pt-3 pb-1.5">
        <Mono muted size={9.5}>
          RECENT · {filtered.length}
        </Mono>
      </div>
      <div className="flex-1 overflow-y-auto px-2 pb-3 flex flex-col gap-0.5">
        {filtered.map((th) => (
          <button
            key={th.id}
            type="button"
            className={cn(
              "text-left flex flex-col gap-1 cursor-pointer text-text font-sans",
              "px-3 py-2.5 pl-3",
              th.active ? "bg-surface-alt" : "bg-transparent",
            )}
            style={{
              borderLeft: th.active
                ? "2px solid var(--accent)"
                : "2px solid transparent",
            }}
          >
            <div className="flex justify-between items-baseline gap-2">
              <span
                className="text-[12.5px] font-semibold tracking-[-0.005em]"
              >
                {th.addr}
              </span>
              {th.unread && (
                <span
                  className="bg-accent rounded-full"
                  style={{ width: 6, height: 6 }}
                />
              )}
            </div>
            <span className="text-[11.5px] text-text-muted leading-[1.35]">
              {th.title}
            </span>
            <div className="flex justify-between items-baseline mt-0.5">
              <Mono muted size={9}>
                {th.zone}
              </Mono>
              <Mono muted size={9}>
                {th.updated.toUpperCase()}
              </Mono>
            </div>
          </button>
        ))}
      </div>
      <div className="border-t border-hair px-4 py-3 flex items-center gap-2.5">
        <div
          className="bg-text text-surface flex items-center justify-center font-mono font-semibold"
          style={{ width: 28, height: 28, fontSize: 11 }}
        >
          HS
        </div>
        <div className="flex-1 min-w-0">
          <div className="text-[12.5px] font-semibold">Halifax Studio</div>
          <div className="text-[10.5px] text-text-muted">
            Practice · 4 seats
          </div>
        </div>
        <button
          type="button"
          className="bg-transparent text-text-muted cursor-pointer font-mono"
          style={{ fontSize: 11 }}
          aria-label="Settings"
        >
          ⚙
        </button>
      </div>
    </aside>
  );
}
