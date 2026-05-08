// /signup — request-invite form (private beta gate). Asks for email,
// name, role, and a one-paragraph project description. Submitting
// flips into a confirmation state with a generated #ABS-NNNN order id.
// Right pane lists the three personas ABS° serves.

"use client";

import { useState } from "react";
import Link from "next/link";
import { AuthLayout } from "@/components/auth-layout";
import { Btn } from "@/components/btn";
import { Field, Select, TextArea } from "@/components/form";
import { HighlightWord } from "@/components/highlight-word";
import { Mono } from "@/components/mono";

const ROLES = [
  "Architect",
  "Homeowner",
  "Developer",
  "Planner / consultant",
  "Other",
];

const PERSONAS = [
  {
    role: "Architects",
    blurb: "Validate massing studies against zone limits before drawing.",
  },
  {
    role: "Homeowners",
    blurb:
      "Confirm an ADU or addition is feasible before hiring an architect.",
  },
  {
    role: "Developers",
    blurb: "Pre-acquisition feasibility. By-right capacity in seconds.",
  },
];

const NEXT_STEPS = [
  {
    n: "01",
    t: "A planner reviews your request",
    d: "We confirm your project is in HRM and ABS° can help.",
  },
  {
    n: "02",
    t: "You get an invite link by email",
    d: "Within 48 hours during business days.",
  },
  {
    n: "03",
    t: "You start reading",
    d: "Set-up takes about 90 seconds. First parcel on us.",
  },
];

export default function SignupPage() {
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState(ROLES[0]);
  const [project, setProject] = useState("");
  const [submitted, setSubmitted] = useState<{ id: string } | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (submitted) {
    return (
      <AuthLayout
        kicker="REQUEST RECEIVED"
        title={
          <>
            We&apos;ll be in <HighlightWord>touch.</HighlightWord>
          </>
        }
        sub="Most invites go out within 48 hours during private beta. We review every request to make sure ABS° is the right fit for your project."
        side={
          <div className="flex flex-col gap-[18px]">
            <Mono muted size={11}>
              WHAT HAPPENS NEXT
            </Mono>
            {NEXT_STEPS.map((s) => (
              <div key={s.n} className="flex gap-3.5">
                <Mono accent size={11} style={{ minWidth: 24 }}>
                  {s.n}
                </Mono>
                <div>
                  <div className="text-[14px] font-semibold mb-0.5">
                    {s.t}
                  </div>
                  <div className="text-[12.5px] text-text-muted leading-[1.45]">
                    {s.d}
                  </div>
                </div>
              </div>
            ))}
          </div>
        }
      >
        <div className="flex flex-col gap-[18px]">
          <div
            className="bg-accent text-on-accent p-[22px] flex flex-col gap-2"
            style={{ color: "var(--on-accent)" }}
          >
            <Mono size={10} style={{ color: "var(--on-accent)" }}>
              CONFIRMATION · #{submitted.id}
            </Mono>
            <div
              className="font-sans font-extrabold text-[22px] leading-[1.1]"
              style={{ letterSpacing: "-0.03em" }}
            >
              You&apos;re on the list.
            </div>
            <div className="text-[13px] leading-[1.45]">
              We&apos;ve sent a copy to {email || "your inbox"}.
            </div>
          </div>
          <Link href="/">
            <Btn variant="ghost" size="lg" className="w-full">
              Back to home
            </Btn>
          </Link>
        </div>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout
      kicker="GET AN INVITE · ABS°"
      title={
        <>
          Tell us about your <HighlightWord>project.</HighlightWord>
        </>
      }
      sub="Private beta, HRM only. We approve invites in batches based on project fit."
      side={
        <div className="flex flex-col gap-[22px]">
          <Mono muted size={11}>
            WHO USES ABS°
          </Mono>
          {PERSONAS.map((p) => (
            <div key={p.role} className="pb-4 border-b border-hair">
              <div className="flex justify-between items-baseline mb-1">
                <span
                  className="text-[17px] font-bold"
                  style={{ letterSpacing: "-0.02em" }}
                >
                  {p.role}
                </span>
                <Mono accent size={10}>
                  ACTIVE
                </Mono>
              </div>
              <div className="text-[13px] text-text-muted leading-[1.45]">
                {p.blurb}
              </div>
            </div>
          ))}
        </div>
      }
    >
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          if (project.trim().length < 10 || submitting) return;
          setSubmitting(true);
          setError(null);
          try {
            const res = await fetch("/api/invite", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ email, name, role, project }),
            });
            if (!res.ok) {
              setError("Could not submit your request. Try again in a moment.");
              setSubmitting(false);
              return;
            }
            const data = (await res.json()) as { id: string };
            setSubmitted({ id: data.id });
          } catch {
            setError("Could not reach the server. Try again in a moment.");
            setSubmitting(false);
          }
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
          label="NAME"
          placeholder="Your name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          autoComplete="name"
          required
        />
        <Select
          label="YOU ARE A…"
          options={ROLES}
          value={role}
          onChange={(e) => setRole(e.target.value)}
        />
        <TextArea
          label="WHAT ARE YOU WORKING ON?"
          placeholder="One project, one paragraph. The address or zone is helpful."
          value={project}
          onChange={(e) => setProject(e.target.value)}
          rows={4}
          required
        />
        <div className="text-[12px] text-text-muted leading-[1.45]">
          By requesting an invite, you agree to our terms and acknowledge ABS°
          is research, not legal advice.
        </div>
        <Btn
          variant="primary"
          size="lg"
          type="submit"
          disabled={project.trim().length < 10 || submitting}
          style={{
            opacity: project.trim().length < 10 || submitting ? 0.55 : 1,
            cursor:
              project.trim().length < 10 || submitting
                ? "not-allowed"
                : "pointer",
          }}
        >
          {submitting ? "Submitting…" : "Request invite →"}
        </Btn>
        {error && (
          <span
            className="text-[12.5px]"
            style={{ color: "var(--brick)" }}
          >
            {error}
          </span>
        )}
        <div className="text-[13px] text-text-muted">
          Already have an account?{" "}
          <Link
            href="/sign-in"
            className="text-text underline underline-offset-2"
          >
            Log in
          </Link>
        </div>
      </form>
    </AuthLayout>
  );
}
