// Case-open client form. Two visible steps:
//
//   1. Anchor + first message + (optional) classify → preview tier
//      recommendation + warn about an in-window match.
//   2. Tier selector + Open button.
//
// Stays in /components/marketing/ because the in-app product chrome
// has its own composer that uses /api/chat directly. This form's job
// is to mint the case credit reservation and hand the user off to
// /app?case_id=N&first_message=<encoded>.

"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import {
  ANCHOR_KIND_DISPLAY,
  AnchorKind,
  CaseRow,
  ClassifyResponse,
  MatchResponse,
  OpenCaseResponse,
  Tier,
  TIER_DISPLAY,
} from "@/lib/cases";
import { Btn } from "@/components/btn";
import { Mono } from "@/components/mono";

const TIER_BUDGETS: Record<Tier, string> = {
  quick: "12k tokens · 4–6 retrieval rounds",
  standard: "45k tokens · 12–18 retrieval rounds",
  complex: "130k tokens · 35–50 retrieval rounds",
};

const ANCHOR_KIND_OPTIONS: AnchorKind[] = [
  "address",
  "project_ref",
  "development_application",
];

const TIER_OPTIONS: Tier[] = ["quick", "standard", "complex"];


export function CaseOpenForm() {
  const router = useRouter();
  const [anchorLabel, setAnchorLabel] = useState("");
  const [anchorKind, setAnchorKind] = useState<AnchorKind>("address");
  const [message, setMessage] = useState("");
  const [tier, setTier] = useState<Tier>("standard");
  const [match, setMatch] = useState<CaseRow | null | undefined>(undefined);
  const [classification, setClassification] = useState<
    ClassifyResponse | null
  >(null);
  const [working, setWorking] = useState<
    "idle" | "matching" | "classifying" | "opening"
  >("idle");
  const [error, setError] = useState<string | null>(null);

  async function lookupMatch() {
    if (!anchorLabel.trim()) return;
    setWorking("matching");
    setMatch(undefined);
    try {
      const r = await fetch(
        `/api/cases/match?anchor_label=${encodeURIComponent(anchorLabel)}&anchor_kind=${encodeURIComponent(anchorKind)}`,
      );
      if (!r.ok) {
        setMatch(null);
        return;
      }
      const data = (await r.json()) as MatchResponse;
      setMatch(data.case);
    } finally {
      setWorking("idle");
    }
  }

  async function classify() {
    if (!anchorLabel.trim() || !message.trim()) {
      setError("Anchor and first message are required to classify.");
      return;
    }
    setWorking("classifying");
    setError(null);
    try {
      const r = await fetch("/api/cases/classify", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          anchor_label: anchorLabel,
          anchor_kind: anchorKind,
          message,
        }),
      });
      if (!r.ok) {
        setError("Couldn't reach the classifier. Pick a tier manually.");
        return;
      }
      const data = (await r.json()) as ClassifyResponse;
      setClassification(data);
      // If the classifier comes back with high confidence, suggest
      // its tier (the user can still override with the radio group).
      if (data.confidence >= 0.7) {
        setTier(data.tier);
      }
    } finally {
      setWorking("idle");
    }
  }

  async function openCase() {
    if (!anchorLabel.trim()) {
      setError("Anchor is required.");
      return;
    }
    setWorking("opening");
    setError(null);
    try {
      const r = await fetch("/api/cases", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          anchor_label: anchorLabel,
          anchor_kind: anchorKind,
          tier,
        }),
      });
      if (r.status === 401) {
        router.push("/login?next=/cases/new");
        return;
      }
      if (r.status === 402) {
        // No available credit at the requested tier.
        const detail = await r.json().catch(() => null);
        const t =
          (detail?.detail as { tier?: string } | undefined)?.tier ?? tier;
        setError(
          `No available ${t} credits. Buy a credit on /pricing first.`,
        );
        return;
      }
      if (!r.ok) {
        const detail = await r.json().catch(() => null);
        const msg =
          (detail?.detail as { message?: string } | undefined)?.message ??
          `Failed to open case (${r.status}).`;
        setError(msg);
        return;
      }
      const data = (await r.json()) as OpenCaseResponse;
      // Redirect into the chat product with the case_id pre-bound.
      // The first message is sent from the chat composer — passing it
      // through the URL would risk losing it on a hard refresh, and
      // the chat API call must happen client-side anyway.
      const params = new URLSearchParams({
        case_id: String(data.case.id),
      });
      if (message.trim()) {
        params.set("first_message", message.trim());
      }
      router.push(`/app?${params.toString()}`);
    } finally {
      setWorking("idle");
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <Field label="Anchor">
        <div className="flex gap-2">
          <select
            aria-label="Anchor kind"
            value={anchorKind}
            onChange={(e) => {
              setAnchorKind(e.target.value as AnchorKind);
              setMatch(undefined);
              setClassification(null);
            }}
            className="bg-surface border border-hair px-3 py-2 text-[13.5px]"
          >
            {ANCHOR_KIND_OPTIONS.map((k) => (
              <option key={k} value={k}>
                {ANCHOR_KIND_DISPLAY[k]}
              </option>
            ))}
          </select>
          <input
            type="text"
            value={anchorLabel}
            onChange={(e) => {
              setAnchorLabel(e.target.value);
              setMatch(undefined);
            }}
            onBlur={lookupMatch}
            placeholder={
              anchorKind === "address"
                ? "e.g. 1234 Main St, Halifax"
                : anchorKind === "project_ref"
                  ? "e.g. Project NS-2026-04"
                  : "e.g. DA-2024-12345"
            }
            className="flex-1 bg-surface border border-hair px-3 py-2 text-[13.5px]"
          />
        </div>
      </Field>

      {match !== undefined && match !== null && (
        <div className="bg-surface-alt border border-hair p-4">
          <Mono size={11} muted>
            EXISTING CASE FOUND
          </Mono>
          <div className="mt-1 text-[13.5px]">
            You opened a case for this anchor on{" "}
            {new Date(match.last_activity_at).toLocaleDateString("en-CA")}{" "}
            ({match.current_tier ? TIER_DISPLAY[match.current_tier] : "—"}).
            Continuing it costs no credits.
          </div>
          <div className="mt-3 flex gap-2">
            <Btn
              variant="primary"
              size="sm"
              onClick={() => router.push(`/app?case_id=${match.id}`)}
            >
              Continue case
            </Btn>
            <Btn
              variant="quiet"
              size="sm"
              onClick={() => setMatch(null)}
            >
              Start new case anyway
            </Btn>
          </div>
        </div>
      )}

      <Field label="First message">
        <textarea
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          rows={5}
          placeholder="Describe the inquiry. The classifier reads this to recommend a tier."
          className="bg-surface border border-hair px-3 py-2 text-[13.5px] font-sans resize-y w-full"
        />
        <div className="flex gap-2 mt-2">
          <Btn
            variant="quiet"
            size="sm"
            onClick={classify}
            disabled={working !== "idle"}
          >
            {working === "classifying"
              ? "Classifying…"
              : "Get tier recommendation"}
          </Btn>
        </div>
      </Field>

      {classification && (
        <div className="bg-surface-alt border border-hair p-4">
          <Mono size={11} muted>
            CLASSIFIER RECOMMENDS · {Math.round(classification.confidence * 100)}%
            CONFIDENCE
          </Mono>
          <div className="mt-1 text-[14px] font-semibold capitalize">
            {TIER_DISPLAY[classification.tier]}
          </div>
          {classification.reasons.length > 0 && (
            <ul className="mt-2 text-[12.5px] text-text-muted list-disc list-inside">
              {classification.reasons.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      <Field label="Open at tier">
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          {TIER_OPTIONS.map((t) => (
            <label
              key={t}
              className={`border p-3 cursor-pointer flex flex-col gap-1 ${
                tier === t ? "border-text" : "border-hair"
              }`}
            >
              <div className="flex items-center gap-2">
                <input
                  type="radio"
                  name="tier"
                  value={t}
                  checked={tier === t}
                  onChange={() => setTier(t)}
                />
                <span className="font-semibold capitalize">
                  {TIER_DISPLAY[t]}
                </span>
              </div>
              <span className="text-[12px] text-text-muted">
                {TIER_BUDGETS[t]}
              </span>
            </label>
          ))}
        </div>
      </Field>

      {error && (
        <div className="text-[13px] text-red-600">{error}</div>
      )}

      <div className="flex gap-3">
        <Btn
          variant="accent"
          size="md"
          onClick={openCase}
          disabled={working !== "idle"}
        >
          {working === "opening" ? "Opening case…" : "Open case"}
        </Btn>
      </div>
    </div>
  );
}


function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <Mono size={11} muted>
        {label.toUpperCase()}
      </Mono>
      {children}
    </div>
  );
}
