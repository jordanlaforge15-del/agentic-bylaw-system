"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

type AttributeRow = {
  attribute_key: string;
  value: unknown;
  unit: string | null;
  confidence: number;
  source: string;
  evidence: Record<string, unknown> | null;
};

type SubmissionView = {
  id: number;
  parcel_id: number | null;
  status: string;
  source_type: string;
  attributes: AttributeRow[];
  latest_decision: Record<string, unknown> | null;
  warnings: string[];
};

type RegulatedMissing = {
  attribute_key: string;
  description: string;
  unit: string | null;
};

export function ConfirmClient({
  submissionId,
}: {
  submissionId: number;
}) {
  const router = useRouter();
  const [submission, setSubmission] = useState<SubmissionView | null>(null);
  const [regulatedMissing, setRegulatedMissing] = useState<RegulatedMissing[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [confirmError, setConfirmError] = useState<string | null>(null);

  const [addressedKeys, setAddressedKeys] = useState<Set<string>>(new Set());
  const [flaggedKeys, setFlaggedKeys] = useState<Set<string>>(new Set());
  const [manualEntries, setManualEntries] = useState<
    Record<string, { value: string; unit: string | null }>
  >({});

  const refresh = useCallback(async () => {
    setLoadError(null);
    try {
      const [subRes, regRes] = await Promise.all([
        fetch(`/api/submissions/${submissionId}`, { cache: "no-store" }),
        fetch(`/api/submissions/${submissionId}/regulated-missing`, {
          cache: "no-store",
        }),
      ]);
      if (!subRes.ok) {
        setLoadError(`Failed to load submission (HTTP ${subRes.status}).`);
        return;
      }
      const subData = await subRes.json();
      setSubmission(subData);
      if (regRes.ok) {
        setRegulatedMissing(await regRes.json());
      }
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "Load failed.");
    }
  }, [submissionId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const redConfidenceAttrs =
    submission?.attributes.filter((a) => a.confidence < 0.6) ?? [];
  const yellowConfidenceAttrs =
    submission?.attributes.filter(
      (a) => a.confidence >= 0.6 && a.confidence < 0.9,
    ) ?? [];

  const flaggedNeedingAction = [...flaggedKeys].filter(
    (k) => !addressedKeys.has(k),
  );
  const redNeedingAction = redConfidenceAttrs.filter(
    (a) => !addressedKeys.has(a.attribute_key),
  );
  const missingNeedingAction = regulatedMissing.filter(
    (m) => !manualEntries[m.attribute_key]?.value,
  );

  const canConfirm =
    redNeedingAction.length === 0 &&
    flaggedNeedingAction.length === 0 &&
    missingNeedingAction.length === 0;

  async function handleConfirmAndEvaluate() {
    setConfirming(true);
    setConfirmError(null);

    try {
      for (const [key, entry] of Object.entries(manualEntries)) {
        if (!entry.value) continue;
        const parsed = parseValue(entry.value);
        const res = await fetch(
          `/api/submissions/${submissionId}/attributes/${encodeURIComponent(key)}`,
          {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ value: parsed, unit: entry.unit }),
          },
        );
        if (!res.ok) {
          setConfirmError(`Failed to save attribute ${key}.`);
          setConfirming(false);
          return;
        }
      }

      const confirmRes = await fetch(
        `/api/submissions/${submissionId}/confirm`,
        { method: "POST" },
      );
      if (!confirmRes.ok) {
        setConfirmError("Failed to confirm submission.");
        setConfirming(false);
        return;
      }

      const evalRes = await fetch(
        `/api/submissions/${submissionId}/evaluate`,
        { method: "POST" },
      );
      if (!evalRes.ok) {
        const body = await evalRes.json().catch(() => null);
        const message =
          (body?.detail?.message as string | undefined) ||
          `Evaluator failed (HTTP ${evalRes.status}).`;
        setConfirmError(message);
        setConfirming(false);
        return;
      }

      router.push(`/submissions/${submissionId}`);
    } catch (err) {
      setConfirmError(
        err instanceof Error ? err.message : "Confirmation failed.",
      );
      setConfirming(false);
    }
  }

  if (loadError) {
    return (
      <div
        data-testid="confirm-load-error"
        role="alert"
        className="rounded border border-red-500 bg-red-50 p-3 text-[13px] text-red-900"
      >
        {loadError}
      </div>
    );
  }
  if (!submission) {
    return <div data-testid="confirm-loading">Loading...</div>;
  }

  return (
    <div className="flex flex-col gap-8">
      <section data-testid="confirm-attributes">
        <h2 className="text-[18px] font-bold mb-3">Extracted attributes</h2>
        <table
          data-testid="confirm-attributes-table"
          className="w-full text-[13px] border-collapse"
        >
          <thead>
            <tr className="border-b border-hair text-left">
              <th className="py-2">Attribute</th>
              <th>Value</th>
              <th>Unit</th>
              <th>Confidence</th>
              <th>Source</th>
              <th>Override</th>
              <th>Flag</th>
            </tr>
          </thead>
          <tbody>
            {submission.attributes.map((row) => (
              <ConfirmAttributeRow
                key={row.attribute_key}
                submissionId={submissionId}
                row={row}
                isFlagged={flaggedKeys.has(row.attribute_key)}
                isAddressed={addressedKeys.has(row.attribute_key)}
                onFlag={() => {
                  setFlaggedKeys((prev) => {
                    const next = new Set(prev);
                    if (next.has(row.attribute_key)) {
                      next.delete(row.attribute_key);
                    } else {
                      next.add(row.attribute_key);
                    }
                    return next;
                  });
                }}
                onOverride={() => {
                  setAddressedKeys((prev) => {
                    const next = new Set(prev);
                    next.add(row.attribute_key);
                    return next;
                  });
                  void refresh();
                }}
              />
            ))}
          </tbody>
        </table>
      </section>

      {submission.warnings.length > 0 && (
        <div
          data-testid="confirm-warnings"
          className="rounded border border-amber-400 bg-amber-50 p-2 text-[12px] text-amber-900"
        >
          <strong>Warnings:</strong>
          <ul className="list-disc pl-5">
            {submission.warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </div>
      )}

      {regulatedMissing.length > 0 && (
        <section data-testid="missing-regulated-panel">
          <h2 className="text-[18px] font-bold mb-3">
            Missing regulated attributes
          </h2>
          <p className="text-[13px] text-text-muted mb-3">
            These attributes are regulated for this zone but were not
            found in the PDF. Enter values manually to include them in
            the evaluation.
          </p>
          <table
            data-testid="missing-regulated-table"
            className="w-full text-[13px] border-collapse"
          >
            <thead>
              <tr className="border-b border-hair text-left">
                <th className="py-2">Attribute</th>
                <th>Description</th>
                <th>Unit</th>
                <th>Value</th>
              </tr>
            </thead>
            <tbody>
              {regulatedMissing.map((entry) => (
                <tr
                  key={entry.attribute_key}
                  data-testid={`missing-row-${entry.attribute_key}`}
                  className="border-b border-hair"
                >
                  <td className="py-2 font-mono text-[12px]">
                    {entry.attribute_key}
                  </td>
                  <td className="text-[12px] text-text-muted max-w-[300px]">
                    {entry.description}
                  </td>
                  <td className="text-text-muted">{entry.unit || "—"}</td>
                  <td>
                    <input
                      data-testid={`missing-input-${entry.attribute_key}`}
                      type="text"
                      value={manualEntries[entry.attribute_key]?.value ?? ""}
                      onChange={(e) =>
                        setManualEntries((prev) => ({
                          ...prev,
                          [entry.attribute_key]: {
                            value: e.target.value,
                            unit: entry.unit,
                          },
                        }))
                      }
                      placeholder="Enter value"
                      className="w-28 rounded border border-hair p-1 text-[12px]"
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      <section data-testid="confirm-action">
        <div className="flex flex-col gap-3">
          {!canConfirm && (
            <div
              data-testid="confirm-blockers"
              className="rounded border border-amber-400 bg-amber-50 p-3 text-[13px] text-amber-900"
            >
              <strong>Before you can proceed:</strong>
              <ul className="list-disc pl-5 mt-1">
                {redNeedingAction.length > 0 && (
                  <li>
                    {redNeedingAction.length} low-confidence attribute
                    {redNeedingAction.length > 1 ? "s" : ""} need
                    override or review:{" "}
                    {redNeedingAction.map((a) => a.attribute_key).join(", ")}
                  </li>
                )}
                {flaggedNeedingAction.length > 0 && (
                  <li>
                    {flaggedNeedingAction.length} flagged attribute
                    {flaggedNeedingAction.length > 1 ? "s" : ""} need
                    correction: {flaggedNeedingAction.join(", ")}
                  </li>
                )}
                {missingNeedingAction.length > 0 && (
                  <li>
                    {missingNeedingAction.length} regulated attribute
                    {missingNeedingAction.length > 1 ? "s" : ""}{" "}
                    missing: {missingNeedingAction.map((m) => m.attribute_key).join(", ")}
                  </li>
                )}
              </ul>
            </div>
          )}

          <button
            data-testid="confirm-and-evaluate-button"
            type="button"
            disabled={!canConfirm || confirming}
            onClick={handleConfirmAndEvaluate}
            className="self-start rounded bg-black px-5 py-2.5 text-[14px] text-white disabled:opacity-50"
          >
            {confirming
              ? "Confirming + evaluating..."
              : "I've reviewed every flagged attribute — run evaluator"}
          </button>

          {confirmError && (
            <div
              data-testid="confirm-error"
              role="alert"
              className="rounded border border-red-500 bg-red-50 p-3 text-[13px] text-red-900"
            >
              {confirmError}
            </div>
          )}
        </div>
      </section>
    </div>
  );
}

function ConfirmAttributeRow({
  submissionId,
  row,
  isFlagged,
  isAddressed,
  onFlag,
  onOverride,
}: {
  submissionId: number;
  row: AttributeRow;
  isFlagged: boolean;
  isAddressed: boolean;
  onFlag: () => void;
  onOverride: () => void;
}) {
  const [overrideValue, setOverrideValue] = useState("");
  const [saving, setSaving] = useState(false);
  const [showEvidence, setShowEvidence] = useState(false);

  async function save() {
    setSaving(true);
    try {
      const parsed = parseValue(overrideValue);
      const res = await fetch(
        `/api/submissions/${submissionId}/attributes/${encodeURIComponent(row.attribute_key)}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ value: parsed, unit: row.unit }),
        },
      );
      if (res.ok) {
        setOverrideValue("");
        onOverride();
      }
    } finally {
      setSaving(false);
    }
  }

  const confidenceColor =
    row.confidence >= 0.9
      ? "bg-green-100 text-green-900"
      : row.confidence >= 0.6
        ? "bg-yellow-100 text-yellow-900"
        : "bg-red-100 text-red-900";

  const evidenceSnippet =
    row.evidence && typeof row.evidence.pdf_snippet === "string"
      ? row.evidence.pdf_snippet
      : null;

  return (
    <>
      <tr
        data-testid={`confirm-row-${row.attribute_key}`}
        className={`border-b border-hair ${isFlagged ? "bg-red-50" : ""} ${isAddressed ? "bg-green-50" : ""}`}
      >
        <td className="py-2 font-mono text-[12px]">
          <span>{row.attribute_key}</span>
          {evidenceSnippet && (
            <button
              data-testid={`evidence-toggle-${row.attribute_key}`}
              type="button"
              onClick={() => setShowEvidence(!showEvidence)}
              className="ml-2 text-[11px] text-blue-600 underline"
            >
              {showEvidence ? "hide" : "evidence"}
            </button>
          )}
        </td>
        <td>{formatValue(row.value)}</td>
        <td className="text-text-muted">{row.unit || "—"}</td>
        <td>
          <span
            data-testid={`confidence-badge-${row.attribute_key}`}
            className={`rounded px-2 py-0.5 text-[11px] ${confidenceColor}`}
          >
            {(row.confidence * 100).toFixed(0)}%
          </span>
        </td>
        <td className="text-text-muted">{row.source}</td>
        <td>
          <div className="flex gap-1">
            <input
              data-testid={`confirm-override-input-${row.attribute_key}`}
              type="text"
              value={overrideValue}
              onChange={(e) => setOverrideValue(e.target.value)}
              placeholder="New value"
              className="w-24 rounded border border-hair p-1 text-[12px]"
            />
            <button
              data-testid={`confirm-override-button-${row.attribute_key}`}
              type="button"
              disabled={saving || !overrideValue}
              onClick={save}
              className="rounded border border-hair px-2 py-1 text-[12px] disabled:opacity-50"
            >
              Save
            </button>
          </div>
        </td>
        <td>
          <button
            data-testid={`flag-button-${row.attribute_key}`}
            type="button"
            onClick={onFlag}
            className={`rounded border px-2 py-1 text-[11px] ${
              isFlagged
                ? "border-red-400 bg-red-100 text-red-900"
                : "border-hair text-text-muted"
            }`}
          >
            {isFlagged ? "Flagged" : "Flag"}
          </button>
        </td>
      </tr>
      {showEvidence && evidenceSnippet && (
        <tr data-testid={`evidence-row-${row.attribute_key}`}>
          <td colSpan={7} className="bg-gray-50 px-4 py-2 text-[12px]">
            <strong className="text-[11px] text-text-muted">
              PDF evidence:
            </strong>
            <pre className="mt-1 whitespace-pre-wrap font-mono text-[11px]">
              {evidenceSnippet}
            </pre>
          </td>
        </tr>
      )}
    </>
  );
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function parseValue(raw: string): unknown {
  const trimmed = raw.trim();
  if (!trimmed) return trimmed;
  if (trimmed === "true") return true;
  if (trimmed === "false") return false;
  const asNumber = Number(trimmed);
  if (!Number.isNaN(asNumber) && trimmed !== "") return asNumber;
  return trimmed;
}
