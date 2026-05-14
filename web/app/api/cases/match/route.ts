// GET /api/cases/match?anchor_label=...&anchor_kind=... — pre-flight
// "do you already have an in-window case for this anchor?" check.

import { NextRequest } from "next/server";
import { callBackend } from "@/lib/api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(req: NextRequest) {
  const anchor_label = req.nextUrl.searchParams.get("anchor_label") ?? "";
  const anchor_kind = req.nextUrl.searchParams.get("anchor_kind") ?? "";
  if (!anchor_label || !anchor_kind) {
    return new Response(
      JSON.stringify({ error: "anchor_label and anchor_kind required" }),
      { status: 400, headers: { "Content-Type": "application/json" } },
    );
  }
  const r = await callBackend("/v1/cases/match", {
    searchParams: { anchor_label, anchor_kind },
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
