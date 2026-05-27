---
name: competitive-monitor
description: >
  Competitive intelligence agent that analyzes competitors in the municipal
  legal-tech / zoning-intelligence space. Reads the version-controlled
  competitor database in competitive-intel/competitors/, uses web search
  to gather fresh signals (product launches, funding, geographic expansion,
  partnerships, press), updates competitor profiles with new findings, scores
  each competitor on a threat matrix, and generates a dated Markdown report
  in competitive-intel/reports/. Use this whenever the user says "competitive
  analysis", "competitor scan", "run competitive monitor", "threat assessment",
  "market landscape", or any phrase implying competitive intelligence gathering.
  Designed to run at regular intervals (weekly or bi-weekly) as forward
  reconnaissance.
---

# competitive-monitor — competitive intelligence and threat assessment

## Context

- **Competitor database**: `competitive-intel/competitors/*.yaml` — one file per competitor, version-controlled.
- **Config**: `competitive-intel/config.yaml` — our product positioning, analysis dimensions, signal types.
- **Reports output**: `competitive-intel/reports/YYYY-MM-DD.md` — dated analysis reports.
- **Schema validator**: `competitive-intel/schema.py` — Pydantic models for competitor YAML.

Anchor docs:

- [competitive-intel/README.md](../../../competitive-intel/README.md) — database structure and schema reference.
- [competitive-intel/config.yaml](../../../competitive-intel/config.yaml) — our product positioning and analysis framework.

---

## Step 1 — Load current state

Read the configuration and all competitor profiles.

```bash
cat competitive-intel/config.yaml
ls competitive-intel/competitors/*.yaml
```

For each YAML file in `competitors/`, read and parse it. Build an in-memory map: `slug → competitor_data`.

Count competitors by category (`direct`, `adjacent`, `emerging`) and threat level (`high`, `medium`, `low`). Print a brief status line:

```
Loaded N competitors: X direct, Y adjacent, Z emerging.
Last full scan: <most recent last_analyzed date across all files>.
```

---

## Step 2 — Gather fresh signals

For each competitor with `status: active`, search for recent news and updates. Use `WebSearch` to query for:

1. `"<competitor name>" zoning OR bylaw OR "land use" news` — product and market signals.
2. `"<competitor name>" funding OR raised OR investment` — funding signals.
3. `"<competitor name>" partnership OR integration OR expansion` — strategic signals.

For category entries (e.g., "Municipal AI Assistants"), search for the category pattern instead:

1. `municipal AI bylaw assistant Canada 2026` — geographic relevance.
2. `zoning AI tool launch 2026` — new entrants.

**Rate limiting**: pause 2 seconds between searches to be a good web citizen. Cap at 3 searches per competitor and 20 total searches per run.

For each search result that looks relevant, use `WebFetch` to read the page and extract:

- **Date** of the signal.
- **Signal type** (from the canonical list in `config.yaml`).
- **One-sentence summary**.
- **Source URL**.

Skip results older than 90 days unless they represent a major event (funding round, acquisition, geographic expansion) not already recorded in the competitor's `signals` list.

---

## Step 3 — Discover new competitors

Search for entrants we don't already track:

1. `zoning intelligence platform 2026` — new products.
2. `bylaw AI assistant Canada` — Canadian-relevant entrants.
3. `municipal legal tech startup` — emerging players.

For each potentially new competitor found:

- Check if it's already in the database (match by name or URL).
- If genuinely new and relevant to the municipal bylaw / zoning intelligence space, create a new YAML file following the schema in `competitive-intel/README.md`.
- Set `category: emerging` and `threat_assessment.level: low` for newly discovered competitors until the next full analysis.
- Print: `NEW COMPETITOR DISCOVERED: <name> — <one-line description>`.

Cap new competitor discovery at 3 per run to keep the database manageable.

---

## Step 4 — Update competitor profiles

For each active competitor, update their YAML file:

### 4.1 Append new signals

Add any signals discovered in Step 2 to the competitor's `signals` list. Deduplicate by URL — if a signal with the same `source_url` already exists, skip it.

### 4.2 Re-score threat assessment

Re-evaluate each competitor's threat level based on:

- **Current signals**: recent funding, geographic expansion toward Canada, or product feature overlap with ABS.
- **Analysis dimensions** from `config.yaml`: product capability, geographic coverage, technology moat, market position, business model, team/funding.
- **Change since last analysis**: has the competitor gotten stronger, weaker, or stayed static?

Update `threat_assessment.level` and `threat_assessment.rationale` if the assessment has materially changed. Add a signal entry documenting the reassessment.

### 4.3 Update metadata

- Set `last_analyzed` to today's date (ISO format).
- Update any factual fields that have changed (e.g., new funding stage, new jurisdictions, pricing changes).

### 4.4 Write updated YAML

Write each modified competitor file back. Use the `Write` tool to overwrite the file with the updated content. Preserve the existing field order and structure.

---

## Step 5 — Generate threat matrix

Score each competitor across the analysis dimensions defined in `config.yaml`. For each dimension, assign a score from 1-5:

| Score | Meaning |
|-------|---------|
| 1 | No presence / not applicable |
| 2 | Minimal / early stage |
| 3 | Moderate / developing |
| 4 | Strong / established |
| 5 | Market-leading / dominant |

Compute a weighted threat score using the dimension weights from `config.yaml`. Normalize to 0-100.

Build a threat matrix table:

```
| Competitor | Category | Threat | Capability | Geography | Tech Moat | Market | Biz Model | Team | Weighted |
|------------|----------|--------|------------|-----------|-----------|--------|-----------|------|----------|
| ...        | ...      | ...    | X/5        | X/5       | X/5       | X/5    | X/5       | X/5  | XX/100   |
```

Sort by weighted score descending.

---

## Step 6 — Generate report

Create a dated report at `competitive-intel/reports/YYYY-MM-DD.md` with the following sections:

```markdown
# Competitive Intelligence Report — YYYY-MM-DD

## Executive Summary

<3-5 sentences: overall market landscape, key changes since last report,
top threats, and one strategic recommendation.>

## Market Landscape

<Brief overview of the municipal legal-tech / zoning intelligence space.
Trends, tailwinds, headwinds relevant to ABS.>

## Threat Matrix

<Table from Step 5.>

## Competitor Profiles

### <Competitor Name> (threat: high/medium/low)

**Category**: direct/adjacent/emerging
**URL**: <url>
**Last analyzed**: <date>

**What they do**: <description>

**Recent signals**:
- <date> — <type> — <summary> ([source](<url>))

**Threat assessment**: <rationale>

**Key watch triggers**:
- <trigger>

---
<Repeat for each competitor, ordered by threat level then weighted score.>

## Signal Timeline

<Chronological list of all signals across all competitors from the last 90 days.>

| Date | Competitor | Type | Summary |
|------|-----------|------|---------|
| ...  | ...       | ...  | ...     |

## Strategic Recommendations

<3-5 actionable recommendations based on the analysis. Each should be:
- Specific (not generic platitudes)
- Tied to a signal or trend observed in this report
- Framed as "because X, we should Y"
>

## Methodology

This report was generated by the competitive-monitor skill on <date>.
Sources: web search, competitor websites, public databases.
Competitor database: competitive-intel/competitors/
Config: competitive-intel/config.yaml
```

---

## Step 7 — Validate and commit

### 7.1 Validate updated YAML files

Run the schema validator to ensure all competitor files are well-formed:

```bash
.venv/bin/python competitive-intel/schema.py
```

If validation fails, fix the offending file(s) before proceeding.

### 7.2 Commit changes

Stage and commit all changes:

```bash
git add competitive-intel/
git commit -m "[ABS-177] Competitive intelligence update — YYYY-MM-DD

- Scanned N competitors, discovered M new signals
- Threat matrix updated
- Report: competitive-intel/reports/YYYY-MM-DD.md

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

### 7.3 Print summary

Print a brief summary to the conversation:

```
Competitive monitor complete.
- Competitors analyzed: N
- New signals found: M
- New competitors discovered: K
- Threat level changes: [list any changes]
- Report: competitive-intel/reports/YYYY-MM-DD.md
```

---

## Scheduling

This skill is designed to run at regular intervals. Recommended cadence:

- **Weekly**: for active competitive monitoring during growth phases.
- **Bi-weekly**: for steady-state monitoring.
- **Ad-hoc**: when a specific competitive event is detected.

To schedule recurring runs, use the `/schedule` skill:

```
/schedule weekly "run competitive monitor"
```

---

## Common abort branches

| Symptom | Branch |
|---|---|
| No competitor YAML files found | Halt. "No competitors in database. Add competitor files to competitive-intel/competitors/ first." |
| config.yaml missing or malformed | Halt. "competitive-intel/config.yaml is missing or cannot be parsed." |
| WebSearch unavailable | Degrade gracefully: skip Steps 2-3, regenerate report from existing data only, note in the report that web search was unavailable. |
| Schema validation fails after updates | Fix the offending files (likely a YAML formatting issue from the update), re-validate, then commit. |
| More than 5 new competitors discovered | Cap at 3 most relevant. Note the others in the report's executive summary for manual triage. |

---

## What this skill explicitly does NOT do

- **Make strategic decisions.** It presents intelligence; the user decides what to act on.
- **Contact competitors.** All intelligence is from public sources only.
- **Modify application code.** This skill operates only on the `competitive-intel/` directory.
- **Access paid databases or APIs.** All research uses public web search and freely accessible pages.
- **Guarantee accuracy.** Web search results can be outdated or wrong. Findings should be verified before making business decisions.
