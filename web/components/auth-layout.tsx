// Two-column auth shell shared by /signup (and the confirmation state).
// Left pane carries the kicker / title / sub / form; right pane (`side`)
// is per-screen context — last-session preview on login (legacy),
// who-uses-ABS personas on signup.
//
// Responsive contract:
//   - base (< 640): single column, form first, side panel below as a
//     stacked sibling. Inputs fill the viewport width.
//   - sm (≥ 640): still single column but with more breathing room and
//     a max-width on the form panel.
//   - lg (≥ 1024): true two-column layout, side pane on the right with
//     an inverted background and a vertical hairline divider.
//
// The min-height is set in svh on mobile (so the iOS URL bar can do its
// thing without orphaning the panel mid-scroll) and switches back to
// the legacy fixed value on desktop.

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
    <div className="grid grid-cols-1 lg:grid-cols-2 lg:[min-height:calc(100vh-220px)]">
      <div className="px-5 sm:px-8 lg:px-12 py-10 sm:py-12 lg:py-16 flex flex-col justify-center w-full mx-auto">
        <div className="w-full mx-auto sm:mx-0 max-w-[480px]">
          <Mono muted size={11} className="block mb-3 sm:mb-3.5">
            {kicker}
          </Mono>
          <h1
            className="font-sans font-extrabold m-0 mb-2.5 sm:mb-3 text-[36px] sm:text-[44px] lg:text-[48px] leading-[1]"
            style={{ letterSpacing: "-0.04em" }}
          >
            {title}
          </h1>
          {sub && (
            <p className="text-[14px] sm:text-[15px] text-text-muted leading-[1.5] m-0 mb-6 sm:mb-7 lg:mb-8 max-w-[460px]">
              {sub}
            </p>
          )}
          {children}
        </div>
      </div>
      <div className="bg-surface-alt border-t lg:border-t-0 lg:border-l border-hair px-5 sm:px-8 lg:px-12 py-10 sm:py-12 lg:py-16 flex flex-col justify-center">
        <div className="w-full mx-auto sm:mx-0 max-w-[440px]">{side}</div>
      </div>
    </div>
  );
}
