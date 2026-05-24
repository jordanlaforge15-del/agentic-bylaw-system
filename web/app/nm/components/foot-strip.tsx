"use client";

import { fmtClock } from "../lib/format";
import { Dot } from "./dot";

export function FootStrip({
  now,
  stateOk,
}: {
  now: number;
  stateOk: boolean;
}) {
  return (
    <div className="nm-foot">
      <div className="nm-foot__cell" style={{ paddingRight: 18 }}>
        <Dot tone={stateOk ? "ok" : "warn"} />
        <span>STATE.JSON &middot; {stateOk ? "OK" : "STALE"}</span>
      </div>
      <div className="nm-foot__cell">
        <Dot tone="ok" />
        <span>LINEAR &middot; 18ms</span>
      </div>
      <div className="nm-foot__cell">
        <Dot tone="ok" />
        <span>CLAUDE API &middot; 240ms</span>
      </div>
      <div className="nm-foot__cell">
        <Dot tone="warn" />
        <span>DISK &middot; 62%</span>
      </div>
      <div className="nm-foot__spacer" />
      <div className="nm-foot__cell nm-foot__cell--right">
        <span>BUILD &middot; 2026.05.22</span>
      </div>
      <div className="nm-foot__cell nm-foot__cell--right">
        <span className="nm-cursor" suppressHydrationWarning>
          SYNCED {now ? fmtClock(now) : "--:--:--"}
        </span>
      </div>
    </div>
  );
}
