// ABS-413: explicit retrieval_enabled publish flag — the retrieval corpus is
// exactly the set of operator-enabled documents; nothing is derived from
// ingestion recency.
//
// What this proves, end-to-end through HTTP + Postgres against the REAL
// publish surface (layer1.pipeline.publish.set_retrieval_enabled — the same
// function behind the CLI's enable-retrieval / disable-retrieval) and the
// REAL production scope (retrieval_enabled_resolver, the resolver the
// advisor app and MCP server wire into RetrievalService):
//
// 1. Freshly seeded documents default to DISABLED and are invisible to a
//    production-scoped search — opt-in publishing fails closed.
// 2. Enabling one document surfaces exactly that document's evidence.
// 3. Enabling a second version of the same bylaw without replace warns about
//    the enabled sibling and leaves both searchable; with replace=true the
//    older version is disabled in the same transaction and search follows
//    the newly published version only.
// 4. Disabling the published document empties the scope again.
//
// The spec drives POST /v1/_test/retrieval-flag (seed / set / status) and
// POST /v1/_test/search-enabled-scope on the e2e FastAPI server.

import { E2E_API_URL, expect, test } from "../fixtures/test-env";

const FLAG_URL = `${E2E_API_URL}/v1/_test/retrieval-flag`;
const SEARCH_URL = `${E2E_API_URL}/v1/_test/search-enabled-scope`;

const MUNICIPALITY = "Test Municipality ABS-413";
// The same spec file runs concurrently in every Playwright project
// (desktop-chrome, mobile-*) against one shared Postgres, and `seed`
// delete-then-recreates its bylaw's documents. Partition the bylaw per
// project+worker so a parallel run can't invalidate this run's ids
// mid-lifecycle.
function bylawName(): string {
  return `ABS-413 Retrieval Flag By-law [${test.info().project.name}-w${test.info().workerIndex}]`;
}
// Matches the sentinel fragments the seed action creates; obscure enough
// that no other seeded bylaw in the shared e2e corpus matches it. Parallel
// partitions share the sentinel text, but every assertion below is on this
// partition's own document ids, so cross-partition matches are inert.
const QUERY = "pergola trellis height limit versioned publish";

type SetResponse = {
  changes: { document_id: number; enabled: boolean; reason: string }[];
  warnings: string[];
  relinked: number;
};

type StatusResponse = {
  documents: { id: number; retrieval_enabled: boolean }[];
};

async function seedDocs(request: any, count = 3): Promise<number[]> {
  const res = await request.post(FLAG_URL, {
    headers: { "Content-Type": "application/json" },
    data: {
      action: "seed",
      municipality: MUNICIPALITY,
      bylaw_name: bylawName(),
      doc_count: count,
    },
  });
  expect(res.status()).toBe(200);
  const body = (await res.json()) as { document_ids: number[] };
  expect(body.document_ids).toHaveLength(count);
  return body.document_ids;
}

async function setFlag(
  request: any,
  documentIds: number[],
  enabled: boolean,
  replace = false,
): Promise<SetResponse> {
  const res = await request.post(FLAG_URL, {
    headers: { "Content-Type": "application/json" },
    data: {
      action: "set",
      document_ids: documentIds,
      enabled,
      replace,
    },
  });
  expect(res.status()).toBe(200);
  return (await res.json()) as SetResponse;
}

async function searchScopedDocIds(request: any): Promise<Set<number>> {
  const res = await request.post(SEARCH_URL, {
    headers: { "Content-Type": "application/json" },
    data: { query: QUERY, limit: 20 },
  });
  expect(res.status()).toBe(200);
  const body = (await res.json()) as {
    matches: { document_id: number }[];
  };
  return new Set(body.matches.map((m) => m.document_id));
}

test("full publish lifecycle: fail-closed seed → enable → replace → disable", async ({
  request,
}) => {
  const [v1, v2, v3] = await seedDocs(request, 3);

  // 1. Opt-in default: nothing seeded here is searchable yet.
  let hits = await searchScopedDocIds(request);
  expect(hits.has(v1)).toBe(false);
  expect(hits.has(v2)).toBe(false);
  expect(hits.has(v3)).toBe(false);

  // 2. Publish v1 — its sentinel becomes visible, siblings stay dark.
  const enableV1 = await setFlag(request, [v1], true);
  expect(enableV1.changes).toEqual([
    { document_id: v1, enabled: true, reason: "requested" },
  ]);
  hits = await searchScopedDocIds(request);
  expect(hits.has(v1)).toBe(true);
  expect(hits.has(v2)).toBe(false);

  // 3a. Publishing v2 WITHOUT replace warns about the enabled sibling and
  //     leaves both versions searchable (representable, but warned).
  const enableV2 = await setFlag(request, [v2], true);
  expect(enableV2.warnings.join(" ")).toContain("other enabled version");
  hits = await searchScopedDocIds(request);
  expect(hits.has(v1)).toBe(true);
  expect(hits.has(v2)).toBe(true);

  // 3b. Publishing v3 WITH replace atomically retires v1 and v2.
  const enableV3 = await setFlag(request, [v3], true, true);
  const reasons = new Map(
    enableV3.changes.map((c) => [c.document_id, c.reason]),
  );
  expect(reasons.get(v3)).toBe("requested");
  expect(reasons.get(v1)).toBe("replaced-sibling");
  expect(reasons.get(v2)).toBe("replaced-sibling");
  hits = await searchScopedDocIds(request);
  expect(hits.has(v1)).toBe(false);
  expect(hits.has(v2)).toBe(false);
  expect(hits.has(v3)).toBe(true);

  // Status reflects the flags exactly.
  const statusRes = await request.post(FLAG_URL, {
    headers: { "Content-Type": "application/json" },
    data: { action: "status", municipality: MUNICIPALITY, bylaw_name: bylawName() },
  });
  expect(statusRes.status()).toBe(200);
  const status = (await statusRes.json()) as StatusResponse;
  const flags = new Map(status.documents.map((d) => [d.id, d.retrieval_enabled]));
  expect(flags.get(v1)).toBe(false);
  expect(flags.get(v2)).toBe(false);
  expect(flags.get(v3)).toBe(true);

  // 4. Unpublish v3 — the bylaw's evidence disappears from scope again
  //    (fail-closed, not fall-back-to-latest).
  const disableV3 = await setFlag(request, [v3], false);
  expect(disableV3.changes).toEqual([
    { document_id: v3, enabled: false, reason: "requested" },
  ]);
  hits = await searchScopedDocIds(request);
  expect(hits.has(v1)).toBe(false);
  expect(hits.has(v2)).toBe(false);
  expect(hits.has(v3)).toBe(false);
});

test("set action rejects unknown document ids without partial writes", async ({
  request,
}) => {
  const [v1] = await seedDocs(request, 1);
  const res = await request.post(FLAG_URL, {
    headers: { "Content-Type": "application/json" },
    data: { action: "set", document_ids: [v1, 99999999], enabled: true },
  });
  expect(res.status()).toBe(400);

  // The valid id in the batch must NOT have been enabled.
  const statusRes = await request.post(FLAG_URL, {
    headers: { "Content-Type": "application/json" },
    data: { action: "status", municipality: MUNICIPALITY, bylaw_name: bylawName() },
  });
  const status = (await statusRes.json()) as StatusResponse;
  const flags = new Map(status.documents.map((d) => [d.id, d.retrieval_enabled]));
  expect(flags.get(v1)).toBe(false);
});
