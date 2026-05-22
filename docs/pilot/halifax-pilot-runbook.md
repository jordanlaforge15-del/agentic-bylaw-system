# Halifax Phase-2 pilot runbook (ABS-54)

This is the operator-facing runbook for the Phase-2 pilot: 3 real
Halifax projects with one cooperative architect or developer. The
Phase-2 stack — IFC ingest, derived setbacks/coverage/FAR, APS hook,
upload UI, compliance matrix — is live on `phase-2-bim-ingestion` and
ready for a real customer the moment one is secured.

This document is **not** a substitute for the pilot itself. It exists
so the pilot doesn't get freelanced — each project is run the same
way, captures the same evidence, and rolls into the scorecard at
[`pilot-scorecard.md`](pilot-scorecard.md).

## 1. Customer selection criteria

Re-stated from the issue spec for convenience:

- Halifax-based or Halifax-active.
- Doing 3+ projects per year in zones the bylaw covers (start with
  R-1, R-2, or commercial mixed-use).
- Already comfortable sharing IFC or `.rvt` files (NDA-protected as
  needed).
- Open to giving structured weekly feedback.

Disqualifiers (drop the candidate and move on):

- They want the pilot to be a paid engagement on their terms before
  trust is established. A pilot is a value-discovery exercise; charging
  changes the dynamic and the feedback.
- They can't (or won't) share IFC. The Phase-2 IFC path is the primary
  surface. APS-only customers exist but require credentials we don't
  have yet — wait for ABS-50's follow-up.
- They don't have 3 zoning-relevant projects within 6 weeks.
- Their projects are entirely outside HRM. The geocoding,
  road-centerlines dataset, and `DEFAULT_JURISDICTION_EPSG_MAP` are
  Halifax-tuned. Other cities are Phase 3+ scope.

## 2. Onboarding the customer

### 2a. The outreach email

Use this template as the cold/warm outreach. Keep it short — under 200
words. Don't pitch features; pitch the offer (free pilot in exchange
for structured feedback).

```
Subject: Pilot offer — bylaw-compliance check on 3 of your Halifax projects

Hi [name],

We've built an early-stage tool that reads an IFC export of a building
model, looks up the parcel zoning + setbacks + bylaw clauses for that
address, and produces a one-page compliance check showing which
bylaws each design satisfies (or doesn't).

We're looking for one Halifax architecture or development firm to run
3 of their projects through it as a pilot. In exchange for ~30 minutes
a week for 4–6 weeks (one meeting, your feedback on a printout), you
get:

- Bylaw compliance hints for 3 of your real projects, before you
  spend on a planner consult.
- A direct line to influence what the tool catches and how it shows
  results.
- Free use during and after the pilot.

We sign a short pilot agreement covering NDA / IP / data handling
upfront. We don't keep the IFC files; we extract the building
attributes and discard the source.

Open to a 20-minute intro call this week or next?

[your name]
```

### 2b. The pilot agreement

Use the template at `docs/pilot/pilot-agreement-template.md` (TODO:
write this; legal-reviewed template covering IP, data handling,
no-charge pilot period, mutual cancellation per the issue spec).
**Don't run the first extraction until this is signed.**

### 2c. Account setup

Once signed:

1. Operator creates an advisor account for the customer (the existing
   Clerk allowlist + `/admin/invites` flow).
2. Operator gifts the customer's account 200 starter credits at each
   tier (same `seed_e2e_user.py` recipe, adapted for the prod DB):

   ```bash
   ssh bylaw-prod
   # In the bylaw-postgres container:
   docker exec -it bylaw-postgres psql -U layer1 layer1
   -- inspect / create the user row, top up case_credit table by hand
   -- per `docs/pilot/account-bootstrap.md` (TODO).
   ```

3. Walk the customer through the `/submissions/new` page on a live
   screen-share. Hand off the PIDs of their first project's parcels
   (look them up via the HRM Open Data parcel viewer — the
   `parcel_identifier` column is the PID).

## 3. The per-project workflow

For each of the 3 pilot projects:

### Step 1 — Customer uploads

- Customer exports IFC from their modelling tool (Revit, ArchiCAD,
  Vectorworks). IFC4 schema; metric units.
- Customer goes to `/submissions/new` in the deployed product.
- Customer pastes the parcel PID and uploads the IFC.

### Step 2 — Operator reviews the extracted attributes

- On the customer's `/submissions/{id}` page, sanity-check every
  attribute the extractor produced.
- For any low-confidence row (badge < 90%), ask the customer to
  confirm or override. The override lands as `source=OVERRIDE` and
  preserves the EXTRACTED row in `evidence_json["overridden_from"]`
  for the audit trail.

### Step 3 — Customer runs the evaluator

- Customer clicks "Run evaluator" on the same page.
- Compliance matrix renders. Customer reviews each row.

### Step 4 — Manual ground truth (the actual pilot signal)

- For each project, the customer hand-measures the four setbacks
  (front, rear, side-left, side-right) on their own drawing.
- Operator captures this in the pilot CSV (see schema below).

### Step 5 — Variance report

- Operator runs `scripts/pilot_variance_report.py` against the
  per-project CSV. Output goes into the weekly review.

## 4. Per-project data capture

One CSV per pilot project, named `pilot_<customer-slug>_<project-slug>.csv`,
stored under `docs/pilots/data/` (gitignored — these contain customer
data, do not commit).

CSV columns:

| column | type | example | notes |
|---|---|---|---|
| `submission_id` | int | `42` | from the deployed product |
| `attribute_key` | str | `front_setback_m` | from the Phase-1 taxonomy |
| `manual_value` | float / int / str | `6.0` | customer's hand-measurement / ground-truth value |
| `automated_value` | float / int / str | `5.8` | what the pipeline emitted |
| `unit` | str | `m` | matches the taxonomy unit |
| `confidence` | float | `0.6` | what the pipeline reported |
| `source` | str | `derived` | extracted / derived / manual / override |
| `notes` | str | `"derived from elevation span"` | optional — operator's qualitative annotation |

For each project, capture both setbacks and categorical attributes.
The taxonomy keys to expect on a residential pilot:

- `building_height_m`, `building_height_storeys`
- `gross_floor_area_m2`, `building_footprint_area_m2`
- `lot_coverage_percent`, `floor_area_ratio`
- `front_setback_m`, `rear_setback_m`, `side_setback_left_m`,
  `side_setback_right_m`
- `primary_use_class`, `residential_unit_count`,
  `parking_stalls_count`, `bicycle_stalls_count`
- `corner_lot_boolean`, `arterial_frontage_boolean`

## 5. Weekly check-in agenda

30 minutes, recurring weekly during the pilot. Operator runs the
meeting from this exact agenda — drift wastes the customer's time.

1. **Variance review** (10 min) — pull the latest per-project
   variance report. Walk the customer through any attribute that
   crossed the scorecard thresholds. Decide together: extractor bug,
   taxonomy gap, bylaw-tagging gap, or evaluator logic gap. File the
   follow-up issue under the appropriate phase project before the
   meeting ends.

2. **What broke this week** (10 min) — three open questions for the
   customer:
   - Anything in the matrix that confused you?
   - Anything you'd expect to see that isn't there?
   - If we charged for this tomorrow, what would you pay per project?

3. **Next project ready?** (5 min) — confirm the next project's IFC
   + PID is ready, schedule the next extraction.

4. **Action items** (5 min) — operator writes them down, sends within
   24 hours of the meeting.

## 6. Kill criteria

Drop the customer and find another if any of these trip:

- Customer has not delivered the next project's IFC for two
  consecutive weeks without a credible reason. The pilot is supposed
  to take 4–6 weeks; a 3-month drift signals the customer doesn't
  actually have the projects.
- Customer's projects are consistently outside the scope the
  Phase-1/Phase-2 taxonomy covers (e.g., all interior renovations
  with no zoning surface to evaluate).
- Customer's feedback is "everything is great" with no actionable
  detail across two consecutive weeks. Either the tool is perfect
  (unlikely) or the customer isn't actually using it.

Re-running customer selection from §1 should be quick at this point —
the runbook stays the same.

## 7. Final write-up

After 3 successful projects (or after kill-criteria triggers), write
up findings in `docs/pilots/phase2_pilot_<customer-slug>.md` per the
issue spec. Use [the scorecard](pilot-scorecard.md) for the
quantitative section. Capture the customer's exact wording for any
qualitative claim — paraphrased pilot quotes lose credibility fast.
