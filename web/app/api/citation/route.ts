// GET /api/citation?citation_path=...&document_id=...
// Proxy to advisor GET /v1/citation — fetches clause text + ancestor chain
// for a single citation path. Used by the "Cited this thread" drawer.

import { NextResponse } from "next/server";
import { buildAdvisorAuthHeaders } from "@/lib/advisor-auth";

const ADVISOR_API_URL =
  process.env.ADVISOR_API_URL || "http://127.0.0.1:8000";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const citationPath = searchParams.get("citation_path");
  if (!citationPath) {
    return NextResponse.json(
      { error: "citation_path is required" },
      { status: 400 },
    );
  }

  const authHeaders = await buildAdvisorAuthHeaders();
  if (authHeaders === null) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const upstream_url = new URL(`${ADVISOR_API_URL}/v1/citation`);
  upstream_url.searchParams.set("citation_path", citationPath);
  const documentId = searchParams.get("document_id");
  if (documentId) upstream_url.searchParams.set("document_id", documentId);

  let upstream: Response;
  try {
    upstream = await fetch(upstream_url.toString(), {
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
