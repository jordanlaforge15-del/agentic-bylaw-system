// POST /api/cases/classify — Layer-2 pre-flight tier classifier
// (proxy to POST /v1/cases/classify). Returns the recommended tier +
// confidence + reasons. The case-open form renders this as a banner.

import { NextRequest } from "next/server";
import { callBackend } from "@/lib/api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function POST(req: NextRequest) {
  const body = (await req.json().catch(() => null)) as Record<
    string,
    unknown
  > | null;
  if (
    !body ||
    typeof body.anchor_label !== "string" ||
    typeof body.anchor_kind !== "string" ||
    typeof body.message !== "string"
  ) {
    return new Response(
      JSON.stringify({
        error: "anchor_label, anchor_kind, message required",
      }),
      { status: 400, headers: { "Content-Type": "application/json" } },
    );
  }
  const r = await callBackend("/v1/cases/classify", {
    method: "POST",
    body: {
      anchor_label: body.anchor_label,
      anchor_kind: body.anchor_kind,
      message: body.message,
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
