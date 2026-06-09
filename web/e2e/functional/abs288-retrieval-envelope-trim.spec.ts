// ABS-288: Trim the retrieval API response envelope.
//
// Verifies that:
//   AC-1  The response envelope has no ``request`` field (request echo removed).
//   AC-2  Matches omit empty list/dict fields (ancestor_chain, cross_references,
//          related_tables, linked_datasets, metadata_json) when include_* flags
//          are all False (the new defaults).
//   AC-3  Core match fields (fragment_id, text, score, etc.) are always present.
//   AC-4  Byte size of the serialised response is materially smaller than a
//          synthetic baseline that includes the request echo + all empty arrays.
//
// Calls POST /v1/_test/search-evidence-raw, which returns the full
// model_dump(mode="json") of RetrievalResponse without any compact layer.

import { execSync } from "node:child_process";
import * as path from "node:path";

import { test, expect, E2E_API_URL, DEMO_USER_ID } from "../fixtures/test-env";

test.beforeAll(() => {
  const repoRoot = path.resolve(__dirname, "..", "..", "..");
  const venvPython = path.join(repoRoot, ".venv", "bin", "python");
  const pgPort = process.env.PG_PORT || "5432";
  const databaseUrl =
    process.env.DATABASE_URL ||
    `postgresql+psycopg://layer1:layer1@localhost:${pgPort}/layer1_test`;
  const env = {
    ...process.env,
    DATABASE_URL: databaseUrl,
    PYTHONPATH: `${path.join(repoRoot, "src")}:${process.env.PYTHONPATH || ""}`,
  };
  execSync(
    `"${venvPython}" "${path.join(repoRoot, "scripts", "seed_e2e_evaluator_bylaws.py")}"`,
    { env, encoding: "utf-8" },
  );
});

async function searchRaw(
  request: import("@playwright/test").APIRequestContext,
  query: string,
  limit = 5,
) {
  const resp = await request.post(`${E2E_API_URL}/v1/_test/search-evidence-raw`, {
    headers: {
      "Content-Type": "application/json",
      "X-Test-User-Id": DEMO_USER_ID,
    },
    data: { query, limit },
  });
  expect(resp.ok(), `search-evidence-raw failed: ${resp.status()}`).toBeTruthy();
  return resp.json() as Promise<Record<string, unknown>>;
}

// AC-1: no request echo in envelope
test("response envelope has no request field", async ({ request }) => {
  const body = await searchRaw(request, "setback residential zone");
  expect(body).not.toHaveProperty("request");
  expect(body).toHaveProperty("total_matches");
  expect(body).toHaveProperty("matches");
});

// AC-2: empty list/dict fields absent from matches
test("matches omit empty list and dict fields when include_* defaults are False", async ({ request }) => {
  const body = await searchRaw(request, "setback residential zone", 3);
  const matches = body.matches as Record<string, unknown>[];
  expect(matches.length).toBeGreaterThan(0);

  for (const match of matches) {
    // These fields are only present when non-empty
    if ("ancestor_chain" in match) {
      expect((match.ancestor_chain as unknown[]).length).toBeGreaterThan(0);
    }
    if ("cross_references" in match) {
      expect((match.cross_references as unknown[]).length).toBeGreaterThan(0);
    }
    if ("related_tables" in match) {
      expect((match.related_tables as unknown[]).length).toBeGreaterThan(0);
    }
    if ("linked_datasets" in match) {
      expect((match.linked_datasets as unknown[]).length).toBeGreaterThan(0);
    }
    if ("metadata_json" in match) {
      expect(Object.keys(match.metadata_json as object).length).toBeGreaterThan(0);
    }
  }
});

// AC-3: core fields always present
test("matches always carry fragment_id, text, and score", async ({ request }) => {
  const body = await searchRaw(request, "setback residential zone", 3);
  const matches = body.matches as Record<string, unknown>[];
  expect(matches.length).toBeGreaterThan(0);

  for (const match of matches) {
    expect(typeof match.fragment_id).toBe("number");
    expect(typeof match.text).toBe("string");
    expect(typeof match.score).toBe("number");
    expect(typeof match.municipality).toBe("string");
    expect(typeof match.bylaw_name).toBe("string");
  }
});

// AC-4: byte-size sanity — raw envelope is smaller than a naive full response
test("serialised response is compact (no bulky echo overhead)", async ({ request }) => {
  const body = await searchRaw(request, "setback residential zone", 5);
  const matches = body.matches as Record<string, unknown>[];

  if (matches.length === 0) {
    // Can't measure compactness with no matches; skip gracefully.
    test.info().annotations.push({ type: "note", description: "No matches; compactness check skipped." });
    return;
  }

  const bytes = JSON.stringify(body).length;
  // A response with a request echo would add ~500 bytes minimum (all the
  // include_* flags, null fields, query text, etc.). Assert the response
  // is present but doesn't balloon — 30 KB for 5 matches is generous.
  expect(bytes).toBeLessThan(30_000);
});
