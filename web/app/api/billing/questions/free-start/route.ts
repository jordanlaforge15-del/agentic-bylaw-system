// POST /api/billing/questions/free-start — proxy to
// POST /v1/billing/questions/free-start.
//
// Payments-off path (ABS-322), decoupled by ABS-324: the case-open form
// calls this to start a free-trial answer. The backend consumes one
// free-question entitlement and opens an Answers `QuestionPurchase` (NOT a
// Case — no CaseCredit interaction), returning the `purchase_id` so the
// browser navigates to the dedicated answer view (/app/answers/{id}).
//
// Returns 402 when the user's free-question counter is already 0.

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
      {
        status: 400,
        headers: { "Content-Type": "application/json" },
      },
    );
  }
  const r = await callBackend("/v1/billing/questions/free-start", {
    method: "POST",
    body,
  });
  const text = await r.text();
  return new Response(text, {
    status: r.status,
    headers: {
      "Content-Type": r.headers.get("Content-Type") || "application/json",
    },
  });
}
