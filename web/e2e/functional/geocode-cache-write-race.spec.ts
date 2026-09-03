// Functional regression: ABS-422 — geocode-cache write race must not 500.
//
// Scope
// -----
// `_cache_put` in src/layer2/retrieval/geocode.py used to be a bare
// check-then-insert on `geocode_cache` with no ON CONFLICT and no
// IntegrityError handling. When two requests geocode the SAME not-yet-cached
// address concurrently, both read a cache miss and both try to INSERT the
// same `normalized_text`. The loser tripped
// `uq_geocode_cache_normalized_text` (psycopg UniqueViolation), which
// poisoned the SQLAlchemy session and cascaded into a PendingRollbackError —
// the request 500'd, and the dead session could take unrelated work in the
// same request down with it.
//
// The fix inserts inside a SAVEPOINT, catches the IntegrityError, rolls back
// only the nested unit, and re-reads the winning row. Cache write contention
// must never fail a user request.
//
// Approach
// --------
// The address-profile test endpoint (`POST /v1/_test/address-profile`,
// ABS-273) drives the exact resolve_location -> _cache_put path over
// real-stack HTTP + Postgres, one fresh `session_scope()` per request. We
// fire a burst of concurrent requests for a SINGLE freshly-minted (therefore
// uncached) civic address so they collide on the same `normalized_text`
// insert, and assert every response is a graceful 200 — never a 500. Pytest
// (tests/datasets/test_geocode.py) covers the recovery branch
// deterministically; this spec proves it holds under genuine Postgres
// concurrency, which is the only place the real UniqueViolation fires.

import { E2E_API_URL, expect, test } from "../fixtures/test-env";


// Concurrency high enough to reliably open the check-then-insert window for
// at least one loser under the default connection pool.
const BURST = 16;


test("concurrent resolves of the same uncached address never 500 on the cache write", async ({
  request,
}) => {
  // A civic address absent from every seeded dataset, uniquely numbered per
  // run so the shared (persistent) e2e Postgres has no cached row to short-
  // circuit the insert. 1-5 digit civic number + capitalized street + suffix
  // matches _CIVIC_PATTERN, so it parses to a civic_address ref and reaches
  // _cache_put as a `no_match` write.
  const civic = 10000 + Math.floor(Math.random() * 80000);
  const address = `${civic} Raceway Street`;

  const responses = await Promise.all(
    Array.from({ length: BURST }, () =>
      request.post(`${E2E_API_URL}/v1/_test/address-profile`, {
        headers: { "Content-Type": "application/json" },
        data: { address },
      }),
    ),
  );

  // Not a single request may 500 on the cache-write race. Before the fix, the
  // loser of the insert race surfaced a 500 (UniqueViolation ->
  // PendingRollbackError). Report the offenders' bodies so a regression is
  // diagnosable rather than a bare status assertion.
  const failures: string[] = [];
  for (const response of responses) {
    if (response.status() !== 200) {
      failures.push(`${response.status()}: ${await response.text()}`);
    }
  }
  expect(
    failures,
    `at least one concurrent resolve failed the cache write:\n${failures.join("\n")}`,
  ).toEqual([]);

  // Every response is the graceful unresolvable DTO — the address genuinely
  // matches nothing, and the race is absorbed rather than raised.
  const bodies = await Promise.all(responses.map((r) => r.json()));
  for (const body of bodies) {
    expect(body.unresolvable).toBe(true);
    expect(body.civic_number).toBe(String(civic));
  }
});
