// ABS-75: Discovery Agent end-to-end through the real FastAPI stack.
//
// What this proves:
//
// - DiscoveryAgent's classifier → parent-resolver → SourceDocument assembly
//   path is reachable through the real Next.js ↔ FastAPI stack (so a proxy
//   misconfig or stale image regressing the abs-learning import would
//   trip e2e, not just unit tests).
// - The output is shaped correctly for run_learning_pipeline to consume:
//   the primary bylaw is identifiable (document_type=bylaw,
//   parent_document=null, in_scope=true), a companion bylaw is listed,
//   and non-bylaw clutter (news posts, agendas) is dropped by the
//   classifier confidence floor.
// - The companions_from_sources helper surfaces in-scope non-primary
//   bylaws + policies — the signal SemanticMapper now uses to populate
//   TaxonomyMap.companion_bylaws_required.
//
// The spec exercises the test-only POST /v1/_test/discover endpoint on
// the e2e FastAPI server. The endpoint accepts pre-crawled candidates +
// canned LLM tool-use payloads, so the spec stays hermetic — no live
// municipal site, no real Anthropic call. The crawler half is covered
// by abs-learning/tests/test_discovery.py against an httpx.MockTransport.

import { E2E_API_URL, expect, test } from "../fixtures/test-env";

type DiscoveredSource = {
  document_name: string;
  document_type: "bylaw" | "schedule" | "appendix" | "map" | "amendment" | "policy";
  document_role: string;
  parent_document: string | null;
  source_url: string;
  format: "pdf" | "html" | "xml" | "docx";
  access_method: "direct_download" | "paginated_html" | "portal" | "api";
  page_count_estimate: number | null;
  last_amended: string | null;
  amendment_series: string | null;
  in_scope: boolean;
};

type DiscoveryResponse = {
  ok: boolean;
  sources: DiscoveredSource[];
  companions: string[];
  llm_call_count: number;
};

test("discovery endpoint assembles a manifest-ready source list from canned classifier output", async ({
  request,
}) => {
  // A small synthetic municipal site: primary land-use bylaw, subdivision
  // bylaw, heritage policy, zoning-map schedule, archival amendment, plus
  // a news page the classifier should drop entirely.
  const SEED = "https://sampleton.example.com/bylaws";
  const candidates = [
    {
      url: "https://sampleton.example.com/bylaws/lub.pdf",
      link_text: "Land Use By-Law",
      discovered_on: SEED,
      is_pdf: true,
      content_type: "application/pdf",
    },
    {
      url: "https://sampleton.example.com/bylaws/subdivision.pdf",
      link_text: "Subdivision By-Law",
      discovered_on: SEED,
      is_pdf: true,
      content_type: "application/pdf",
    },
    {
      url: "https://sampleton.example.com/bylaws/heritage-policy.pdf",
      link_text: "Heritage Design Guidelines",
      discovered_on: SEED,
      is_pdf: true,
      content_type: "application/pdf",
    },
    {
      url: "https://sampleton.example.com/bylaws/zoning-map.pdf",
      link_text: "Zoning Map (Schedule A)",
      discovered_on: SEED,
      is_pdf: true,
      content_type: "application/pdf",
    },
    {
      url: "https://sampleton.example.com/bylaws/amend-2024-03.pdf",
      link_text: "Bylaw No. 2024-03 — Heritage Amendment",
      discovered_on: SEED,
      is_pdf: true,
      content_type: "application/pdf",
    },
    {
      url: "https://sampleton.example.com/news/road-closure-2024",
      link_text: "Road closure notice",
      discovered_on: SEED,
      is_pdf: false,
      content_type: "text/html",
    },
  ];

  // Single batch → single classifier response. The news page is
  // intentionally dropped (no entry) — the classifier's job.
  const classifierResponse = [
    {
      url: "https://sampleton.example.com/bylaws/lub.pdf",
      document_name: "Land Use By-Law",
      document_type: "bylaw",
      document_role: "Primary land-use bylaw",
      format: "pdf",
      access_method: "direct_download",
      in_scope: true,
      is_primary_bylaw: true,
      confidence: 0.95,
    },
    {
      url: "https://sampleton.example.com/bylaws/subdivision.pdf",
      document_name: "Subdivision By-Law",
      document_type: "bylaw",
      document_role: "Subdivision bylaw",
      format: "pdf",
      access_method: "direct_download",
      in_scope: true,
      is_primary_bylaw: false,
      confidence: 0.9,
    },
    {
      url: "https://sampleton.example.com/bylaws/heritage-policy.pdf",
      document_name: "Heritage Design Guidelines",
      document_type: "policy",
      document_role: "Heritage design policy",
      format: "pdf",
      access_method: "direct_download",
      in_scope: true,
      confidence: 0.85,
    },
    {
      url: "https://sampleton.example.com/bylaws/zoning-map.pdf",
      document_name: "Zoning Map Schedule A",
      document_type: "schedule",
      document_role: "Zoning map schedule",
      format: "pdf",
      access_method: "direct_download",
      in_scope: true,
      confidence: 0.9,
    },
    {
      url: "https://sampleton.example.com/bylaws/amend-2024-03.pdf",
      document_name: "Bylaw 2024-03 Heritage Amendment",
      document_type: "amendment",
      document_role: "Heritage amendment",
      format: "pdf",
      access_method: "direct_download",
      in_scope: false,
      amendment_series: "Bylaw No. 2024-03",
      confidence: 0.85,
    },
  ];

  const parentLinks = [
    { document_name: "Land Use By-Law", parent_document: null },
    { document_name: "Subdivision By-Law", parent_document: null },
    {
      document_name: "Heritage Design Guidelines",
      parent_document: null,
    },
    {
      document_name: "Zoning Map Schedule A",
      parent_document: "Land Use By-Law",
    },
    {
      document_name: "Bylaw 2024-03 Heritage Amendment",
      parent_document: "Land Use By-Law",
    },
  ];

  const response = await request.post(`${E2E_API_URL}/v1/_test/discover`, {
    headers: { "Content-Type": "application/json" },
    data: {
      municipality: {
        name: "Town of Sampleton",
        jurisdiction_code: "sampleton",
        province: "Nova Scotia",
        governing_body: "Council of Sampleton",
      },
      candidates,
      classifier_responses: [classifierResponse],
      parent_links: parentLinks,
      confidence_floor: 0.4,
      batch_size: 15,
    },
  });
  expect(response.status()).toBe(200);
  const body = (await response.json()) as DiscoveryResponse;

  expect(body.ok).toBe(true);
  expect(body.llm_call_count).toBe(2); // one classifier + one parent-resolver

  const sources = body.sources;
  expect(sources.length).toBeGreaterThan(0);

  // News page should NOT appear in the output (classifier dropped it).
  expect(
    sources.find((s) => s.source_url.includes("news/road-closure-2024")),
  ).toBeUndefined();

  // Acceptance #1: primary land-use bylaw is identifiable in the shape
  // run_learning_pipeline._find_primary_source expects.
  const primary = sources.find(
    (s) =>
      s.document_type === "bylaw" &&
      s.parent_document === null &&
      s.in_scope === true &&
      s.document_name === "Land Use By-Law",
  );
  expect(primary).toBeDefined();
  expect(primary?.format).toBe("pdf");
  expect(primary?.access_method).toBe("direct_download");

  // Acceptance #1: at least one companion bylaw alongside the primary.
  expect(body.companions).toContain("Subdivision By-Law");
  // Policies are also valid companions per the SemanticMapper signal.
  expect(body.companions).toContain("Heritage Design Guidelines");

  // Acceptance: the amendment is linked to its parent bylaw (resolver worked).
  const amendment = sources.find(
    (s) => s.document_name === "Bylaw 2024-03 Heritage Amendment",
  );
  expect(amendment?.parent_document).toBe("Land Use By-Law");
  expect(amendment?.in_scope).toBe(false);
  expect(amendment?.amendment_series).toBe("Bylaw No. 2024-03");

  // Acceptance: the zoning schedule is also linked to the LUB.
  const schedule = sources.find(
    (s) => s.document_name === "Zoning Map Schedule A",
  );
  expect(schedule?.parent_document).toBe("Land Use By-Law");
  expect(schedule?.document_type).toBe("schedule");
});

test("discovery endpoint drops low-confidence classifications below the floor", async ({
  request,
}) => {
  // Single candidate, one classifier call returning a low-confidence
  // entry — the agent's confidence_floor should drop it entirely, and
  // since no children exist the parent-resolution call is skipped.
  const response = await request.post(`${E2E_API_URL}/v1/_test/discover`, {
    headers: { "Content-Type": "application/json" },
    data: {
      municipality: {
        name: "Town of Sampleton",
        jurisdiction_code: "sampleton",
        province: "Nova Scotia",
      },
      candidates: [
        {
          url: "https://sampleton.example.com/maybe.pdf",
          link_text: "Maybe a bylaw?",
          discovered_on: "https://sampleton.example.com/bylaws",
          is_pdf: true,
        },
      ],
      classifier_responses: [
        [
          {
            url: "https://sampleton.example.com/maybe.pdf",
            document_name: "Maybe a bylaw",
            document_type: "bylaw",
            document_role: "?",
            format: "pdf",
            access_method: "direct_download",
            in_scope: true,
            confidence: 0.10,
          },
        ],
      ],
      // No parent_links — confidence-filtered set is empty so resolver
      // is skipped anyway.
      confidence_floor: 0.5,
      batch_size: 15,
    },
  });
  expect(response.status()).toBe(200);
  const body = (await response.json()) as DiscoveryResponse;
  expect(body.ok).toBe(true);
  expect(body.sources).toEqual([]);
  expect(body.companions).toEqual([]);
  // Only the classifier call was made — parent-resolver short-circuits
  // when there's < 2 classified candidates.
  expect(body.llm_call_count).toBe(1);
});
