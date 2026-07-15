// GET /api/cases — list user's cases (proxy to GET /v1/cases).
// POST /api/cases — open a new case (proxy to POST /v1/cases).

import { NextRequest } from "next/server";
import { callBackend } from "@/lib/api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET() {
  const r = await callBackend("/v1/cases");
  const text = await r.text();
  return new Response(text, {
    status: r.status,
    headers: {
      "Content-Type":
        r.headers.get("Content-Type") || "application/json",
    },
  });
}

export async function POST(req: NextRequest) {
  const body = (await req.json().catch(() => null)) as Record<
    string,
    unknown
  > | null;
  // ABS-382: opening a case is free — no tier is required. `tier` is
  // no longer validated or forwarded; the backend ignores it anyway.
  if (
    !body ||
    typeof body.anchor_label !== "string" ||
    typeof body.anchor_kind !== "string"
  ) {
    return new Response(
      JSON.stringify({
        error: "anchor_label, anchor_kind required",
      }),
      { status: 400, headers: { "Content-Type": "application/json" } },
    );
  }
  const r = await callBackend("/v1/cases", {
    method: "POST",
    body: {
      anchor_label: body.anchor_label,
      anchor_kind: body.anchor_kind,
    },
  });
  const text = await r.text();
  return new Response(text, {
    status: r.status,
    headers: {
      "Content-Type":
        r.headers.get("Content-Type") || "application/json",
    },
  });
}
