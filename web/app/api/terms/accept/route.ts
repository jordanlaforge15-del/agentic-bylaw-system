// POST /api/terms/accept — proxy to the advisor's POST /v1/terms/accept.
//
// Forwards two evidentiary headers the upstream router persists onto
// the acceptance row:
//   * x-forwarded-for — the real client IP captured by the edge.
//     Without this the FastAPI side would see the Next.js server's
//     loopback address.
//   * user-agent — passed through so the recorded row reflects the
//     submitting browser/client, not the Next.js proxy's UA.
//
// Body is the version hash the client read. The upstream cross-checks
// it against the live hash and rejects stale-hash accepts with 409
// (we surface that status to the caller untouched).

import { NextRequest } from "next/server";
import { callBackend } from "@/lib/api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function POST(req: NextRequest) {
  const body = (await req.json().catch(() => null)) as Record<
    string,
    unknown
  > | null;
  if (
    !body ||
    typeof body.version !== "string" ||
    body.version.length !== 64
  ) {
    return new Response(
      JSON.stringify({
        error: "version required (sha256 hex)",
      }),
      { status: 400, headers: { "Content-Type": "application/json" } },
    );
  }

  // Forward the real client IP + UA so the acceptance row carries
  // evidence about the submitting client, not the proxy itself.
  const xff =
    req.headers.get("x-forwarded-for") ||
    req.headers.get("x-real-ip") ||
    "";
  const ua = req.headers.get("user-agent") || "";

  const r = await callBackend("/v1/terms/accept", {
    method: "POST",
    body: { version: body.version },
    forwardHeaders: {
      ...(xff ? { "x-forwarded-for": xff } : {}),
      ...(ua ? { "user-agent": ua } : {}),
    },
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
