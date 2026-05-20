// Shared fetch helper for the FastAPI backend proxy routes.
//
// One file because every server-side proxy route does the same dance:
//   1. Read ADVISOR_API_URL from env (default localhost).
//   2. Build auth headers via buildAdvisorAuthHeaders.
//   3. Forward the request and return the upstream response.
//
// Centralising it means a backend-URL or auth-shape change is one
// edit, not N. Used by /api/cases, /api/billing/*, /api/admin/*.

import { buildAdvisorAuthHeaders } from "@/lib/advisor-auth";

export const ADVISOR_API_URL =
  process.env.ADVISOR_API_URL || "http://127.0.0.1:8000";

export type ApiInit = {
  method?: "GET" | "POST" | "PUT" | "DELETE";
  body?: unknown;
  searchParams?: Record<string, string | number | undefined>;
  // When true the auth headers are omitted (used by the public
  // /v1/billing/catalog endpoint, which renders for anonymous
  // pricing-page visitors).
  skipAuth?: boolean;
  // Extra request headers to forward upstream. Useful for proxies
  // that need to pass evidentiary headers (x-forwarded-for,
  // user-agent) onto the FastAPI side so the recorded row reflects
  // the real client rather than the Next.js server's loopback hop.
  // Caller-supplied values override defaults set by callBackend.
  forwardHeaders?: Record<string, string>;
};

export async function callBackend(
  path: string,
  init: ApiInit = {},
): Promise<Response> {
  const url = new URL(path, ADVISOR_API_URL);
  if (init.searchParams) {
    for (const [k, v] of Object.entries(init.searchParams)) {
      if (v === undefined) continue;
      url.searchParams.set(k, String(v));
    }
  }

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "application/json",
  };

  if (!init.skipAuth) {
    const auth = await buildAdvisorAuthHeaders();
    if (auth === null) {
      return new Response(JSON.stringify({ error: "Unauthorized" }), {
        status: 401,
        headers: { "Content-Type": "application/json" },
      });
    }
    Object.assign(headers, auth);
  }

  if (init.forwardHeaders) {
    Object.assign(headers, init.forwardHeaders);
  }

  return fetch(url.toString(), {
    method: init.method ?? "GET",
    headers,
    body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
    cache: "no-store",
  });
}
