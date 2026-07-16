// POST /api/billing/questions/quote — proxy to POST /v1/billing/questions/quote.
//
// The off-menu "Other" path (ABS-316): the user types a free-form question
// and the backend LLM classifies its difficulty and returns an anchored
// price. Producing the quote is ALWAYS FREE (no charge, no Stripe object)
// but auth-gated so it can't be scraped anonymously. Buying the answer is
// a separate step (POST /api/billing/checkout/other).

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
  const r = await callBackend("/v1/billing/questions/quote", {
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
