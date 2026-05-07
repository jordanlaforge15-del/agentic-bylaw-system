// Two-column auth shell shared by /login and /signup. Left pane carries
// the kicker / title / sub / form; right pane (`side`) is per-screen
// context — last-session preview on login, who-uses-ABS personas on
// signup. Below lg the right pane stacks under the left.

import { Mono } from "./mono";

type Props = {
  kicker: string;
  title: React.ReactNode;
  sub?: string;
  side: React.ReactNode;
  children: React.ReactNode;
};

export function AuthLayout({ kicker, title, sub, side, children }: Props) {
  return (
    <div
      className="grid grid-cols-1 lg:grid-cols-2"
      style={{ minHeight: "calc(100vh - 220px)" }}
    >
      <div className="px-12 py-16 flex flex-col justify-center w-full mx-auto">
        <div className="w-full" style={{ maxWidth: 480 }}>
          <Mono muted size={11} className="block mb-3.5">
            {kicker}
          </Mono>
          <h1
            className="font-sans font-extrabold m-0 mb-3"
            style={{ fontSize: 48, letterSpacing: "-0.04em", lineHeight: 1 }}
          >
            {title}
          </h1>
          {sub && (
            <p
              className="text-[15px] text-text-muted leading-[1.5] m-0 mb-8"
              style={{ maxWidth: 460 }}
            >
              {sub}
            </p>
          )}
          {children}
        </div>
      </div>
      <div className="bg-surface-alt border-l border-hair px-12 py-16 flex flex-col justify-center">
        <div className="w-full mx-auto" style={{ maxWidth: 440 }}>
          {side}
        </div>
      </div>
    </div>
  );
}
