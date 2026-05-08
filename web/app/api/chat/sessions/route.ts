// GET /api/chat/sessions — list the signed-in user's chat sessions.
// Same proxy pattern as /api/chat: auth is added server-side so the
// browser never sees backend credentials. See lib/advisor-auth for
// the Clerk → Bearer-JWT and dev fallback selection.

import { NextResponse } from "next/server";
import { buildAdvisorAuthHeaders } from "@/lib/advisor-auth";

const ADVISOR_API_URL =
  process.env.ADVISOR_API_URL || "http://127.0.0.1:8000";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET() {
  const authHeaders = await buildAdvisorAuthHeaders();
  if (authHeaders === null) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${ADVISOR_API_URL}/v1/chat/sessions`, {
      method: "GET",
      headers: authHeaders,
      cache: "no-store",
    });
  } catch (e) {
    return NextResponse.json(
      {
        error: "Could not reach advisor backend",
        detail: (e as Error).message,
      },
      { status: 502 },
    );
  }

  const body = await upstream.text();
  return new Response(body, {
    status: upstream.status,
    headers: {
      "Content-Type":
        upstream.headers.get("Content-Type") || "application/json",
    },
  });
}
