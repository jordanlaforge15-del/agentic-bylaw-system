// Clerk-hosted sign-up widget. Mirror of /sign-in — same shell,
// same theme tokens — so the move from "create account" to
// "sign in" doesn't visually jolt the user.
//
// During private beta the page leads with an "invite-only" notice
// and a CTA back to /signup (the invite-request form). The Clerk
// <SignUp> widget is still rendered below because:
//   1. Clerk's email-invitation flow lands on /sign-up/verify-... —
//      removing the widget would break that flow for invited users.
//   2. The actual gate is enforced dashboard-side ("Restrict signups
//      to allowlist"): even if an uninvited visitor pastes in an
//      email, Clerk responds with "this email isn't on the allow
//      list" and account creation fails. The widget is safe to leave
//      visible — it just won't succeed without an invite.
// Once the beta gate is removed, drop the invite-only notice block
// and the H1 reverts to plain "Get started."

import type { Metadata } from "next";
import { pageMetadata } from "@/lib/page-metadata";
import Link from "next/link";
import { SignUp } from "@clerk/nextjs";
import { ABSLogo } from "@/components/abs-logo";
import { Btn } from "@/components/btn";
import { Mono } from "@/components/mono";

// Credential gate, not a landing page (ABS-536).
//
// `noindex` and a self-referencing canonical. Without its own
// `alternates`, this page inherits the root layout's `canonical: "/"`
// and tells Google it is a duplicate of the home page — Next merges
// `metadata` one key at a time, so an absent key is an inherited key,
// not an empty one. That is what production was serving: /sign-in and
// /sign-up both naming the home page as their canonical.
//
// robots.txt disallows this path too, so Googlebot should not fetch it
// at all. The `noindex` is here for the case where it already has the
// URL from somewhere else — a disallowed URL can still be indexed
// URL-only, and a `Disallow` guarantees Google never sees a `noindex`
// it could act on.
export const metadata: Metadata = pageMetadata({
  path: "/sign-up",
  title: "Create Account — ABS°",
  description:
    "Account creation for ABS° is invite-only during the private beta. Request an invite, or sign in if you already have an account.",
  noindex: true,
});


export default function SignUpPage() {
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
          NEW ACCOUNT · ABS°
        </Mono>
        <h1
          className="font-sans font-extrabold m-0 mb-3"
          style={{ fontSize: 36, letterSpacing: "-0.035em", lineHeight: 1.05 }}
        >
          Invite only, for now.
        </h1>
        <p className="text-[14px] text-text-muted leading-[1.5] m-0 mb-5">
          ABS° is in private beta. New accounts are by invitation. If you
          have one, the link in your email completes the flow below —
          otherwise, request access first.
        </p>
        <Link href="/signup" className="contents">
          <Btn variant="primary" size="md" className="mb-7">
            Request an invite →
          </Btn>
        </Link>
        <SignUp appearance={absClerkAppearance} />
      </div>
    </main>
  );
}

// Kept inline rather than shared with /sign-in so each page can
// drift independently if the brand needs different chrome on
// "create" vs "return".
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
