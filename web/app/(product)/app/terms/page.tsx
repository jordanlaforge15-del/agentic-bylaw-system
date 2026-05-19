// /app/terms — Terms & Conditions click-wrap acceptance screen.
//
// Shown to a freshly-signed-in user on first login (the /app page
// server-redirects here when no acceptance row exists for the current
// version). Once they click I Agree, the POST records the row and
// the page hard-navigates to /app where the gate now lets them
// through.
//
// Rendering choices that matter:
//
//   * The document is rendered in full inside a scrollable container,
//     not behind a "View" link or a collapsed accordion. Per the
//     ABS-18 spec, click-wrap enforceability depends on the user
//     having actually been able to read what they're agreeing to.
//   * The I Agree button sits at the bottom of the scroll. We do NOT
//     gate it behind a must-scroll-to-bottom check — that pattern
//     adds friction for the screen-reader-and-back-button case and
//     does not strengthen click-wrap meaningfully (courts care about
//     "was the document reasonably presented", not about scroll
//     telemetry). The full document being visible without
//     interaction is what counts.
//   * The accepted ``version`` hash is the same string we POST back,
//     so a stale-hash race (document edited between fetch and click)
//     surfaces as a 409 from the server and we re-fetch.

"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Btn } from "@/components/btn";
import { Mono } from "@/components/mono";

type TermsResponse = {
  version: string;
  body: string;
  accepted: boolean;
};

export default function TermsAcceptancePage() {
  const router = useRouter();
  const [terms, setTerms] = useState<TermsResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch("/api/terms", { cache: "no-store" });
        if (!res.ok) {
          setLoadError(
            `Could not load the Terms (HTTP ${res.status}). Reload the page to try again.`,
          );
          return;
        }
        const data = (await res.json()) as TermsResponse;
        if (cancelled) return;
        // Defensive: if the server says we already accepted (rare
        // race: user opened /app/terms by typing the URL directly),
        // just bounce to /app rather than showing a redundant
        // acceptance UI.
        if (data.accepted) {
          router.replace("/app");
          return;
        }
        setTerms(data);
      } catch {
        if (cancelled) return;
        setLoadError(
          "Could not reach the server. Reload the page to try again.",
        );
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [router]);

  async function handleAccept() {
    if (!terms || submitting) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const res = await fetch("/api/terms/accept", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ version: terms.version }),
      });
      if (res.status === 409) {
        // Stale-hash race. Re-fetch and ask the user to re-read.
        setSubmitError(
          "The Terms changed while you were reading. The page will reload with the current version.",
        );
        setTimeout(() => window.location.reload(), 1500);
        return;
      }
      if (!res.ok) {
        setSubmitError(
          `Could not record acceptance (HTTP ${res.status}). Try again in a moment.`,
        );
        setSubmitting(false);
        return;
      }
      // Hard navigate so the server-side gate at /app re-runs and
      // sees the new acceptance row.
      window.location.assign("/app");
    } catch {
      setSubmitError("Could not reach the server. Try again in a moment.");
      setSubmitting(false);
    }
  }

  return (
    <main
      className="min-h-dvh bg-surface text-text"
      data-testid="terms-acceptance-page"
    >
      <div
        className="mx-auto px-6 sm:px-10 py-10 sm:py-14 flex flex-col gap-6"
        style={{ maxWidth: 880 }}
      >
        <header className="flex flex-col gap-3 pb-4 border-b border-hair">
          <Mono muted size={11}>
            BEFORE YOU START · ABS°
          </Mono>
          <h1
            className="font-sans font-extrabold m-0"
            style={{
              fontSize: 36,
              letterSpacing: "-0.035em",
              lineHeight: 1.05,
            }}
          >
            Terms and Conditions
          </h1>
          <p className="text-[14px] text-text-muted leading-[1.5] m-0">
            Your trial is approved. Read the Terms below and click{" "}
            <strong className="text-text">I Agree</strong> at the end to
            start using ABS°.
          </p>
        </header>

        {loadError && (
          <div
            className="border border-hair p-4 text-[13.5px]"
            style={{ color: "var(--brick)" }}
            role="alert"
          >
            {loadError}
          </div>
        )}

        {terms && (
          <article
            data-testid="terms-document"
            data-terms-version={terms.version}
            className="bg-surface-alt border border-hair p-6 sm:p-8 overflow-y-auto text-[14px] leading-[1.55]"
            style={{ maxHeight: "60vh" }}
          >
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {terms.body}
            </ReactMarkdown>
          </article>
        )}

        {terms && (
          <div className="flex flex-col gap-3">
            <p className="text-[13px] text-text-muted leading-[1.5] m-0">
              By clicking <strong className="text-text">I Agree</strong> you
              acknowledge that you have read and accept these Terms and
              Conditions in full. Your acceptance is recorded with your user
              identifier, the time, your IP address, and the version of the
              Terms shown above.
            </p>
            {submitError && (
              <div
                className="text-[12.5px]"
                style={{ color: "var(--brick)" }}
                role="alert"
              >
                {submitError}
              </div>
            )}
            <div className="flex flex-row gap-3 items-center">
              <Btn
                variant="primary"
                size="lg"
                onClick={handleAccept}
                disabled={submitting}
                data-testid="terms-agree-button"
              >
                {submitting ? "Recording…" : "I Agree →"}
              </Btn>
              <Mono muted size={11} data-testid="terms-version-pill">
                VERSION {terms.version.slice(0, 8)}
              </Mono>
            </div>
          </div>
        )}
      </div>
    </main>
  );
}
