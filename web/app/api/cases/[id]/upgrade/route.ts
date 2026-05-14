// POST /api/cases/[id]/upgrade — atomic credit-tier swap.
// Body: { target_tier, trigger? }.

import { NextRequest } from "next/server";
import { callBackend } from "@/lib/api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function POST(
  req: NextRequest,
  ctx: { params: Promise<{ id: string }> },
) {
  const { id } = await ctx.params;
  const body = (await req.json().catch(() => null)) as Record<
    string,
    unknown
  > | null;
  if (!body || typeof body.target_tier !== "string") {
    return new Response(
      JSON.stringify({ error: "target_tier required" }),
      { status: 400, headers: { "Content-Type": "application/json" } },
    );
  }
  const r = await callBackend(
    `/v1/cases/${encodeURIComponent(id)}/upgrade`,
    {
      method: "POST",
      body: {
        target_tier: body.target_tier,
        trigger:
          typeof body.trigger === "string" ? body.trigger : "user_manual",
      },
    },
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
