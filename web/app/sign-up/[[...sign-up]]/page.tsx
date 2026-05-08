// Clerk-hosted sign-up widget. Mirror of /sign-in — same shell,
// same theme tokens — so the move from "create account" to
// "sign in" doesn't visually jolt the user.

import Link from "next/link";
import { SignUp } from "@clerk/nextjs";
import { ABSLogo } from "@/components/abs-logo";
import { Mono } from "@/components/mono";

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
          Get started.
        </h1>
        <p className="text-[14px] text-text-muted leading-[1.5] m-0 mb-7">
          Create an account to read the Halifax Land Use By-law against your
          parcel.
        </p>
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
