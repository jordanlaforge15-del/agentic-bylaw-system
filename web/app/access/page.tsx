// /access — shared-password barrier shown before /app and /admin.
// Renders without the marketing chrome (it lives at the root path,
// not under (marketing)). The form posts to /api/access; on success
// the API sets a cookie and we client-route to ?from=… (or fall back
// to the gate's home page).

"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { ABSLogo } from "@/components/abs-logo";
import { Btn } from "@/components/btn";
import { Field } from "@/components/form";
import { Mono } from "@/components/mono";

export default function AccessPage() {
  return (
    <main
      className="min-h-screen flex items-center justify-center bg-surface text-text px-6 py-10"
    >
      <div className="w-full" style={{ maxWidth: 380 }}>
        <Link
          href="/"
          aria-label="ABS home"
          className="inline-flex items-center mb-7"
        >
          <ABSLogo size={28} />
        </Link>
        <Suspense fallback={null}>
          <AccessForm />
        </Suspense>
      </div>
    </main>
  );
}

function AccessForm() {
  const router = useRouter();
  const params = useSearchParams();
  const gate: "demo" | "admin" =
    params.get("gate") === "admin" ? "admin" : "demo";
  const from = params.get("from");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const headline = gate === "admin" ? "Admin only." : "Restricted access.";
  const blurb =
    gate === "admin"
      ? "Enter the admin password to view invite requests."
      : "ABS° is in private beta. Enter the demo password you were given. No password yet? Request an invite below.";
  const fieldLabel = gate === "admin" ? "ADMIN PASSWORD" : "DEMO PASSWORD";
  const fallbackTarget = gate === "admin" ? "/admin/invites" : "/app";

  return (
    <>
      <Mono muted size={11} className="block mb-3">
        {gate === "admin" ? "ADMIN · ABS°" : "RESTRICTED · ABS°"}
      </Mono>
      <h1
        className="font-sans font-extrabold m-0 mb-3"
        style={{ fontSize: 36, letterSpacing: "-0.035em", lineHeight: 1.05 }}
      >
        {headline}
      </h1>
      <p
        className="text-[14px] text-text-muted leading-[1.5] m-0 mb-7"
      >
        {blurb}
      </p>

      <form
        onSubmit={async (e) => {
          e.preventDefault();
          if (busy) return;
          setBusy(true);
          setError(null);
          try {
            const res = await fetch("/api/access", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ gate, password }),
            });
            if (!res.ok) {
              const data = (await res.json().catch(() => ({}))) as {
                error?: string;
              };
              setError(data.error || "Wrong password.");
              setBusy(false);
              return;
            }
            router.push(from || fallbackTarget);
          } catch {
            setError("Could not reach the server.");
            setBusy(false);
          }
        }}
        className="flex flex-col gap-3.5"
      >
        <Field
          label={fieldLabel}
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="off"
          required
        />
        {error && (
          <span className="text-[12.5px]" style={{ color: "var(--brick)" }}>
            {error}
          </span>
        )}
        <Btn variant="primary" size="lg" type="submit" disabled={busy}>
          {busy ? "Checking…" : "Continue →"}
        </Btn>
      </form>

      {gate === "demo" && (
        <div className="mt-6 text-[13px] text-text-muted">
          Don&apos;t have a password?{" "}
          <Link
            href="/signup"
            className="text-text underline underline-offset-2"
          >
            Request an invite
          </Link>
        </div>
      )}
    </>
  );
}
