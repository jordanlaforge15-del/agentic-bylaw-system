# Phase 2 — follow-ups and known caveats

Captured 2026-05-21 at the end of the Phase 2 implementation cycle
(ABS-48 → ABS-49 → ABS-52 → ABS-51 → ABS-50 → ABS-53 → ABS-54). The
Phase-2 stack is on `phase-2-bim-ingestion` and not yet merged into
`dev`. This doc records what shipped with caveats, what was
deferred, and what cross-phase work it surfaced — so a future
operator (or future-me) doesn't have to re-derive these from commit
messages.

Each item names the relevant code path so it stays grep-able. File a
follow-up issue under the appropriate phase project when picking
something up; don't quietly fix here.

## APS / .rvt path needs a real round-trip

**Where:** `src/layer1/parsers/aps_submission.py`,
`docs/extraction/revit_parameter_map.md`.

ABS-50 shipped the full APS orchestration (auth → OSS signed-S3
upload → Model Derivative translation → manifest polling →
metadata + properties fetch) and the Revit `BuiltInParameter` →
Phase-1 taxonomy mapper, plus 12 mocked-HTTP tests. It has **never
been exercised against a real Autodesk APS endpoint** because no
credentials were available in the implementation session.

To validate: drop `APS_CLIENT_ID` and `APS_CLIENT_SECRET` into the
deploy env, run `extract_aps(some_rvt_path, SubmissionIngestConfig())`
against a small real `.rvt`, confirm:

1. The bucket / upload / translate / poll loop completes (latency:
   30–120 s for a small file, per Autodesk's published numbers).
2. The property payload's actual category names and parameter names
   match the mapper's `_HEIGHT_KEYS` / `_USE_CLASS_KEYS` /
   `_AREA_KEYS` heuristics. Edit
   `docs/extraction/revit_parameter_map.md` and the tuples in
   `aps_submission.py` with any new BuiltInParameter names the real
   file surfaces.
3. The unit conversion in `_coerce_metres` is correct against the
   sample (Revit's internal units are normally mm / mm² but some
   Canadian templates ship in metres; if the latter, read the
   `units` field on the property and gate the scale on it).

Also worth doing in the same engagement: extract the project's base
point + true-north from the APS metadata and wire it into
`project_location_from_aps` so ABS-51's setbacks work for the APS
path the way they do for the IFC path. The hook is already there;
only the lookup of the relevant JSON keys is deferred.

The `.rvt` upload path is **rejected at the API today** (HTTP 415 with
a clear error). The frontend doesn't need changes to enable APS —
just remove the extension check in
`src/advisor/api/submissions_router.py::upload_submission`.

## `values_callable` enum fix should land on Phase 1 too

**Where:** `src/layer2/compliance/db/models.py`.

ABS-53 was the first writer of `submission` / `submission_attribute`
rows through a real-Postgres e2e and uncovered that the SAEnum
columns serialized the StrEnum *name* (`"DRAFT"`) instead of the
*value* (`"draft"`). Postgres rejected with
`InvalidTextRepresentation`; sqlite tolerated it, which is why the
existing Phase-1 compliance-schema tests
(`tests/test_compliance_schema.py`) didn't catch it. Fixed by
passing `values_callable=lambda e: [m.value for m in e]` to each of
the three `SAEnum` columns on `Submission` and `SubmissionAttribute`.

**Audit needed:** every other StrEnum-backed SAEnum in the codebase
likely has the same latent issue. Grep for `SAEnum(.*StrEnum`/
`SAEnum(.*Enum, name=` and verify each one either (a) already passes
`values_callable`, (b) is sqlite-only in practice, or (c) gets the
same fix. A short Phase-1 audit issue closes this cleanly.

A migration-side audit is also worth doing: every `sa.Enum(...)` in
`alembic/versions/` should match the Python StrEnum values exactly.
The migration 0014 values
(`"draft"`, `"evaluating"`, ...) and the StrEnum values in
`db/models.py` happen to match, which is why this fix works without a
data migration. New enums added in future migrations should keep that
property explicitly.

## ABS-53 deferrals (UI scope cuts called out in the plan)

**Where:** `web/app/(product)/submissions/...`,
`src/advisor/api/submissions_router.py`.

The submission UI shipped the core flow (upload → attribute review +
override → evaluator → matrix) plus the disclaimer banner. The
following were deferred and called out in the In-Progress plan; each
is a focused follow-up issue:

- **Address-to-parcel geocoding.** Today the upload form expects a
  parcel PID (`parcel.parcel_identifier`). The geocode →
  ST_Contains lookup already exists in
  `layer2.spatial.extractor.extract_lot_facts`; lift it into
  `_resolve_parcel` in `submissions_router.py` and add an
  address-typeahead to the upload form.
- **Map-based parcel picker.** Needs a Leaflet / MapLibre dependency
  and a parcel-tile source. Useful UX win but real scope; sized as
  its own issue.
- **Streaming extraction progress.** The pipeline is synchronous
  today; a real progress stream needs a job queue + SSE. Worth
  doing once the typical IFC starts producing >10 s of work, which
  with the current Phase-1 stack it doesn't.
- **PDF / shareable-link export of the compliance matrix.** The
  matrix renders cleanly enough to screenshot; PDF is a polish
  follow-up. Shareable-link auth needs an opinion (signed-URL token
  vs. requiring sign-in vs. read-only invite) — open the discussion
  before building.
- **`.rvt` upload in the UI.** Backend rejects with HTTP 415 + a
  message; the frontend never needs to send it. Flip when the APS
  round-trip above is validated.

## Pre-existing `lot-facts-centerline-buffer` e2e failure (not caused by Phase 2)

**Where:** `web/e2e/functional/lot-facts-centerline-buffer.spec.ts`,
`src/layer2/spatial/lot_metrics.py`.

Full `make e2e` on `phase-2-bim-ingestion` reports **73 / 74** with
`lot-facts-centerline-buffer.spec.ts` failing
(`Expected: "ok", Received: "unresolved"` and variants). Verified
this is NOT introduced by any Phase-2 commit:

- `git merge-base dev phase-2-bim-ingestion` → `f6b477f` (local dev
  tip when this work started).
- The fix `bbb232e [fix] ABS-23: corner / multi-frontage lots via
  wider buffer + street grouping` is on `dev` but **not** an
  ancestor of `f6b477f` (it landed via merge `96e2bc4` after the
  project branch was cut).
- `git log dev -- src/layer2/spatial/lot_metrics.py` lists `bbb232e`
  among the most recent commits; the project branch never picked it
  up.

Two clean resolutions:

1. **Wait.** When `phase-2-bim-ingestion` merges into `dev` (or `dev`
   merges into the project branch), the missing fix resolves.
2. **Cherry-pick `bbb232e` into the project branch** if you want a
   fully-green `make e2e` gate during Phase-2-internal work. One
   small commit, no Phase-2 scope creep.

Either way: the failure is pre-existing-at-the-merge-base and not a
Phase-2 regression. The submission-upload spec
(`web/e2e/functional/submission-upload.spec.ts`) is the load-bearing
new spec for ABS-53 and passes cleanly.

## `tests` is now on `pyproject.toml` `pythonpath`

**Where:** `pyproject.toml` `[tool.pytest.ini_options].pythonpath`.

ABS-49 needed a way for one test module to import test fixtures from
another (the synthetic-IFC builder at
`tests/fixtures/submissions/synthetic_ifc.py`). The repo didn't
have an established pattern; I added `"tests"` to `pythonpath` so
modules under `tests/` are importable as top-level names
(`from fixtures.submissions.synthetic_ifc import ...`).

Side effects worth knowing:

- The pre-existing broken import in
  `tests/layer2/test_semantic_retrieval.py`
  (`from tests.test_semantic_enrichment import semantic_db`) is
  **still broken** because that one uses a `tests.` package prefix
  that would require `tests/__init__.py`. Out of scope for Phase 2;
  worth filing a Phase-1 cleanup issue.
- Any future test fixture intended for reuse goes under
  `tests/fixtures/<area>/`. The Phase-2 fixtures
  (`tests/fixtures/submissions/synthetic_ifc.py` and
  `tests/fixtures/submissions/aps_property_payloads.py`) are the
  reference shape.

## Project branch is local-only, never pushed to origin

`phase-2-bim-ingestion` exists only on this machine. When it's time
to ship Phase 2, the natural sequence is:

1. Merge `phase-2-bim-ingestion` → `dev` (resolves the lot-facts
   spec failure noted above).
2. Run `make e2e` on `dev`, confirm fully green.
3. Promote `dev → main` per `docs/BRANCHING_STRATEGY.md`.
4. Run the deploy recipe in `docs/DEPLOYMENT.md`.

Pushing the project branch to origin is fine if it's useful for
remote backup or sharing, but isn't a prerequisite for the merge.
