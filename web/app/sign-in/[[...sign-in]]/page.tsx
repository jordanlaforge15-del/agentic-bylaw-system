// Clerk-hosted sign-in widget themed to ABS° brand. Catch-all
// segment so Clerk's internal multi-step flows (verify-email,
// reset-password, OAuth callbacks) all resolve back to this page.
//
// The wrapper mirrors the original /access shell — ABS logo at
// top, mono kicker, sharp corners — so the auth surface feels of
// a piece with the marketing site rather than dropping the user
// into Clerk's default Tailwind look.

import Link from "next/link";
import { SignIn } from "@clerk/nextjs";
import { ABSLogo } from "@/components/abs-logo";
import { Mono } from "@/components/mono";

export default function SignInPage() {
  return (
    <main className="min-h-screen flex items-center justify-center bg-surface text-text px-6 py-10">
      <div className="w-full" style={{ maxWidth: 420 }}>
        <Link
          href="/"
          aria-label="ABS home"
          className="inline-flex items-center mb-7"
        >
          <ABSLogo size={28} />
        </Link>
        <Mono muted size={11} className="block mb-3">
          MEMBER SIGN-IN · ABS°
        </Mono>
        <h1
          className="font-sans font-extrabold m-0 mb-3"
          style={{ fontSize: 36, letterSpacing: "-0.035em", lineHeight: 1.05 }}
        >
          Welcome back.
        </h1>
        <p className="text-[14px] text-text-muted leading-[1.5] m-0 mb-7">
          Sign in to continue your reading.
        </p>
        {/* signUpUrl overrides the ClerkProvider default for the
            "Don't have an account?" footer link only. During private
            beta we send unauthenticated visitors to /signup (the
            invite-request form), not to /sign-up (Clerk's create-
            account widget — which is gated dashboard-side anyway).
            Drop this prop when self-serve signup goes live. */}
        <SignIn appearance={absClerkAppearance} signUpUrl="/signup" />
      </div>
    </main>
  );
}

// Match the design system: sharp corners, hairline borders, the
// brand accent on the focal CTA, and JetBrains Mono for any small
// labels. Colors come from CSS variables so dark-mode (`Setback`)
// inherits without a second appearance object.
const absClerkAppearance = {
  variables: {
    colorPrimary: "var(--accent-ink)",
    colorBackground: "var(--surface)",
    colorText: "var(--text)",
    colorTextSecondary: "var(--text-muted)",
    colorInputBackground: "var(--surface)",
    colorInputText: "var(--text)",
    colorDanger: "var(--brick)",
    borderRadius: "0",
    fontFamily: "var(--font-sans)",
  },
  elements: {
    rootBox: "w-full",
    card: "shadow-none border border-hair bg-surface p-6 rounded-none",
    headerTitle: "hidden",
    headerSubtitle: "hidden",
    socialButtonsBlockButton:
      "rounded-none border border-hair bg-surface text-text hover:bg-surface-alt",
    formButtonPrimary:
      "rounded-none bg-accent text-on-accent border border-accent font-semibold tracking-tight hover:opacity-90",
    formFieldInput:
      "rounded-none border border-hair bg-surface text-text focus:border-text",
    formFieldLabel: "font-mono uppercase text-[11px] tracking-[0.14em]",
    footerActionLink: "text-text underline underline-offset-2",
    dividerLine: "bg-hair",
    dividerText: "text-text-muted font-mono uppercase text-[10px]",
  },
};
