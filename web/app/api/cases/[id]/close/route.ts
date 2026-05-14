// POST /api/cases/[id]/close — explicit case close (refunds any
// reserved-but-uncommitted credit).

import { NextRequest } from "next/server";
import { callBackend } from "@/lib/api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function POST(
  _req: NextRequest,
  ctx: { params: Promise<{ id: string }> },
) {
  const { id } = await ctx.params;
  const r = await callBackend(
    `/v1/cases/${encodeURIComponent(id)}/close`,
    { method: "POST" },
  );
  const text = await r.text();
  return new Response(text, {
    status: r.status,
    headers: {
      "Content-Type":
        r.headers.get("Content-Type") || "application/json",
    },
  });
}
