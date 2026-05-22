"""Layer 2 compliance: attribute taxonomy + evaluator service.

Phase 1 deliverables (see Linear project "Phase 1 — Bylaw-side prep &
evaluator MCP tool"). Two surfaces collaborate here:

* ``attributes/`` — the attribute taxonomy that defines the contract
  between extraction and evaluation. Bylaw fragments are tagged with
  attribute IDs; submissions are populated with attribute values keyed
  on those same IDs.
* the evaluator service (``evaluator.py``) — given a submission's
  attributes and a location, retrieves the bylaw clauses that regulate
  each attribute, extracts numeric constraints, and produces a
  per-attribute pass/fail/uncertain compliance matrix.

Storage is in the new ``parcel`` / ``submission`` / ``submission_attribute``
/ ``approval_decision`` tables added by migration ``0014_compliance_schema``.
"""
