// POST /api/billing/checkout/question — proxy to POST /v1/billing/checkout/question.
// Body: { question_slug }. Returns a Checkout URL the browser redirects to.

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
  const r = await callBackend("/v1/billing/checkout/question", {
    method: "POST",
    body: { question_slug: body.question_slug },
  });
  const text = await r.text();
  return new Response(text, {
    status: r.status,
    headers: {
      "Content-Type": r.headers.get("Content-Type") || "application/json",
    },
  });
}
