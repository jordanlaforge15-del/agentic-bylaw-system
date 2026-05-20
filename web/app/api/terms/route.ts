// GET /api/terms — proxy to the advisor's GET /v1/terms/current.
//
// Returns { version, body, accepted } for the signed-in user. The
// /app server-side gate uses ``accepted`` to decide whether to
// redirect to /app/terms; the /app/terms client page uses ``body``
// and ``version`` to render the document and post the acceptance.
//
// Why a proxy and not a direct call from the page: the same reason
// /api/chat is a proxy — the Clerk session JWT is minted server-side
// via auth() and forwarded as Authorization: Bearer; the browser
// never sees it. Same-origin /api/terms also dodges CORS.

import { callBackend } from "@/lib/api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET() {
  const r = await callBackend("/v1/terms/current");
  const text = await r.text();
  return new Response(text, {
    status: r.status,
    headers: {
      "Content-Type":
        r.headers.get("Content-Type") || "application/json",
    },
  });
}
