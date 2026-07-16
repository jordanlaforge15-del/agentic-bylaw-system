// GET /api/billing/questions/purchases/[id] — proxy to
// GET /v1/billing/questions/purchases/{id}. Returns the priced-question
// purchase + its (raw) answer for the post-purchase answer view
// (ABS-321). Auth-required; ownership is enforced upstream.

import { NextRequest } from "next/server";
import { callBackend } from "@/lib/api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  const upstream = await callBackend(
    `/v1/billing/questions/purchases/${encodeURIComponent(id)}`,
  );
  const text = await upstream.text();
  return new Response(text, {
    status: upstream.status,
    headers: {
      "Content-Type":
        upstream.headers.get("Content-Type") || "application/json",
      "Cache-Control": "no-store",
    },
  });
}
