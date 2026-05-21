"use client";

// Interactive island for /submissions/[id].
//
// Loads the submission via /api/submissions/[id], renders the
// extracted-attribute table (with confidence badges + per-row
// override), the "Run evaluator" button, and the compliance matrix
// once an evaluator run has produced a decision.
//
// Kept deliberately simple — one component, no shared state library.
// Re-fetches the submission after override and after evaluator runs
// rather than reaching into the response shapes; that's slightly more
// network but keeps the cache layer (`{ status, attributes,
// latest_decision }`) the single source of truth.

import { useCallback, useEffect, useState } from "react";

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

type ClauseEvaluation = {
  fragment_id: number;
  citation_path: string | null;
  document_id: number;
  bylaw_name: string;
  text: string;
  verdict: string;
  delta: Record<string, unknown> | null;
};

type AttributeResult = {
  attribute_key: string;
  submitted_value: unknown;
  verdict: string;
  delta: Record<string, unknown> | null;
  applicable_clauses: ClauseEvaluation[];
};

type DecisionView = {
  overall_status: string;
  attribute_results: AttributeResult[];
  unevaluated_attributes: string[];
  notes: string[];
};

export function SubmissionDetailClient({
  submissionId,
}: {
  submissionId: number;
}) {
  const [submission, setSubmission] = useState<SubmissionView | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [evaluating, setEvaluating] = useState(false);
  const [evalError, setEvalError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoadError(null);
    try {
      const res = await fetch(`/api/submissions/${submissionId}`, {
        cache: "no-store",
      });
      if (!res.ok) {
        setLoadError(`Failed to load submission (HTTP ${res.status}).`);
        return;
      }
      setSubmission(await res.json());
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "Load failed.");
    }
  }, [submissionId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function runEvaluator() {
    setEvaluating(true);
    setEvalError(null);
    try {
      const res = await fetch(`/api/submissions/${submissionId}/evaluate`, {
        method: "POST",
      });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        const message =
          (body?.detail?.message as string | undefined) ||
          `Evaluator failed (HTTP ${res.status}).`;
        setEvalError(message);
        return;
      }
      await refresh();
    } catch (err) {
      setEvalError(err instanceof Error ? err.message : "Evaluator failed.");
    } finally {
      setEvaluating(false);
    }
  }

  if (loadError) {
    return (
      <div
        data-testid="submission-load-error"
        role="alert"
        className="rounded border border-red-500 bg-red-50 p-3 text-[13px] text-red-900"
      >
        {loadError}
      </div>
    );
  }
  if (!submission) {
    return <div data-testid="submission-loading">Loading…</div>;
  }

  const decision = submission.latest_decision as DecisionView | null;

  return (
    <div className="flex flex-col gap-8">
      <section data-testid="submission-attributes">
        <h2 className="text-[18px] font-bold mb-3">
          Extracted attributes
        </h2>
        <AttributesTable
          submissionId={submissionId}
          attributes={submission.attributes}
          onChange={refresh}
        />
        {submission.warnings.length > 0 && (
          <div
            data-testid="submission-warnings"
            className="mt-3 rounded border border-amber-400 bg-amber-50 p-2 text-[12px] text-amber-900"
          >
            <strong>Warnings:</strong>
            <ul className="list-disc pl-5">
              {submission.warnings.map((w, i) => (
                <li key={i}>{w}</li>
              ))}
            </ul>
          </div>
        )}
      </section>

      <section data-testid="submission-evaluator">
        <h2 className="text-[18px] font-bold mb-3">Run evaluator</h2>
        <button
          data-testid="run-evaluator-button"
          type="button"
          disabled={evaluating}
          onClick={runEvaluator}
          className="rounded bg-black px-4 py-2 text-[14px] text-white disabled:opacity-50"
        >
          {evaluating ? "Evaluating…" : "Run evaluator"}
        </button>
        {evalError && (
          <div
            data-testid="evaluator-error"
            role="alert"
            className="mt-3 rounded border border-red-500 bg-red-50 p-3 text-[13px] text-red-900"
          >
            {evalError}
          </div>
        )}
      </section>

      {decision && <ComplianceMatrix decision={decision} />}
    </div>
  );
}

function AttributesTable({
  submissionId,
  attributes,
  onChange,
}: {
  submissionId: number;
  attributes: AttributeRow[];
  onChange: () => void;
}) {
  return (
    <table
      data-testid="attributes-table"
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
        </tr>
      </thead>
      <tbody>
        {attributes.map((row) => (
          <AttributeRowView
            key={row.attribute_key}
            submissionId={submissionId}
            row={row}
            onChange={onChange}
          />
        ))}
      </tbody>
    </table>
  );
}

function AttributeRowView({
  submissionId,
  row,
  onChange,
}: {
  submissionId: number;
  row: AttributeRow;
  onChange: () => void;
}) {
  const [overrideValue, setOverrideValue] = useState<string>("");
  const [saving, setSaving] = useState(false);

  async function save() {
    setSaving(true);
    try {
      const parsed = parseOverrideValue(overrideValue);
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
        onChange();
      }
    } finally {
      setSaving(false);
    }
  }

  return (
    <tr
      data-testid={`attribute-row-${row.attribute_key}`}
      className="border-b border-hair"
    >
      <td className="py-2 font-mono text-[12px]">{row.attribute_key}</td>
      <td>{formatValue(row.value)}</td>
      <td className="text-text-muted">{row.unit || "—"}</td>
      <td>
        <ConfidenceBadge confidence={row.confidence} />
      </td>
      <td className="text-text-muted">{row.source}</td>
      <td>
        <div className="flex gap-1">
          <input
            data-testid={`override-input-${row.attribute_key}`}
            type="text"
            value={overrideValue}
            onChange={(e) => setOverrideValue(e.target.value)}
            placeholder="New value"
            className="w-24 rounded border border-hair p-1 text-[12px]"
          />
          <button
            data-testid={`override-button-${row.attribute_key}`}
            type="button"
            disabled={saving || !overrideValue}
            onClick={save}
            className="rounded border border-hair px-2 py-1 text-[12px] disabled:opacity-50"
          >
            Save
          </button>
        </div>
      </td>
    </tr>
  );
}

function ConfidenceBadge({ confidence }: { confidence: number }) {
  const color =
    confidence >= 0.9
      ? "bg-green-100 text-green-900"
      : confidence >= 0.5
        ? "bg-yellow-100 text-yellow-900"
        : "bg-red-100 text-red-900";
  return (
    <span
      data-testid="confidence-badge"
      className={`rounded px-2 py-0.5 text-[11px] ${color}`}
    >
      {(confidence * 100).toFixed(0)}%
    </span>
  );
}

function ComplianceMatrix({ decision }: { decision: DecisionView }) {
  return (
    <section data-testid="compliance-matrix">
      <h2 className="text-[18px] font-bold mb-3">Compliance matrix</h2>
      <p data-testid="overall-status" className="mb-3 text-[14px]">
        Overall status: <strong>{decision.overall_status}</strong>
      </p>
      {decision.attribute_results.length === 0 && (
        <p
          data-testid="matrix-empty"
          className="text-[13px] text-text-muted"
        >
          No applicable clauses for the supplied attributes.
        </p>
      )}
      <table className="w-full text-[13px] border-collapse">
        <thead>
          <tr className="border-b border-hair text-left">
            <th className="py-2">Attribute</th>
            <th>Submitted</th>
            <th>Verdict</th>
            <th>Cited clauses</th>
          </tr>
        </thead>
        <tbody>
          {decision.attribute_results.map((ar) => (
            <tr
              key={ar.attribute_key}
              data-testid={`matrix-row-${ar.attribute_key}`}
              className="border-b border-hair align-top"
            >
              <td className="py-2 font-mono text-[12px]">{ar.attribute_key}</td>
              <td>{formatValue(ar.submitted_value)}</td>
              <td>
                <VerdictBadge verdict={ar.verdict} />
              </td>
              <td>
                {ar.applicable_clauses.length === 0 && (
                  <em className="text-text-muted">No clauses</em>
                )}
                <ul className="list-disc pl-5">
                  {ar.applicable_clauses.map((c) => (
                    <li key={c.fragment_id}>
                      <span className="font-mono text-[11px]">
                        {c.citation_path || `frag#${c.fragment_id}`}
                      </span>{" "}
                      — {c.bylaw_name}
                    </li>
                  ))}
                </ul>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function VerdictBadge({ verdict }: { verdict: string }) {
  const color =
    verdict === "compliant"
      ? "bg-green-100 text-green-900"
      : verdict === "non_compliant"
        ? "bg-red-100 text-red-900"
        : "bg-yellow-100 text-yellow-900";
  return (
    <span
      data-testid={`verdict-badge-${verdict}`}
      className={`rounded px-2 py-0.5 text-[11px] ${color}`}
    >
      {verdict}
    </span>
  );
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function parseOverrideValue(raw: string): unknown {
  // Try numeric → boolean → string.
  const trimmed = raw.trim();
  if (!trimmed) return trimmed;
  if (trimmed === "true") return true;
  if (trimmed === "false") return false;
  const asNumber = Number(trimmed);
  if (!Number.isNaN(asNumber) && trimmed !== "") return asNumber;
  return trimmed;
}
