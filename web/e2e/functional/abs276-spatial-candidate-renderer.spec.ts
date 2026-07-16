// ABS-276: spatial candidate renderer renders all canonical attributes generically.
//
// Before this fix, _feature_to_candidate in src/layer2/retrieval/spatial.py only
// rendered height keys (max_height_m, max_height_storeys) and emitted
// "no maximum height specified" for features that carry other attributes like
// max_far. This caused FAR-precinct candidates to reach the LLM with
// no attribute data — a silent regression.
//
// The fix replaces the height-only if-ladder with a generic loop over
// sorted(canonical.items()), skipping only display_label and source_case
// (which get explicit placement). This spec exercises that path end-to-end
// through the real FastAPI stack via the POST /v1/_test/spatial-candidate-text
// endpoint (mounted only in e2e_server.py).
//
// Assertions:
//   - FAR-precinct feature: "max_far=1.75" in text; old "no maximum height"
//     message absent.
//   - Height-precinct feature: "max_height_m=12" and "max_height_storeys=4"
//     present (regression guard).
//   - Mixed feature: both height and FAR attributes present in one candidate.
//   - display_label surfaces first; source_case appended last.
//   - Attribute with integer value rendered without decimal point.

import { test, expect, E2E_API_URL, DEMO_USER_ID } from "../fixtures/test-env";

async function candidateText(
  request: import("@playwright/test").APIRequestContext,
  canonicalAttributes: Record<string, unknown>,
  citationLabel = "(4.5)",
): Promise<string> {
  const res = await request.post(
    `${E2E_API_URL}/v1/_test/spatial-candidate-text`,
    {
      headers: {
        "Content-Type": "application/json",
        "X-Test-User-Id": DEMO_USER_ID,
      },
      data: { canonical_attributes: canonicalAttributes, citation_label: citationLabel },
    },
  );
  expect(
    res.status(),
    `spatial-candidate-text failed: ${res.status()} ${await res.text()}`,
  ).toBe(200);
  const body = (await res.json()) as { text: string };
  return body.text;
}

test("FAR-precinct feature renders max_far and omits old height-only fallback", async ({
  request,
}) => {
  const text = await candidateText(request, { max_far: 1.75 });

  expect(text).toContain("max_far=1.75");
  // The pre-fix fallback must never appear for non-height datasets.
  expect(text).not.toContain("no maximum height specified");
});

test("height-precinct feature still renders max_height_m and max_height_storeys", async ({
  request,
}) => {
  const text = await candidateText(request, {
    max_height_m: 12.0,
    max_height_storeys: 4,
  });

  // Float formatted with :g strips trailing zeros (12.0 → "12").
  expect(text).toContain("max_height_m=12");
  expect(text).toContain("max_height_storeys=4");
  expect(text).not.toContain("no maximum height specified");
});

test("mixed feature with both height and FAR renders both attributes", async ({
  request,
}) => {
  const text = await candidateText(request, {
    max_far: 2.0,
    max_height_m: 18.5,
    max_height_storeys: 6,
  });

  expect(text).toContain("max_far=2");
  expect(text).toContain("max_height_m=18.5");
  expect(text).toContain("max_height_storeys=6");
});

test("display_label surfaces before attributes; source_case appended last", async ({
  request,
}) => {
  const text = await candidateText(request, {
    display_label: "FAR Precinct A",
    max_far: 1.5,
    source_case: "HRM-2024-003",
  });

  // display_label first, then attributes in sorted order, then source_case.
  const labelIdx = text.indexOf("FAR Precinct A");
  const farIdx = text.indexOf("max_far=1.5");
  const caseIdx = text.indexOf("source_case=HRM-2024-003");

  expect(labelIdx).toBeGreaterThanOrEqual(0);
  expect(farIdx).toBeGreaterThan(labelIdx);
  expect(caseIdx).toBeGreaterThan(farIdx);
});
