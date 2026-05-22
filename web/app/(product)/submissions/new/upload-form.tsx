"use client";

// Upload form for /submissions/new.
//
// Two fields: an .ifc file input and a parcel-identifier text input
// (PID or address — backend currently matches on parcel_identifier;
// see ABS-53 plan for the deferred geocode lookup).
//
// On submit, POSTs multipart/form-data to /api/submissions and
// redirects to /submissions/[id] on success. Errors render inline so
// the user can correct without losing form state.

import { useState } from "react";
import { useRouter } from "next/navigation";

export function SubmissionUploadForm() {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [parcelAddress, setParcelAddress] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!file) {
      setError("Pick an .ifc file first.");
      return;
    }
    if (!parcelAddress.trim()) {
      setError("Enter the parcel identifier (PID).");
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("parcel_address", parcelAddress.trim());
      const res = await fetch("/api/submissions", {
        method: "POST",
        body: form,
      });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        const message =
          (body?.detail?.message as string | undefined) ||
          (body?.error as string | undefined) ||
          `Upload failed (HTTP ${res.status}).`;
        setError(message);
        setSubmitting(false);
        return;
      }
      const body = await res.json();
      router.push(`/submissions/${body.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed.");
      setSubmitting(false);
    }
  }

  return (
    <form
      data-testid="submission-upload-form"
      onSubmit={onSubmit}
      className="flex flex-col gap-4"
    >
      <label className="flex flex-col gap-1">
        <span className="text-[13px] font-medium">IFC file</span>
        <input
          data-testid="submission-file-input"
          type="file"
          accept=".ifc"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          className="rounded border border-hair p-2 text-[14px]"
          required
        />
        <span className="text-[12px] text-text-muted">
          Export from Revit / ArchiCAD / Vectorworks as IFC4. .rvt
          direct upload is a separate follow-up.
        </span>
      </label>

      <label className="flex flex-col gap-1">
        <span className="text-[13px] font-medium">Parcel identifier</span>
        <input
          data-testid="submission-parcel-input"
          type="text"
          value={parcelAddress}
          onChange={(e) => setParcelAddress(e.target.value)}
          placeholder="e.g. 00012345 (Halifax PID)"
          className="rounded border border-hair p-2 text-[14px]"
          required
        />
        <span className="text-[12px] text-text-muted">
          Address-to-parcel geocoding is a follow-up; for now, paste
          the parcel ID directly.
        </span>
      </label>

      {error && (
        <div
          data-testid="submission-upload-error"
          role="alert"
          className="rounded border border-red-500 bg-red-50 p-3 text-[13px] text-red-900"
        >
          {error}
        </div>
      )}

      <button
        data-testid="submission-upload-submit"
        type="submit"
        disabled={submitting}
        className="self-start rounded bg-black px-4 py-2 text-[14px] text-white disabled:opacity-50"
      >
        {submitting ? "Uploading + extracting…" : "Upload + extract"}
      </button>
    </form>
  );
}
