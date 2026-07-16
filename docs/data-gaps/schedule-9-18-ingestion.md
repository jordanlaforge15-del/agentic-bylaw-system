# Data gap: Schedule 9 (landmark sites) and Schedule 18 (front setbacks)

**Raised by:** ABS-375 (spatial/schedule lookups fail during report runs).
**Status:** ingestion ticket — the datasets do not exist in the corpus.

## Why this is a ticket, not a code fix

ABS-375's definition of done says: *"If a dataset is genuinely absent from
the corpus, this issue's outcome is instead the ingestion ticket for it —
punting to the customer stops being the failure mode."* Two of the three
degraded rows in the retest fall under that clause. The third — the
adjacent-parcel zoning lookup — **was** buildable and is implemented in this
same change (`get_adjacent_zoning`; see `src/advisor/chat/tools.py` and
`RetrievalService.get_adjacent_zoning`). This document is the ingestion
follow-up for the two that need data we do not yet hold.

## What is missing

Audited on 2026-07-09 against `data/geo-datasets/` and the dataset configs in
`src/layer1/datasets/`:

| Schedule | What the report needs | Present in corpus? |
|----------|-----------------------|--------------------|
| **Schedule 9 — Landmark sites** | Whether a specific parcel is a designated landmark site (the go/no-go gate for multi-unit dwelling use in the INS zone: conditionally permitted **only** on a Schedule 9 landmark site). | **No.** No landmark dataset, no `landmark` canonical field, no GeoJSON. The only `landmark` hits in the tree are the word in the `named_place` tool-description prose. |
| **Schedule 18 — Front setbacks** | The parcel-specific / streetwall front-setback value the schedule maps spatially. | **No.** The only reference is a regex in `src/layer1/profiles.py` used during PDF *parsing* ("setback as specified on Schedule 18") — there is no queryable spatial dataset. |

The ingested `Land_Use_Schedules_*.geojson` covers only schedule areas
`E / L / R / S` (BYLAW_IDs 9 and 21) — not 9 or 18.

Consequence today: a report needing either fact has no spatial feature to
hit, falls back to `search_bylaw_evidence` text retrieval, and — when that
misses — degrades the decisive row to the neutral "Review required" /
"confirm with HRM Planning & Development" band. On DS-000020 (1250 Robie St)
that left the project's own critical-threshold row (Schedule 9 landmark
status) and the Schedule 18 front setback unresolved.

## Ingestion work required

1. **Source the layers from HRM open data.** Locate the Schedule 9 landmark
   sites and Schedule 18 front-setback layers on HRM's ArcGIS/Hub (the same
   publisher and REST pattern already used by `halifax_zoning.yaml`,
   `halifax_height_precincts.yaml`, etc.). If HRM does not publish them as
   spatial layers, the schedules must be georeferenced from the LUB PDF maps
   — a larger task; flag it back to product before starting.
2. **Add dataset configs** under `src/layer1/datasets/` mirroring the
   existing overlay YAMLs:
   - `halifax_schedule9_landmark_sites.yaml` — canonical field e.g.
     `landmark: bool` (or `landmark_name`), `links_to` the Schedule 9
     fragment. Its name must contain a keyword so
     `overlay_role_for_name` classifies it — add a `("landmark", "landmark")`
     entry to `OVERLAY_ROLE_KEYWORDS` in
     `mcp/bylaw_retrieval/retrieval/service.py`, and an `AddressProfile`
     field + `_build_address_profile` branch so the point-in-polygon result
     surfaces as a definitive true/false (the same pattern as
     `heritage` / `abuts_pedestrian_street`).
   - `halifax_schedule18_front_setbacks.yaml` — canonical field
     `front_setback_m: float`, `links_to` the Schedule 18 fragment.
3. **Drop the GeoJSON** under `data/geo-datasets/` and wire it into the
   ingest run.
4. **Verify** with a case at a known landmark site and a known Schedule 18
   street, asserting `get_address_profile` returns the landmark flag and the
   front-setback value rather than null.

## Once ingested, no further report-agent change is needed

The report agent already reaches spatial overlays through
`get_address_profile` / `search_bylaw_evidence` with the location slot, and
the persona already instructs it to prefer resolved data over deferring to
HRM. A Schedule 9 landmark overlay and a Schedule 18 setback overlay slot
into that existing machinery: once the datasets are ingested and role-tagged,
the point-in-polygon intersection resolves the two rows the same way zone /
height / FAR resolve today. The prompt language added in ABS-375
(`questions.py` dev-standards + variance templates) already tells the agent
to name the specific schedule rather than default the verdict to "verify with
HRM", so the failure mode is contained even before ingestion lands.
