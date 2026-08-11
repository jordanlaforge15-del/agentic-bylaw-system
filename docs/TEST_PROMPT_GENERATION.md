# Regional Centre Bylaw Test Prompt Generation

This document describes the design, schema, and reproduction process for the Regional Centre bylaw test prompt suite defined in `evals/regional_centre_test_prompts.json`.

---

## Overview

The test prompt database simulates real end-user conversation turns with the bylaw AI assistant. Each test case encodes a persona, a geographic context (address + zone), a complexity level, a liability level, and a set of multi-turn user messages that an agent or Playwright spec can replay against the live app.

**Current database**: `evals/regional_centre_test_prompts.json`

**Query tool**: `scripts/query_test_prompts.py`

**Generation script**: `scripts/generate_regional_centre_test_prompts.py`

**Skill (agentic orchestration)**: `.claude/skills/generate-test-prompts/SKILL.md`

---

## Provenance limitation — read this before quoting a pass rate

**These cases are not ground truth.** `generate_regional_centre_test_prompts.py`
authors every field of a case via `claude -p`: the question, the persona, the
`expected_answer_keywords`, the `expected_bylaw_references`, the
`expected_topics`. The system under test is a Claude model.

So a case that passes establishes that the advisor **agrees with what a Claude
model guessed the answer was**. It does not establish that the answer is
correct under the by-law. "18/20 passing" is a consistency measure, not an
accuracy measure, and it must never be reported as one.

Later work grounded parts of a case without changing that: the references
resolve against the real corpus (ABS-463), the keywords were recalibrated
against the real ingest (ABS-265), cited clauses are checked for applicability
against the by-law's own trigger conditions (ABS-462), and the address now
derives from the zoning schedule and is verified against it (ABS-467). None of
those touch the question a model chose to ask or the answer a model decided was
correct.

The independent tier is the golden subset at
[`evals/golden/golden_cases.json`](../evals/golden/golden_cases.json): six cases
whose correct answers and governing provisions a qualified human records, graded
by `scripts/verify_golden_cases.py` into `verification/GOLDEN_SUMMARY.json`. It
is the only evidence in the project that does not originate from a model, it is
what blocks a production deploy, and its results are **never** summed with the
generated ones. Rationale and the gating decision:
[ABS-468-EVAL-GROUND-TRUTH.md](ABS-468-EVAL-GROUND-TRUTH.md).

What the generated suite is genuinely good for: regression detection (did an
answer that used to cite Section 198 stop doing so?), hallucination and
applicability checks (both graded against the corpus, not against the
generator), retrieval and cost measurement, and breadth of coverage no
professional has time to hand-author. That is worth having. It is just not a
correctness measure.

---

## Test Case Schema

Each record in the JSON array has the following fields:

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Sequential identifier: `TC-NNN` |
| `title` | string | Short human-readable title |
| `persona.type` | string | Persona type (see valid values below) |
| `persona.subtype` | string \| null | Sub-role (e.g. `architect`, `planner`) |
| `persona.description` | string | One-line description of the individual |
| `address` | string | Full civic address, **derived from `zone`** and verified to resolve to it (ABS-467) |
| `address_resolution` | object | Evidence the address was verified against: `resolved_zone`, `resolution_quality`, `location_type`, `location_confidence`, `location_resolver`, `parcel_pid` |
| `zone` | string | Zone code the schedule actually maps (e.g. `ER-2`, `CEN-1`). Not `ER-1` — the by-law defines it but no polygon carries it |
| `complexity` | `simple` \| `medium` \| `complex` | Complexity of the scenario |
| `liability` | `low` \| `medium` \| `high` | Liability level driven by project scale and risk |
| `tags` | string[] | Queryable scenario tags |
| `bylaw_features` | string[] | Bylaw parameters exercised by this test case |
| `turns` | Turn[] | Ordered list of conversation turns (user messages only) |
| `expected_bylaw_references` | string[] | Bylaw sections/tables the answer should cite |
| `expected_answer_keywords` | string[] | Key phrases that should appear in correct advisor responses |
| `expected_topics` | string[] | High-level topics the response must address |
| `notes` | string | Rationale, edge cases, and design intent |

### Turn object

```json
{
  "turn": 1,
  "role": "user",
  "message": "The user's message text..."
}
```

Note: Only `"user"` role turns are stored. The assistant's responses are not pre-scripted — this is a behavioural test, not a scripted replay.

---

## Valid Enumeration Values

### Zones (from bylaw Section 30)

| Code | Full Name |
|------|-----------|
| CEN-1 | Centre 1 Zone |
| CEN-2 | Centre 2 Zone |
| COR | Corridor Zone |
| DD | Downtown Dartmouth Zone |
| DH | Downtown Halifax Zone |
| ER-1 | Established Residential 1 Zone |
| ER-2 | Established Residential 2 Zone |
| ER-3 | Established Residential 3 Zone |
| HR-1 | Higher Order Residential 1 Zone |
| HR-2 | Higher Order Residential 2 Zone |
| INS | Institutional Zone |
| RPK | Regional Park Zone |

### Persona Types

| Value | Description |
|-------|-------------|
| `homeowner` | Property owner managing their own home |
| `real_estate_developer` | Developer pursuing a project for profit |
| `real_estate_law_professional` | Lawyer conducting due diligence or advising clients |
| `realtor` | Agent advising buyers/sellers on development potential |
| `architectural_consultant` | Architect, planner, or drafter working on a design |
| `city_agent` | Municipal staff reviewing permits or applications |

### Complexity Levels

| Value | Turns | Bylaw parameters | Description |
|-------|-------|-----------------|-------------|
| `simple` | 1–2 | 1 | Single parameter lookup (e.g. one setback) |
| `medium` | 3–4 | 2–3 | Multiple parameters, some cross-referencing |
| `complex` | 5–6 | 4+ | Full feasibility sweep including overlays, multiple zones or uses |

### Liability Levels

| Value | Examples |
|-------|---------|
| `low` | Tree placement, deck addition, small accessory structure |
| `medium` | Secondary suite conversion, duplex, due diligence advisory |
| `high` | Multi-unit residential tower, heritage demolition, 100+ unit development |

### Bylaw Features

| Feature key | Description |
|-------------|-------------|
| `setbacks` | Front, side, or rear setback standards |
| `rear_setback` | Rear setback specifically |
| `side_setback` | Side setback specifically |
| `height` | Maximum building height (Table 5) |
| `height_overlay` | Height governed by Schedule 15 precinct map |
| `lot_coverage` | Maximum lot coverage percentage |
| `FAR` | Floor area ratio |
| `FAR_overlay` | FAR governed by Schedule 17 precinct map |
| `parking` | Off-street parking requirements |
| `use_permission` | Permitted/not-permitted use lookup |
| `combination_of_uses` | Section 49 combination rules for ER zones |
| `development_permit` | Whether a development permit is required |
| `development_permit_exemption` | Section 9 exemptions |
| `heritage_overlay` | Schedule 22 Heritage Conservation District rules |

### Tags

Tags are freeform strings used for thematic grouping. Established tags:

`renovation`, `new_construction`, `residential`, `commercial`, `mixed_use`, `multi_unit`, `secondary_suite`, `high_rise`, `downtown`, `heritage`, `corridor`, `institutional`, `due_diligence`, `redevelopment`, `feasibility`, `accessory_structure`, `accessory_dwelling`, `demolition`, `overlay`, `conversion`

---

## Geographic Scope

All addresses must be within the **Regional Centre Plan Area**: the Halifax Peninsula and Dartmouth inside the Circumferential Highway (bylaw Section 2).

Do not pick a street from a list and hope. The plan area is not the constraint
that bit — being in the *zone* is, and a plausible peninsula address is exactly
how 17 of the first 20 cases ended up in the wrong zone or in no zone at all.
The generator derives the address from the zone and verifies it through the
production `get_address_profile` path; see
[ABS-467-EVAL-ADDRESS-DERIVATION.md](ABS-467-EVAL-ADDRESS-DERIVATION.md).

Use `--on-street` when the scenario leans on a particular street (an arterial,
a transit corridor, a viewplane). It biases which real address is chosen; it
cannot override the verification, and the search falls back to the whole zone
when that street carries no parcel in it.

---

## Design Principles

When creating new test cases:

1. **Zone coverage first** — ensure every zone has at least one case. Priority gaps: INS, RPK.
2. **Persona rotation** — distribute cases across all six persona types.
3. **Complexity balance** — target ~20% simple, ~40% medium, ~40% complex.
4. **Liability gradient** — at least two cases per level.
5. **Feature coverage** — every bylaw feature should appear in at least one case.
6. **Realistic escalation** — conversations should start with a simple question and deepen. Each turn must advance the inquiry.
7. **Persona voice** — homeowners are informal; developers are business-focused; lawyers are precise; architects are technical; city agents are procedural.
8. **Bylaw accuracy** — `expected_answer_keywords` and `expected_bylaw_references` must match the bylaw fixture in `tests/fixtures/halifax_regional_centre_lub.txt`.

---

## Querying the Database

The query tool supports filtering by any combination of fields:

```bash
# All simple homeowner cases
python scripts/query_test_prompts.py --complexity simple --persona homeowner

# All cases covering FAR in CEN zones
python scripts/query_test_prompts.py --bylaw-feature FAR

# All high-liability cases, full JSON output
python scripts/query_test_prompts.py --liability high --output json

# Show conversation turns for a specific zone
python scripts/query_test_prompts.py --zone ER-1 --output turns

# Get just the IDs for scripting
python scripts/query_test_prompts.py --zone CEN-1 --output ids

# Discover valid filter values
python scripts/query_test_prompts.py --list-zones
python scripts/query_test_prompts.py --list-tags
```

For programmatic use (e.g. in a Playwright spec or Python test):

```python
import json, subprocess

result = subprocess.run(
    ["python", "scripts/query_test_prompts.py", "--zone", "ER-1", "--output", "json"],
    capture_output=True, text=True, check=True
)
cases = json.loads(result.stdout)
```

Or directly from Python without spawning a subprocess:

```python
import json
from pathlib import Path

prompts = json.loads(Path("evals/regional_centre_test_prompts.json").read_text())
er1_cases = [p for p in prompts if p["zone"] == "ER-1"]
```

---

## Generating New Test Cases

### Script mode (single case)

```bash
python scripts/generate_regional_centre_test_prompts.py \
  --zone INS \
  --persona city_agent \
  --subtype building_official \
  --complexity medium \
  --liability medium \
  --tags institutional setbacks development_permit \
  --bylaw-features setbacks development_permit \
  --title "Building official reviewing institutional setbacks in INS" \
  --turns 3 \
  --append
```

There is no `--address`. The zone picks a real parcel, the parcel yields a real
civic address, and that address is resolved back through `get_address_profile`
before the case is written — so a new case cannot reintroduce the zone/address
mismatch ABS-467 fixed. This needs `DATABASE_URL` pointing at a database with
the HRM zoning and parcel datasets, and `GOOGLE_MAPS_API_KEY`.

Messages come from `claude -p` (billed to the Claude Code subscription). If the
`claude` binary is missing, the script produces stub messages to fill in
manually.

### Batch mode

Create a spec file `scripts/test_prompt_specs_new.json` as a JSON array of spec
objects (see the script docstring for the schema), then:

```bash
python scripts/generate_regional_centre_test_prompts.py \
  --spec-file scripts/test_prompt_specs_new.json \
  --append
```

### Agentic orchestration

For large-scale expansion (covering many new zones or personas at once), use the `generate-test-prompts` skill. It audits coverage gaps, designs specs for all missing combinations, calls the generation script, validates results, and commits. See `.claude/skills/generate-test-prompts/SKILL.md` for the full step-by-step instructions.

---

## Validation Checklist

Before committing new test cases:

- [ ] All required fields present (`id`, `title`, `persona`, `address`, `address_resolution`, `zone`, `complexity`, `liability`, `tags`, `bylaw_features`, `turns`, `expected_bylaw_references`, `expected_answer_keywords`, `expected_topics`, `notes`)
- [ ] `python scripts/verify_eval_address_zones.py --check` passes — every address resolves to its case's zone
- [ ] `address_resolution.resolution_quality` is `rooftop`, or the notes say why it is not
- [ ] No `[STUB]` placeholder messages remain in `turns`
- [ ] `expected_bylaw_references` match real section/table labels in the bylaw fixture
- [ ] `id` values are unique and sequential
- [ ] Turn-1 message is specific enough to identify the zone and scenario without the metadata
- [ ] `zone` value is one of the 12 valid codes from bylaw Section 30
- [ ] Address is plausibly within the Regional Centre Plan Area

Run the validation snippet from the skill doc to check programmatically.

---

## Test Execution

The Playwright spec at `web/e2e/functional/abs203-test-prompts.spec.ts` provides automated coverage for this feature. It:

1. Verifies the JSON database loads and passes schema validation.
2. Confirms all 10 base test cases are present with required fields.
3. Exercises the Python query tool for each filter dimension and checks results.
4. Runs one simple single-turn test case against the live advisor chat to confirm end-to-end prompt delivery.

Run it with:

```bash
export PG_PORT=5433 E2E_FASTAPI_PORT=8002 E2E_WEB_PORT=3002
export E2E_API_URL=http://127.0.0.1:8002 E2E_BASE_URL=http://localhost:3002
cd web && npx playwright test e2e/functional/abs203-test-prompts.spec.ts
```

---

## Reproduction Steps

To reproduce the initial 10-case suite from scratch:

1. Read `tests/fixtures/halifax_regional_centre_lub.txt` to understand zones, use permissions, dimensional standards, and overlays.
2. Map the 10 scenarios to zone × persona × complexity combinations ensuring coverage breadth.
3. Write multi-turn user messages grounded in the bylaw text (or use `generate_regional_centre_test_prompts.py` with `ANTHROPIC_API_KEY`).
4. Populate `expected_bylaw_references` and `expected_answer_keywords` from the bylaw fixture.
5. Run the validation checks above.
6. Commit.

To add more cases in future, follow the **Generating New Test Cases** section above, then run the Playwright spec to confirm the query tool and schema remain valid.
