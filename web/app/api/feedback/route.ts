import { NextRequest } from "next/server";
import { callBackend } from "@/lib/api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function POST(req: NextRequest) {
  const body = (await req.json().catch(() => null)) as Record<
    string,
    unknown
  > | null;
  if (!body || typeof body.category !== "string" || typeof body.body !== "string") {
    return new Response(
      JSON.stringify({ error: "category and body are required" }),
      { status: 400, headers: { "Content-Type": "application/json" } },
    );
  }

  const r = await callBackend("/v1/feedback", {
    method: "POST",
    body: { category: body.category, body: body.body },
  });
  const text = await r.text();
  return new Response(text, {
    status: r.status,
    headers: {
      "Content-Type": r.headers.get("Content-Type") || "application/json",
    },
  });
}
