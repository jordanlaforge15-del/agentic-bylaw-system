// GET /api/chat/sessions — list the demo user's chat sessions.
// Same proxy pattern as /api/chat: Next adds the X-Test-User-Id
// header server-side so the browser never sees backend auth or URL.

import { NextResponse } from "next/server";

const ADVISOR_API_URL =
  process.env.ADVISOR_API_URL || "http://127.0.0.1:8000";
const DEMO_USER_ID = process.env.ADVISOR_DEMO_USER_ID || "demo-user-1";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET() {
  let upstream: Response;
  try {
    upstream = await fetch(`${ADVISOR_API_URL}/v1/chat/sessions`, {
      method: "GET",
      headers: { "X-Test-User-Id": DEMO_USER_ID },
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
