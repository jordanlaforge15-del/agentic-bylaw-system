---
Title: ABS-7 — lot_facts Part 2: centerline-buffer frontage, depth, and corner detection
Description: Part 1 (commit 54dfaf4) shipped area-only because the shared-edge frontage heuristic in src/layer2/spatial/lot_metrics.py collapsed to 0 m on HRM's parcel layer — parcels tessellate edge-to-edge to road centerlines, so every front edge of a residential lot is ε-close to the parcel directly across the street and got classified as "shared with a neighbour." Part 2 replaces that with a centerline-buffer algorithm:

    frontage = ST_Length(ST_Intersection(parcel_boundary,
                                         ST_Buffer(centerline_union, buffer_m)))

with buffer_m defaulting to 8 m. depth = area / frontage when frontage > 1 m. Corner detection uses a length-thresholded bearing analysis (filters out the perpendicular-edge artifact bits — see _detect_corner docstring) instead of the literal "count of distinct buffer components" spec, because two streets meeting at an intersection merge into a single buffer component on real urban corners.

What landed in this branch:
- src/layer1/datasets/halifax_street_centerlines.yaml — new dataset pinned to the HRM StreetNetwork FeatureServer (layer 0, ASSETID feature key).
- src/layer1/datasets/config.py — DatasetRole extended with "road_centerlines".
- src/layer2/spatial/lot_metrics.py — rewritten with the centerline-buffer algorithm, projection helpers, corner-detection threshold tied to buffer_m.
- src/layer2/spatial/extractor.py — surfaces frontage_m/depth_m/corner alongside area_m2/perimeter_m, method="centerline_buffer", drops to confidence=0.7 when frontage < 5% of perimeter, area-only fallback when centerlines aren't ingested, removed unused _find_neighbour_parcels.
- docs/agent/persona.md — frontage/depth/corner copy restored.
- tests/layer2/spatial/test_lot_metrics.py — 12 unit tests with synthetic Quinpool-/Barrington-/Duke-style fixtures.
- tests/layer2/spatial/test_extractor.py — parcels_db fixture now ingests a tiny road centerlines GeoJSON; happy-path test asserts the full centerline-buffer payload.

E2E coverage added per the SDLC requirement:
- scripts/seed_e2e_parcels.py — idempotent seeder that inserts a 15×30 m synthetic parcel, an east-west centerline along its south edge, and a geocode-cache row for "100 Test Street" into layer1_test.
- scripts/inspect_case_metadata.py — small CLI that prints advisor_case.metadata_json as JSON (CaseOut doesn't expose metadata_json over HTTP).
- web/e2e/functional/lot-facts-centerline-buffer.spec.ts — POST /v1/cases with the seeded anchor, inspect the persisted spatial_facts, assert method=centerline_buffer + area_m2 ≈ 450 + frontage_m ≈ 31 + depth_m ≈ 14.5 + corner=false + confidence=1.0.

Branch: worktree-abs-7-lot-facts-centerline-buffer (off main when the workflow still rooted there; dev was merged in to integrate the post-rollout commits before the final merge back into dev).

Verification still pending (per the Linear ticket): prod ingest of HRM street centerlines (ssh bylaw-prod), bylaw-advisor:0.5.5 build + deploy, real-address sanity check against 6321 Quinpool / 1505 Barrington / 5251 Duke comparing to HRM's mapping tool.

Start Date: 2026-05-17
---
Title: ABS-9 test follow-up — Playwright specs for case-open credit adoption & network error
Description: Add instrumented Playwright tests covering both halves of the ABS-9 fix that shipped on 2026-05-17 (commits 1240f0b/ca3778e on dev, since promoted to main).

UI side — web/e2e/functional/case-open-network-error.spec.ts (1 test, new):
Uses page.route + route.abort('failed') to deterministically reproduce the Safari "Load failed" / dropped-connection rejection on POST /api/cases. Asserts the new "Network error opening case: …" banner appears, the Open-case button re-enables, and the URL stays on /cases/new.

Backend side — web/e2e/functional/abs9-credit-adoption.spec.ts (1 test, relocated + patched):
Originally drafted untracked on dev. Moved into this branch and fixed: the existing assertion `expect(chatBody).toMatch(/RC-LUB §15\.4/)` couldn't match because the SSE wire format JSON-escapes § as `§`. Added a decodeAssistantText helper that parses each `data:` line as JSON and concatenates the `content_block_delta.text_delta` payloads, then asserts the citation against the decoded text. The seed step uses a fresh user with exactly 1 credit per tier, so the original 402 symptom would actually fire if the ABS-9 fix were reverted.

Dropped from a prior iteration: web/e2e/functional/case-open-credit-adoption.spec.ts — relied on the shared 200-credit demo user so it didn't differentiate broken from fixed code. The relocated dev spec covers the same regression more rigorously.

Branch: worktree-abs-9-playwright-tests (off dev, per docs/BRANCHING_STRATEGY.md).
Start Date: 2026-05-18
---
Title: ABS-9 — Fix network error when creating a new case
Description: User norman.stephanie@gmail.com in prod hit "Network error: load failed" when creating a new case. Root cause: a 402 on the first /v1/chat after /v1/cases — open_case reserves a credit against the case (session_id=NULL), and the chat route's reserve_credit_for_session ignored that and claimed another available credit, 402'ing users who'd just spent their last credit on the case open. Fix: chat now adopts the case-reserved credit. Also added a try/catch on the frontend openCase fetch so a real network error surfaces instead of being swallowed.
Start Date: 2026-05-17
Merged Into Main: Yes
Date Merged: 2026-05-17
---
Title: ABS-8 — Case creation 402 in prod (idempotency + leaked-credit recovery)
Description: chris.rafuse@gmail.com hit a 402 ({code: no_available_credit, tier: standard}) on POST /v1/cases despite having been granted 3 standard credits. Root cause is the ABS-11 double-reservation bug — two of his three credits were stuck in `reserved` state with session_id=NULL, attached to cases that already had a sessioned credit. ABS-9 closed the chat-side leak (the second reservation) but did not address (a) the equivalent leak path on a duplicate POST /v1/cases for the same anchor, or (b) the credits already leaked in prod. This change adds a tier-matched idempotency guard in open_case so a re-open returns the case's existing active credit instead of claiming a fresh one, plus a refund_orphaned_case_reservations sweep + admin endpoint POST /v1/admin/maintenance/refund-orphaned-reservations that conservatively refunds reserved-no-session credits whose case has a sibling sessioned active credit at the same tier (the diagnostic signature of the pre-ABS-9 leak). New audit event credit_reused distinguishes idempotent re-open from a fresh reservation. Recovery for credit 9 (no sibling — case 4 has no other active credit) falls to the existing 24h abandon sweep or a manual admin grant.
Start Date: 2026-05-17
Merged Into Main: No
Date Merged: —
---
