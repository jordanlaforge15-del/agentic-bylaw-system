// POST /api/billing/questions/intake — proxy to POST /v1/billing/questions/intake.
//
// The consultant-style intake step that runs BEFORE checkout (ABS-315):
// given a selected catalog question + the user's free-form description so
// far + any inputs already collected, the backend LLM extracts what it can
// and tells us whether the question's required inputs are complete. Always
// FREE — no Stripe object, no charge — but auth-gated so it can't be
// scraped anonymously.

import { NextRequest } from "next/server";
import { callBackend } from "@/lib/api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function POST(req: NextRequest) {
  const body = (await req.json().catch(() => null)) as Record<
    string,
    unknown
  > | null;
  if (!body || typeof body.question_slug !== "string") {
    return new Response(
      JSON.stringify({ error: "question_slug required" }),
      { status: 400, headers: { "Content-Type": "application/json" } },
    );
  }
  const r = await callBackend("/v1/billing/questions/intake", {
    method: "POST",
    body: {
      question_slug: body.question_slug,
      conversation:
        typeof body.conversation === "string" ? body.conversation : "",
      inputs:
        body.inputs && typeof body.inputs === "object" ? body.inputs : {},
    },
  });
  const text = await r.text();
  return new Response(text, {
    status: r.status,
    headers: {
      "Content-Type": r.headers.get("Content-Type") || "application/json",
    },
  });
}
