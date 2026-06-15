// POST /api/billing/checkout/other — proxy to POST /v1/billing/checkout/other.
// Body: { question }. The backend re-quotes the question server-side, places
// a manual-capture authorization for the quoted amount, and returns a
// hosted-checkout URL the browser redirects to (ABS-316).

import { NextRequest } from "next/server";
import { callBackend } from "@/lib/api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function POST(req: NextRequest) {
  const body = (await req.json().catch(() => null)) as Record<
    string,
    unknown
  > | null;
  if (!body || typeof body.question !== "string" || !body.question.trim()) {
    return new Response(
      JSON.stringify({ error: "question required" }),
      { status: 400, headers: { "Content-Type": "application/json" } },
    );
  }
  const r = await callBackend("/v1/billing/checkout/other", {
    method: "POST",
    body: { question: body.question },
  });
  const text = await r.text();
  return new Response(text, {
    status: r.status,
    headers: {
      "Content-Type": r.headers.get("Content-Type") || "application/json",
    },
  });
}
