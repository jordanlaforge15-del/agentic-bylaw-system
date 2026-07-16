// ABS-297: WI-7 / WI-3 surface-asymmetry drift guard.
//
// ABS-288 (WI-7) flipped RetrievalRequest.include_* defaults to False at
// the MCP / external surface. The advisor production path
// (src/advisor/chat/tools.py:774-777) deliberately keeps the LLM-visible
// behaviour True-by-default via an explicit payload.get("include_*",
// True) fallback, so WI-3 fan-out leads (cross_references,
// ancestor_chain, linked_datasets) keep reaching the model whenever it
// omits the include_* flags — which the persona never tells it to set.
//
// If a future refactor drops that explicit fallback (e.g. unpacks the
// payload directly into RetrievalRequest, or calls
// payload.get("include_*") without the True), the handler will inherit
// the post-WI-7 False defaults and the model silently stops seeing
// fan-out leads. No exception, no warning, just a quiet cost regression
// and a quiet quality regression. Neither abs288 nor abs289 catches
// this.
//
// This spec drives /v1/_test/advisor-search-include-flags, which runs
// the real search_bylaw_evidence_handler against a capturing stub
// RetrievalService and returns the include_* flags on the constructed
// RetrievalRequest. The Python unit tests
// (tests/advisor/chat/test_tools.py — ABS-297 section) pin the same
// contract at the handler boundary; this spec proves it holds through
// the running FastAPI stack.

import { test, expect } from "@playwright/test";

const E2E_API_URL = process.env.E2E_API_URL ?? "http://127.0.0.1:8001";

test("default advisor search payload yields all include_* flags True (WI-3 fan-out lead drift guard)", async ({
  request,
}) => {
  const response = await request.post(
    `${E2E_API_URL}/v1/_test/advisor-search-include-flags`,
    { data: { query: "residential zones" } },
  );
  expect(
    response.status(),
    `endpoint failed: ${await response.text()}`,
  ).toBe(200);
  const flags = await response.json();

  // Each assertion's failure message names the exact regression so a
  // future maintainer staring at red CI can fix the right line without
  // re-deriving the WI-7 ↔ WI-3 asymmetry.
  expect(
    flags.include_context,
    "ABS-297 drift: include_context fell through to RetrievalRequest's False default. " +
      "Restore payload.get('include_context', True) in chat/tools.py search_bylaw_evidence_handler.",
  ).toBe(true);
  expect(
    flags.include_cross_references,
    "ABS-297 drift: include_cross_references fell through to False. " +
      "WI-3 cross_references fan-out leads will silently stop reaching the model.",
  ).toBe(true);
  expect(
    flags.include_tables,
    "ABS-297 drift: include_tables fell through to False. " +
      "Restore payload.get('include_tables', True).",
  ).toBe(true);
  expect(
    flags.include_datasets,
    "ABS-297 drift: include_datasets fell through to False. " +
      "WI-3 linked-dataset fan-out leads will silently die.",
  ).toBe(true);
});

test("explicit include_* False from the model is preserved (does not get masked by True-by-default)", async ({
  request,
}) => {
  // Paired check: the True-by-default fallback must NOT clobber an
  // explicit opt-out from the model. If a future implementation
  // switches to ``payload.get("include_*", True) or True`` to "be safe",
  // a model that deliberately opts out (e.g. on a follow-up turn that
  // already has the cross-refs from its parent turn) would lose that
  // ability. Pin that contract too.
  const response = await request.post(
    `${E2E_API_URL}/v1/_test/advisor-search-include-flags`,
    {
      data: {
        query: "residential zones",
        include_context: false,
        include_cross_references: false,
        include_tables: false,
        include_datasets: false,
      },
    },
  );
  expect(response.status()).toBe(200);
  const flags = await response.json();

  expect(flags.include_context).toBe(false);
  expect(flags.include_cross_references).toBe(false);
  expect(flags.include_tables).toBe(false);
  expect(flags.include_datasets).toBe(false);
});
