# `layer1 learn-city` — Onboarding a new city with the agentic pipeline

`layer1 learn-city` runs the full agentic learning pipeline for a municipality:
**DiscoveryAgent → StructureAnalystAgent → SemanticMapperAgent → ValidationAgent**
and writes a `CityIntakeManifest` JSON file that drives `layer1 ingest --manifest`.

---

## Quick start

```bash
layer1 learn-city \
  --jurisdiction-code HRM-MAINLAND \
  --name "Halifax Regional Municipality" \
  --province "Nova Scotia" \
  --seed-url https://www.halifax.ca/home-property/planning-development/policies-planning-documents/regional-plan/the-plan-for-each-area-of-hrm/halifax-plan-area \
  --output abs-learning/output/HRM-MAINLAND/manifest.json
```

Or via Make:

```bash
make learn-city-hrm-mainland
```

---

## Flags

| Flag | Required | Description |
|------|----------|-------------|
| `--jurisdiction-code` | yes | Short unique code (e.g. `HRM-MAINLAND`) |
| `--name` | yes | Full municipality name |
| `--province` | yes | Province name |
| `--seed-url` | yes* | Municipal website URL to crawl for documents |
| `--direct-pdf` | yes* | Bypass Discovery — use this PDF URL directly |
| `--output` | yes | Path to write the `CityIntakeManifest` JSON |
| `--retrieval` | no | `document-id=N` — use a real layer1 DB retrieval client |
| `--citation-samples` | no | Path to a JSON array of citation strings for validation |
| `--model` | no | LLM model (default: `claude-sonnet-4-6`) |
| `--governing-body` | no | Governing body name |
| `--db-url` | no | Database URL (needed when `--retrieval` is provided) |

\* `--seed-url` and `--direct-pdf` are mutually exclusive; exactly one is required.

---

## Discovery vs. direct PDF

**Discovery** (`--seed-url`): the `DiscoveryAgent` crawls the municipal website
starting from the seed URL, classifies candidate documents via LLM, and returns
a `List[SourceDocument]`. Use this for first-time city onboarding when you don't
know the exact PDF URLs.

**Direct PDF** (`--direct-pdf`): skips discovery entirely. Use this when you
already know the primary bylaw PDF URL — typically on a re-run after discovery
already identified it.

---

## Retrieval and citation validation

Without `--retrieval`, the `ValidationAgent` runs with a stub client that always
returns `None`. This means:

- `citation_resolution_rate` will be **0.0**
- The CLI prints a warning: _"Using stub client. citation_resolution_rate will be
  0.0 (signal is meaningless without a real client)."_
- The manifest may still have `pipeline_ready=True` if zone completeness and
  pattern coverage thresholds pass.

To get a meaningful `citation_resolution_rate`, first ingest the primary PDF:

```bash
layer1 ingest mainland_lub.pdf --municipality "Halifax Regional Municipality" \
    --bylaw-name "Mainland Land Use By-law" --db-url $DATABASE_URL --create-schema
# → Document 4 ingested
```

Then re-run learn-city with `--retrieval`:

```bash
layer1 learn-city \
  --jurisdiction-code HRM-MAINLAND \
  --name "Halifax Regional Municipality" \
  --province "Nova Scotia" \
  --direct-pdf https://cdn.halifax.ca/mainland-lub.pdf \
  --retrieval document-id=4 \
  --output abs-learning/output/HRM-MAINLAND/manifest.json
```

---

## Cost estimate

Each full pipeline run makes approximately **18 LLM calls** (~$2 at
`claude-sonnet-4-6` API rates). The CLI prints this estimate before invoking
the pipeline so you can abort if unexpected.

All calls route through `ClaudeCodeClient` by default, which bills against
your Claude Code subscription rather than the Anthropic API. Pass `--model`
to override the model sent to `claude -p`.

---

## Halifax Mainland LUB — first real-world run (2026-05-23)

The Halifax Mainland LUB was the first city onboarded via the learning pipeline.
The run used a temporary driver script at `/tmp/learn_mainland_lub_v2.py`; all
future onboardings should use `layer1 learn-city` instead.

```bash
layer1 learn-city \
  --jurisdiction-code HRM-MAINLAND \
  --name "Halifax Regional Municipality" \
  --province "Nova Scotia" \
  --seed-url https://www.halifax.ca/home-property/planning-development/policies-planning-documents/regional-plan/the-plan-for-each-area-of-hrm/halifax-plan-area \
  --output abs-learning/output/HRM-MAINLAND/manifest.json
```

Expected outcome: `pipeline_ready=True` with a `QAReport.status` of `PASS`
or `PASS_WITH_FLAGS`. The manifest at
`abs-learning/output/HRM-MAINLAND/manifest.json` can then be passed to
`layer1 ingest --manifest`.

---

## After the manifest is written

Once `pipeline_ready=True`, ingest the primary PDF using the manifest as
the parser configuration driver:

```bash
layer1 ingest mainland_lub.pdf \
  --manifest abs-learning/output/HRM-MAINLAND/manifest.json \
  --municipality "Halifax Regional Municipality" \
  --bylaw-name "Mainland Land Use By-law" \
  --db-url $DATABASE_URL \
  --create-schema
```

See `layer1 ingest --help` for full options.
