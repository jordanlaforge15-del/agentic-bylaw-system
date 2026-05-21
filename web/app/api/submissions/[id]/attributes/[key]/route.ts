// PATCH /api/submissions/[id]/attributes/[key] — manual override.

import { NextRequest } from "next/server";
import { callBackend } from "@/lib/api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function PATCH(
  req: NextRequest,
  ctx: { params: Promise<{ id: string; key: string }> },
) {
  const { id, key } = await ctx.params;
  const body = (await req.json().catch(() => null)) as Record<
    string,
    unknown
  > | null;
  if (!body || typeof body.value === "undefined") {
    return new Response(
      JSON.stringify({ error: "Body must include `value`." }),
      { status: 400, headers: { "Content-Type": "application/json" } },
    );
  }
  // PATCH is implemented as a backend POST internally via the cases /
  // billing pattern, but FastAPI's @router.patch handles it natively.
  // callBackend only supports GET/POST/PUT/DELETE; for PATCH we go
  // direct.
  const { buildAdvisorAuthHeaders } = await import("@/lib/advisor-auth");
  const { ADVISOR_API_URL } = await import("@/lib/api");
  const auth = await buildAdvisorAuthHeaders();
  if (auth === null) {
    return new Response(JSON.stringify({ error: "Unauthorized" }), {
      status: 401,
      headers: { "Content-Type": "application/json" },
    });
  }
  const url = new URL(
    `/v1/submissions/${encodeURIComponent(id)}/attributes/${encodeURIComponent(key)}`,
    ADVISOR_API_URL,
  );
  const r = await fetch(url.toString(), {
    method: "PATCH",
    headers: {
      ...auth,
      "Content-Type": "application/json",
      Accept: "application/json",
    },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  const text = await r.text();
  return new Response(text, {
    status: r.status,
    headers: {
      "Content-Type": r.headers.get("Content-Type") || "application/json",
    },
  });
}
