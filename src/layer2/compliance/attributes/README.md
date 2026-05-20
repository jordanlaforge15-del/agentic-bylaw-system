# Phase-1 attribute taxonomy

`taxonomy.yaml` is the single source of truth for the attribute IDs
that flow between extraction (BIM, PDF, manual entry) and evaluation
(bylaw clauses tagged with regulated attributes).

## Where the IDs surface

* `source_fragment.attribute_tags` — list of attribute IDs that a
  bylaw clause regulates. Populated by the semantic-enrichment pass
  (`src/layer2/semantic/enrich_attribute_tags.py`).
* `submission_attribute.attribute_key` — the attribute being asserted
  by a submission. Each row stores `value_json` in the shape the
  taxonomy entry's `value_type` expects.

## Categories

* **`geometric_direct`** — measurable straight off a BIM model or
  PDF (height, GFA).
* **`geometric_derived`** — computed from BIM + parcel geometry
  (setbacks, FAR, lot coverage).
* **`categorical`** — project-metadata attributes (use class,
  occupancy, unit mix).
* **`site`** — site-design attributes (parking, bicycle, loading).
* **`contextual`** — joined from external datasets (zone code,
  heritage overlay, lot area, corner-lot flag).

## Adding a new attribute

1. Append an entry to `taxonomy.yaml`. Required fields: `id`,
   `category`, `value_type`, `description`. Optional but encouraged:
   `unit`, `extraction_difficulty`, `derivation`, `bylaw_tag_keywords`.
2. Run `pytest tests/test_taxonomy.py` — the loader is strict; any
   missing field, unknown enum value, or duplicate id is a fixture
   error caught at parse time.
3. If the new attribute should retroactively tag existing bylaw
   clauses, re-run the enrichment pass against the affected
   jurisdiction.

## Out of scope for v1

* Subjective attributes ("compatible with neighbourhood character")
  — handled by human review, never tagged here.
* Conditional rule modelling — the taxonomy carries one ID per
  *measured* attribute; bylaws encode the conditions (e.g. corner-lot
  setback rules condition on `corner_lot_boolean`).
* Multi-version evaluation — the file ships with a single `version`.
  A future change will pin `approval_decision.taxonomy_version` so
  re-evaluation against a newer taxonomy is observable.
