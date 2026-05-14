// POST /api/chat — proxy to the FastAPI advisor backend at
// $ADVISOR_API_URL (default http://127.0.0.1:8000).
//
// Why a proxy instead of calling /v1/chat from the browser:
//   1. Clerk's session token never reaches the browser as a
//      bearer header — we mint it via auth() server-side and
//      forward it as Authorization: Bearer <jwt>. The client only
//      ever sees a same-origin /api/chat URL.
//   2. Avoids CORS in dev (no Access-Control-Allow-Origin gymnastics
//      between :3000 and :8000).
//   3. Single canonical place to swap backends, log, or add headers.
//
// The backend speaks SSE. We forward the raw byte stream untouched —
// no JSON re-encoding, no event filtering — so the browser sees the
// same event taxonomy the FastAPI app emits (session, message_start,
// content_block_delta, message_stop, plus periodic ping comments).
// That keeps this file dumb and lets the client own all parsing.

import { NextRequest } from "next/server";
import { buildAdvisorAuthHeaders } from "@/lib/advisor-auth";

const ADVISOR_API_URL =
  process.env.ADVISOR_API_URL || "http://127.0.0.1:8000";

export const dynamic = "force-dynamic";
// Node runtime — we need fetch streaming + AbortController which the
// Edge runtime supports too, but Node is the safer default for a
// localhost-only proxy.
export const runtime = "nodejs";

export async function POST(req: NextRequest) {
  const body = (await req.json().catch(() => null)) as Record<
    string,
    unknown
  > | null;
  if (!body || typeof body.message !== "string" || !body.message.trim()) {
    return new Response(JSON.stringify({ error: "Missing message" }), {
      status: 400,
      headers: { "Content-Type": "application/json" },
    });
  }

  const authHeaders = await buildAdvisorAuthHeaders();
  if (authHeaders === null) {
    return new Response(JSON.stringify({ error: "Unauthorized" }), {
      status: 401,
      headers: { "Content-Type": "application/json" },
    });
  }

  const upstream = await fetch(`${ADVISOR_API_URL}/v1/chat`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      ...authHeaders,
    },
    body: JSON.stringify({
      message: body.message,
      session_id: typeof body.session_id === "string" ? body.session_id : null,
      // case_id binds the new session to a previously-opened case
      // (POST /v1/cases). Required for new sessions in the case-credit
      // model; ignored when session_id is provided.
      case_id:
        typeof body.case_id === "number" && Number.isInteger(body.case_id)
          ? body.case_id
          : null,
    }),
    // Disable Next's response cache for this fetch — SSE streams must
    // not be cached.
    cache: "no-store",
  }).catch((e: unknown) => {
    return new Response(
      JSON.stringify({
        error: "Could not reach advisor backend",
        detail: (e as Error).message,
        url: ADVISOR_API_URL,
      }),
      { status: 502, headers: { "Content-Type": "application/json" } },
    );
  });

  if (!upstream.ok) {
    // Pass through non-200 status + body. Useful for surfacing 401
    // (auth misconfig), 429 (quota), 503 (gate disabled).
    const text = await upstream.text();
    return new Response(text, {
      status: upstream.status,
      headers: {
        "Content-Type":
          upstream.headers.get("Content-Type") || "application/json",
      },
    });
  }

  // Stream the upstream body verbatim. Client consumes SSE.
  return new Response(upstream.body, {
    status: 200,
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
    },
  });
}
