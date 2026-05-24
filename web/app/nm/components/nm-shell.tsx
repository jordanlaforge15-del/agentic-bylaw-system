"use client";

import type { ReactNode } from "react";
import { SWRConfig } from "swr";
import { useTick, useNmState, useNmTheme } from "../lib/hooks";
import { TopBar } from "./top-bar";
import { NavBar } from "./nav-bar";
import { FootStrip } from "./foot-strip";

export function NmShell({ children }: { children: ReactNode }) {
  const { theme, setTheme } = useNmTheme();
  const now = useTick(1000);
  const { state, error } = useNmState();

  return (
    <SWRConfig value={{}}>
      <div id="nm-root" data-nm-theme={theme} className="nm-app">
        <TopBar state={state} now={now} theme={theme} onTheme={setTheme} />
        <NavBar state={state} />
        <div className="nm-console">{children}</div>
        <FootStrip now={now} stateOk={!error && state !== null} />
      </div>
    </SWRConfig>
  );
}
