// POST /api/feedback — submit general (non-message-specific) feedback.
// Proxies to POST /v1/feedback on the FastAPI backend; auth is added
// server-side via buildAdvisorAuthHeaders.

import { NextRequest, NextResponse } from "next/server";
import { buildAdvisorAuthHeaders } from "@/lib/advisor-auth";

const ADVISOR_API_URL =
  process.env.ADVISOR_API_URL || "http://127.0.0.1:8000";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function POST(req: NextRequest) {
  const authHeaders = await buildAdvisorAuthHeaders();
  if (authHeaders === null) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${ADVISOR_API_URL}/v1/feedback`, {
      method: "POST",
      headers: {
        ...authHeaders,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
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

  const responseBody = await upstream.text();
  return new Response(responseBody, {
    status: upstream.status,
    headers: {
      "Content-Type":
        upstream.headers.get("Content-Type") || "application/json",
    },
  });
}
