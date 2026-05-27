// Print preview for a bylaw reading session. Opens in a new tab via
// the "Export reading (PDF)" button in the parcel pane; auto-triggers
// window.print() once the session data has loaded so the user lands
// directly in the browser's print dialog.
//
// URL shape: /app/print?session_id=<uuid>

"use client";

import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import {
  extractParcelContext,
  type BackendMessage,
  type ParcelContext,
} from "@/lib/parcel";

type ReadMsg = { role: "user" | "agent"; text: string };

function extractReadableMessages(msgs: BackendMessage[]): ReadMsg[] {
  const out: ReadMsg[] = [];
  for (const m of msgs) {
    if (m.role === "user") {
      if (typeof m.content === "string" && m.content.trim()) {
        out.push({ role: "user", text: m.content.trim() });
      }
    } else {
      const text =
        typeof m.content === "string"
          ? m.content
          : m.content
              .filter(
                (b): b is { type: "text"; text: string } => b.type === "text",
              )
              .map((b) => b.text)
              .join("");
      if (text.trim()) {
        out.push({ role: "agent", text: text.trim() });
      }
    }
  }
  return out;
}

function PrintContent() {
  const searchParams = useSearchParams();
  const sessionId = searchParams.get("session_id");

  const [parcel, setParcel] = useState<ParcelContext | null>(null);
  const [messages, setMessages] = useState<ReadMsg[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!sessionId) {
      setError("No session_id specified.");
      setLoading(false);
      return;
    }
    fetch(`/api/chat/sessions/${encodeURIComponent(sessionId)}`, {
      cache: "no-store",
    })
      .then(async (res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json() as Promise<{ messages: BackendMessage[] }>;
      })
      .then((data) => {
        setParcel(extractParcelContext(data.messages));
        setMessages(extractReadableMessages(data.messages));
      })
      .catch((e: unknown) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [sessionId]);

  // Auto-trigger the print dialog once content is ready.
  useEffect(() => {
    if (loading || error) return;
    const t = setTimeout(() => window.print(), 400);
    return () => clearTimeout(t);
  }, [loading, error]);

  const exportDate = new Date().toLocaleDateString("en-CA", {
    year: "numeric",
    month: "long",
    day: "numeric",
  });

  if (loading) {
    return (
      <div style={{ padding: 48, fontFamily: "Georgia, serif" }}>
        Loading reading…
      </div>
    );
  }

  if (error) {
    return (
      <div
        style={{ padding: 48, fontFamily: "Georgia, serif", color: "#c00" }}
      >
        Could not load reading: {error}
      </div>
    );
  }

  return (
    <>
      <style>{`
        @media print {
          @page { margin: 20mm 25mm; }
          .no-print { display: none !important; }
        }
        body {
          margin: 0;
          background: #fff;
          color: #111;
          font-family: Georgia, "Times New Roman", serif;
        }
        .pr-root { max-width: 720px; margin: 0 auto; padding: 40px 24px; }
        .pr-header {
          display: flex;
          justify-content: space-between;
          align-items: baseline;
          margin-bottom: 32px;
          border-bottom: 2px solid #111;
          padding-bottom: 12px;
        }
        .pr-brand {
          font-family: system-ui, sans-serif;
          font-weight: 700;
          font-size: 15px;
          letter-spacing: -0.01em;
        }
        .pr-date {
          font-family: system-ui, sans-serif;
          font-size: 11px;
          color: #666;
        }
        .pr-section-label {
          font-family: system-ui, sans-serif;
          font-size: 10.5px;
          letter-spacing: 0.07em;
          text-transform: uppercase;
          color: #666;
          margin: 28px 0 10px;
          font-weight: 600;
        }
        .pr-address {
          font-family: system-ui, sans-serif;
          font-size: 20px;
          font-weight: 700;
          letter-spacing: -0.02em;
          margin: 0 0 14px;
        }
        .pr-address-sub {
          font-weight: 400;
          font-size: 14px;
        }
        .pr-table {
          width: 100%;
          border-collapse: collapse;
          font-size: 13px;
          margin-bottom: 8px;
        }
        .pr-table th {
          text-align: left;
          font-family: monospace;
          font-size: 11px;
          letter-spacing: 0.04em;
          color: #666;
          padding: 5px 12px 5px 0;
          font-weight: normal;
          width: 40%;
        }
        .pr-table td {
          font-weight: 600;
          font-family: system-ui, sans-serif;
          padding: 5px 0;
          border-bottom: 1px dotted #ccc;
        }
        .pr-msg { margin-bottom: 22px; }
        .pr-msg-label {
          font-family: system-ui, sans-serif;
          font-size: 10px;
          letter-spacing: 0.06em;
          text-transform: uppercase;
          color: #888;
          margin-bottom: 5px;
        }
        .pr-msg--user .pr-msg-body {
          font-size: 14px;
          font-family: system-ui, sans-serif;
          font-weight: 600;
        }
        .pr-msg--agent .pr-msg-body {
          font-size: 13.5px;
          line-height: 1.65;
          white-space: pre-wrap;
        }
        .pr-citation {
          font-size: 12.5px;
          border-left: 3px solid #ddd;
          padding: 4px 10px;
          margin-bottom: 6px;
          font-family: system-ui, sans-serif;
        }
        .pr-citation-path { font-weight: 600; }
        .pr-citation-title { color: #666; }
        .pr-footer {
          margin-top: 44px;
          padding-top: 12px;
          border-top: 1px solid #ccc;
          font-family: system-ui, sans-serif;
          font-size: 10px;
          color: #888;
        }
      `}</style>
      <div className="pr-root">
        <div className="pr-header">
          <div className="pr-brand">Halifax Bylaw Advisor</div>
          <div className="pr-date">Exported {exportDate}</div>
        </div>

        {parcel && (
          <>
            <div className="pr-section-label">Parcel</div>
            <div className="pr-address">
              {parcel.address.civic_number} {parcel.address.street}
              <span className="pr-address-sub">, Halifax Regional Municipality</span>
            </div>
            <table className="pr-table">
              <tbody>
                {parcel.zone && (
                  <tr>
                    <th>Zone</th>
                    <td>
                      {parcel.zone.code}
                      {parcel.zone.description
                        ? ` · ${parcel.zone.description}`
                        : ""}
                    </td>
                  </tr>
                )}
                {parcel.height?.max_m != null && (
                  <tr>
                    <th>Max Height</th>
                    <td>{parcel.height.max_m} m</td>
                  </tr>
                )}
                {parcel.heritage && (
                  <tr>
                    <th>Heritage</th>
                    <td>
                      {parcel.heritage.name}
                      {parcel.heritage.status
                        ? ` (${parcel.heritage.status})`
                        : ""}
                    </td>
                  </tr>
                )}
                {parcel.far?.max != null && (
                  <tr>
                    <th>Max FAR</th>
                    <td>{parcel.far.max}</td>
                  </tr>
                )}
                {parcel.bonus && (
                  <tr>
                    <th>Bonus Zoning</th>
                    <td>{parcel.bonus.name}</td>
                  </tr>
                )}
                {parcel.shadow && (
                  <tr>
                    <th>Shadow Impact</th>
                    <td>{parcel.shadow.area}</td>
                  </tr>
                )}
              </tbody>
            </table>
          </>
        )}

        <div className="pr-section-label">Transcript</div>
        {messages.length === 0 && (
          <p
            style={{
              color: "#888",
              fontStyle: "italic",
              fontSize: 13,
              margin: 0,
            }}
          >
            No conversation content found.
          </p>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`pr-msg pr-msg--${m.role}`}>
            <div className="pr-msg-label">
              {m.role === "user" ? "Question" : "Analysis"}
            </div>
            <div className="pr-msg-body">{m.text}</div>
          </div>
        ))}

        {parcel && parcel.cited.length > 0 && (
          <>
            <div className="pr-section-label">Citations</div>
            {parcel.cited.map((c, i) => (
              <div key={i} className="pr-citation">
                <span className="pr-citation-path">{c.citation}</span>
                {c.title && (
                  <span className="pr-citation-title"> · {c.title}</span>
                )}
              </div>
            ))}
          </>
        )}

        <div className="pr-footer">
          For planning guidance only. Not legal advice.
          Generated by Halifax Bylaw Advisor.
        </div>
      </div>
    </>
  );
}

export default function PrintReadingPage() {
  return (
    <Suspense fallback={<div style={{ padding: 48 }}>Loading…</div>}>
      <PrintContent />
    </Suspense>
  );
}
