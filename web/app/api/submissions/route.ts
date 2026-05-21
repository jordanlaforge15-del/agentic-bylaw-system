// POST /api/submissions — multipart .ifc upload, proxied to FastAPI.
//
// Custom proxy (rather than `callBackend`) because the upstream call
// is multipart/form-data, not JSON. We read the incoming request body
// as a FormData, repackage it for the upstream fetch with the same
// boundary, and stream the response back.

import { NextRequest } from "next/server";
import { buildAdvisorAuthHeaders } from "@/lib/advisor-auth";
import { ADVISOR_API_URL } from "@/lib/api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
// Disable the App Router's default body-parse-and-buffer; we want
// the raw multipart body so the upstream parser handles file streaming.
export const maxDuration = 300;

export async function POST(req: NextRequest) {
  const auth = await buildAdvisorAuthHeaders();
  if (auth === null) {
    return new Response(JSON.stringify({ error: "Unauthorized" }), {
      status: 401,
      headers: { "Content-Type": "application/json" },
    });
  }

  // Re-build a FormData from the incoming request. Next 16 supports
  // `req.formData()` natively; this preserves both the file and the
  // string form fields.
  let form: FormData;
  try {
    form = await req.formData();
  } catch (err) {
    return new Response(
      JSON.stringify({ error: "Could not parse upload form data" }),
      { status: 400, headers: { "Content-Type": "application/json" } },
    );
  }

  const upstreamUrl = new URL("/v1/submissions", ADVISOR_API_URL);
  const upstream = await fetch(upstreamUrl.toString(), {
    method: "POST",
    headers: {
      // Don't set Content-Type — `fetch` derives the correct
      // multipart/form-data boundary from the FormData object itself.
      ...auth,
      Accept: "application/json",
    },
    body: form,
    cache: "no-store",
  });

  const text = await upstream.text();
  return new Response(text, {
    status: upstream.status,
    headers: {
      "Content-Type":
        upstream.headers.get("Content-Type") || "application/json",
    },
  });
}
