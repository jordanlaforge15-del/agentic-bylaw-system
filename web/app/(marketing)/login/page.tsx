// /login — email + password with OAuth/magic-link fallbacks. The right
// pane shows a "from your last session" reading card to communicate
// what waiting for the user inside. Submitting the form (mock auth)
// routes to /app.

"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { AuthLayout } from "@/components/auth-layout";
import { Btn } from "@/components/btn";
import { Field } from "@/components/form";
import { HighlightWord } from "@/components/highlight-word";
import { Mono } from "@/components/mono";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [pw, setPw] = useState("");

  return (
    <AuthLayout
      kicker="LOG IN · ABS°"
      title={
        <>
          Welcome <HighlightWord>back.</HighlightWord>
        </>
      }
      sub="Pick up where you left your last reading."
      side={<LastSession />}
    >
      <form
        onSubmit={(e) => {
          e.preventDefault();
          router.push("/app");
        }}
        className="flex flex-col gap-4"
      >
        <Field
          label="EMAIL"
          type="email"
          placeholder="you@firm.com"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          autoComplete="email"
          required
        />
        <Field
          label="PASSWORD"
          type="password"
          placeholder="••••••••"
          value={pw}
          onChange={(e) => setPw(e.target.value)}
          autoComplete="current-password"
          required
        />
        <div className="flex justify-end">
          <button
            type="button"
            className="bg-transparent border-none text-text-muted text-[12px] cursor-pointer font-sans hover:text-text"
          >
            Forgot password?
          </button>
        </div>
        <Btn variant="primary" size="lg" type="submit">
          Log in →
        </Btn>

        <div className="flex items-center gap-3 my-2">
          <div className="flex-1 h-px bg-hair" />
          <Mono muted size={9.5}>
            OR
          </Mono>
          <div className="flex-1 h-px bg-hair" />
        </div>

        <Btn variant="ghost" size="lg" type="button">
          Continue with Google
        </Btn>
        <Btn variant="ghost" size="lg" type="button">
          Continue with magic link
        </Btn>

        <div className="mt-3 text-[13px] text-text-muted">
          Don&apos;t have an account?{" "}
          <Link
            href="/signup"
            className="text-text underline underline-offset-2"
          >
            Request an invite
          </Link>
        </div>
      </form>
    </AuthLayout>
  );
}

function LastSession() {
  return (
    <div className="flex flex-col gap-[18px]">
      <Mono muted size={11}>
        FROM YOUR LAST SESSION
      </Mono>
      <div
        className="bg-surface p-[22px] flex flex-col gap-3"
        style={{ border: "1px solid var(--hair)" }}
      >
        <div className="flex justify-between items-baseline">
          <Mono muted size={10}>
            5184 MORRIS ST · ER-1
          </Mono>
          <Mono accent size={10}>
            OPEN
          </Mono>
        </div>
        <div className="text-[13px] text-text-muted italic">
          &ldquo;Can I add a backyard suite?&rdquo;
        </div>
        <div
          className="font-sans font-extrabold text-[22px] leading-[1.15]"
          style={{ letterSpacing: "-0.03em" }}
        >
          <HighlightWord>Yes — up to 80 m².</HighlightWord>
        </div>
        <div className="pt-2.5 border-t border-hair flex justify-between">
          <Mono muted size={9.5}>
            HRM LUB § 9.4
          </Mono>
          <Mono muted size={9.5}>
            UPDATED 2 DAYS AGO
          </Mono>
        </div>
      </div>
      <p className="text-[13px] text-text-muted leading-[1.5] m-0">
        Three readings in progress, two awaiting your review.
      </p>
    </div>
  );
}
