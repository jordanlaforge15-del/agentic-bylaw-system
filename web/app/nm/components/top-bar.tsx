"use client";

import type { RunState } from "../lib/types";
import { fmtElapsed, fmtClock } from "../lib/format";
import { Dot } from "./dot";
import { NightManagerMark } from "./nm-mark";
import { ThemeSwitch } from "./theme-switch";

export function TopBar({
  state,
  now,
  theme,
  onTheme,
}: {
  state: RunState | null;
  now: number;
  theme: string;
  onTheme: (v: string) => void;
}) {
  const startedMs = state ? new Date(state.started_at).getTime() : 0;
  const elapsedMs = state ? now - startedMs : 0;
  const issues = state ? Object.values(state.issues) : [];
  const active = issues.filter(
    (i) => i.status === "in_progress" || i.status === "reviewing",
  ).length;
  const maxAgents = state?.config.max_agents ?? 0;

  return (
    <div className="nm-topbar">
      <div className="nm-topbar__brand">
        <div className="nm-topbar__logo">
          <NightManagerMark size={16} />
        </div>
        <div>
          <div className="nm-topbar__title">NIGHT MANAGER</div>
          <div className="nm-topbar__sub">mission console &middot; v3.2.0</div>
        </div>
      </div>

      <div className="nm-topbar__center">
        {state ? (
          <>
            <div className="nm-topbar__stat">
              <Dot tone="ok" pulse />
              <span>SYSTEM</span>
              <b style={{ color: "var(--ok)" }}>NOMINAL</b>
            </div>
            <div className="nm-topbar__stat">
              <span>RUN</span>
              <b className="nm-prim">{state.run_id}</b>
            </div>
            <div className="nm-topbar__stat">
              <span>STARTED</span>
              <b>{fmtClock(startedMs)}</b>
            </div>
            <div className="nm-topbar__stat">
              <span>ELAPSED</span>
              <span className="nm-display">{fmtElapsed(elapsedMs)}</span>
            </div>
            <div className="nm-topbar__stat">
              <span>AGENTS</span>
              <span className="nm-display" style={{ fontSize: 16 }}>
                {active} / {maxAgents}
              </span>
            </div>
            <div className="nm-topbar__stat nm-right">
              <span>OPERATOR</span>
              <b>@nm-ops</b>
            </div>
          </>
        ) : (
          <div className="nm-topbar__stat">
            <span>NO ACTIVE RUN</span>
          </div>
        )}
      </div>

      <ThemeSwitch theme={theme} onTheme={onTheme} />

      <div className="nm-topbar__right">
        <div className="nm-topbar__clock">
          <span className="nm-topbar__clock-lbl">LOCAL TIME</span>
          <span className="nm-display">{fmtClock(now)}</span>
        </div>
        <div className="nm-topbar__clock">
          <span className="nm-topbar__clock-lbl">UTC</span>
          <span className="nm-display">
            {new Date(now).toISOString().slice(11, 19)}
          </span>
        </div>
      </div>
    </div>
  );
}
