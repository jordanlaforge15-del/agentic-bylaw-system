# Agentic Intake Pipeline — Current Limitations

_Last updated: 2026-05-24. Reflects the system after Halifax Mainland LUB onboarding (`document_id=5` in dev) and the subscription-billing migration ([[ABS-107]] / [[ABS-109]])._

## Scope

This doc covers the full path a new municipality's bylaw travels from a URL to queryable data in the dev DB, and what stops that path from being trustworthy today.

| Stage | Code | Purpose |
| --- | --- | --- |
| Discovery | `abs-learning/src/agents/discovery.py` | Crawl a municipal site, pick the primary normative bylaw artifact |
| Structure analysis | `abs-learning/src/agents/structure_analyst.py` | LLM derives a `ParserConfig` from sampled PDF windows |
| Semantic mapping | `abs-learning/src/agents/semantic_mapper.py` | LLM proposes a citation scheme + entity mapping |
| Validation | `abs-learning/src/agents/validation.py` | Sanity checks before handoff |
| Layer 1 ingest | `layer1 ingest` | Parse PDF into blocks / fragments / tables using the manifest |
| Layer 1 enrichment | `layer1 enrich-semantics` | Regex extraction of semantic facts |
| Audit | `layer1 audit-pages [--llm]` | Read-only quality probe |

## What works today

- Full chain produces a usable `ParserConfig` for two HRM bylaws (Regional Centre LUB, Mainland LUB).
- Discovery correctly picks the primary normative document on HRM-style municipal sites after [[ABS-89]] / [[ABS-92]] / [[ABS-93]].
- StructureAnalyst converges on a citation scheme in ~11 min wall clock for a 222-page PDF.
- Subscription-billed LLM calls work end-to-end ([[ABS-107]] / [[ABS-109]]): $0 marginal cost vs the prior ~$25/run on the Anthropic API.
- Layer 1 ingest produces complete fragment trees for both HRM LUBs (Mainland: 2 763 fragments / 49 tables / 222 pages / 2 870 blocks).
- Regex enrichment produces hundreds of semantic facts per bylaw after the Mainland-driven extractor fixes ([[ABS-103]] / [[ABS-104]] / [[ABS-105]] / [[ABS-106]]) — Mainland: 258 facts, 110 cross-references.
- `audit-pages --llm` runs cleanly against the subscription backend and surfaces real Layer 1 issues.

## Limitations

### Discovery

- **Coverage assumed, not verified.** Heap-based BFS with three priority tiers ([[ABS-93]]) picks the most likely bylaw URLs first, but there is no completeness check. A site that buries the bylaw behind an unusual link structure, JavaScript navigation, or paywall will silently return an incomplete candidate set.
- **eTLD+1 host filter is binary.** A bylaw hosted on a sibling subdomain inside the same eTLD+1 will be crawled; a bylaw hosted on a *different* eTLD+1 (e.g. a consulting-firm-managed PDF host) will not, unless `allowed_hosts` is overridden. No automatic detection.
- **CMS-routed PDF detection relies on a content-type check.** Works for sites that serve `application/pdf` regardless of URL extension ([[ABS-92]]). Fails silently for sites that wrap PDFs in HTML viewer pages.

### Structure analysis (learning agents)

- **Prompt cache doesn't propagate across the subscription subprocess boundary.** Each `claude -p` invocation is a fresh process, so the per-window prompt cache wired in [[ABS-94]] only helps within one subprocess. The cost impact is real for any bylaw needing many windows.
- **Cross-window majority-vote reconciliation can vote wrong.** [[ABS-90]] reconciles disagreements between window-level proposals by simple majority. Bylaws with mixed citation schemes (rare but seen — appendices with their own numbering) can vote the wrong scheme through.
- **JSON schema enforcement is best-effort.** `claude -p --json-schema` validates the response; if it fails to conform, the shim retries 3× with a repair prompt and then raises `ClaudeCodeBackendError`. No graceful fallback — a stubbornly malformed response simply fails the run, with no salvage of partial progress.
- **No persistence of partial progress.** A timeout, network blip, or schema-repair-loop exhaustion mid-run loses all completed window results.

### Layer 1 ingest

- **Table extraction is fragile on amendment-history layouts.** On Mainland LUB pages 200–222, multi-column tables are shattered into per-cell heading fragments and ~50–60 % of cell content is dropped. Content is non-normative (audit-trail metadata) so it doesn't affect retrieval — but it surfaced because the audit sampler [[ABS-115]] over-weighted those pages.
- **Address numbers misclassified as section labels.** `348 Purcell's Cove Road` produces a phantom section `348`. Likely rare across substantive content but unmeasured.
- **No re-ingest path that preserves curation.** A re-ingest blows away the fragment graph. Any downstream review decisions would be lost — relevant once a feedback loop exists (see below).

### Layer 1 enrichment (regex)

- **One-shot regex, no LLM pass.** Anything the regexes don't match is invisible. Recent extractor extensions ([[ABS-103]] / [[ABS-104]] / [[ABS-105]] / [[ABS-106]]) closed the Mainland-specific gaps but every new bylaw is a new puzzle, and each gap is found only when an audit happens to flag it or a user complains.
- **Subsection citation labels collapse to parent.** `62EE(1)..62EE(7)` all share `citation_label='62EE'` with `citation_path=NULL`, breaking cross-reference resolution for subsections. Filed as [[ABS-116]]. Same shape likely affects other `<NN>(<n>)` patterns; scope unmeasured.
- **Prose conditions only matched via marker chars or fallback heuristic.** [[ABS-106]] added a heuristic for bylaws without explicit marker chars (semicolons, "provided that", etc.); coverage on bylaws unlike Mainland is unmeasured.

### Audit

- **Sampler biased toward non-normative pages.** Risk scorer weighs raw uncertain-fragment counts, so amendment-history and schedule tables consistently dominate the top-N. Filed as [[ABS-115]]. Until this lands, larger `--sample` values mostly yield more amendment-history pages, not more substantive coverage.
- **Audit is read-only.** Produces a JSON report; nothing in the system can act on its findings. They become Linear tickets, not data updates (see feedback loop gap below).
- **DB session held idle across LLM calls.** `audit_document_pages` loads all snapshots upfront then keeps the SQLAlchemy session open during the LLM loop. With the default `idle_in_transaction_session_timeout=60 000 ms` from `docker-compose.yml`, any sample beyond ~1 LLM page crashes the run with `psycopg.errors.IdleInTransactionSessionTimeout`. Filed as [[ABS-114]]. Workaround: `PG_IDLE_IN_TXN_TIMEOUT=1800000 docker compose up -d postgres` before the run.
- **`--pages` accepts only single integers, not ranges.** Filed as [[ABS-117]].
- **Default `--sample 5` is low** for a 222-page bylaw, but bigger samples burn subscription quota and (per [[ABS-115]]) don't necessarily audit substantive content. The real fix is the scorer, not the default.

### Feedback loop (absent)

The pipeline today has **no mechanism to revise data after the one-shot ingest + enrich**:

- No LLM second-pass extraction. Audit's LLM call is read-only.
- No human-in-the-loop reviewer UI. `semantic_review_event` table exists in schema but nothing writes to it.
- Output-side feedback (`answer_feedback`, `claim_feedback`, `retrieval_feedback`) is collected from advisor users but doesn't propagate back to canonical Layer 1 tables.

Scoped as the [Bylaw Data Quality Loop](https://linear.app/agenticbylawsystems/initiative/bylaw-data-quality-loop-d66abb86c334) initiative.

## Operational

- **Layer 2 RAG and the Advisor still bill the Anthropic API**, not subscription. [[ABS-111]] (Layer 2) and [[ABS-112]] (Advisor scoping) outstanding. Only Layer 1 audit + learning pipeline are subscription-billed today.
- **June 15 2026 Agent SDK credit-tier change** is unverified for the Max plan — [[ABS-113]] tracks pre-cutover verification. If headless quota becomes its own bucket and is small, the learning pipeline + audit costs return.
- **DB transaction timeouts were hardcoded in `docker-compose.yml`** until [[ABS-100]] parameterised them. The default 60 s still bites the audit ([[ABS-114]]).
- **Parallel worktree runs require manual port-triplet management** and an explicit `DATABASE_URL` export until [[ABS-69]] lands.
- **Stale `OPENAI_API_KEY` / `AUDIT_LLM_MODEL` lines in `.env.example`** post-[[ABS-109]]. Filed as [[ABS-110]].

## Open issues snapshot

| Issue | Priority | Title |
| --- | --- | --- |
| [ABS-69](https://linear.app/agenticbylawsystems/issue/ABS-69) | — | `dev-setup` ships only `[dev]` extras + `DATABASE_URL` not exported |
| [ABS-100](https://linear.app/agenticbylawsystems/issue/ABS-100) | — | Parameterise Postgres timeouts in docker-compose (landed) |
| [ABS-110](https://linear.app/agenticbylawsystems/issue/ABS-110) | Low | Clean up stale OpenAI vars in `.env.example` |
| [ABS-111](https://linear.app/agenticbylawsystems/issue/ABS-111) | Medium | Migrate Layer 2 LLM client to Claude Code |
| [ABS-112](https://linear.app/agenticbylawsystems/issue/ABS-112) | Medium | Design discussion: Advisor on subscription |
| [ABS-113](https://linear.app/agenticbylawsystems/issue/ABS-113) | Medium | Verify Max-plan Agent SDK credit allocation before 2026-06-15 |
| [ABS-114](https://linear.app/agenticbylawsystems/issue/ABS-114) | Medium | `audit-pages --llm` holds DB session idle across LLM calls |
| [ABS-115](https://linear.app/agenticbylawsystems/issue/ABS-115) | Medium | Audit risk scorer over-weights non-normative pages |
| [ABS-116](https://linear.app/agenticbylawsystems/issue/ABS-116) | Medium | Subsection citation labels collapse: `62EE(1)..(7)` share label |
| [ABS-117](https://linear.app/agenticbylawsystems/issue/ABS-117) | Low | `audit-pages --pages`: accept ranges like `10-25,30-35` |

## Suggested closing order

The smallest set of changes that would make the pipeline trustworthy for a new municipality, in priority order:

1. **[[ABS-115]]** — audit attention follows actual normative content. Required before any larger audit run is useful. Without this, `--sample 5` is a coin flip and `--sample 25` mostly returns amendment-history pages.
2. **[[ABS-114]]** — audit doesn't crash at scale. Unblocks running a 50+ page sample without `PG_IDLE_IN_TXN_TIMEOUT` workarounds.
3. **[[ABS-116]]** — subsection retrieval is correct. Direct user-visible advisor impact.
4. **Bylaw Data Quality Loop, Stream 1** (LLM second-pass enrichment) — largest single multiplier on coverage for any new municipality. Removes the "new bylaw → file regex extension → re-run enrichment" loop.
5. **Bylaw Data Quality Loop, Stream 2** (reviewer UI) — operationalises everything the audit currently can only report.

Below that priority, the remaining issues are ergonomics ([[ABS-117]]), known-cost cleanups ([[ABS-110]] / [[ABS-69]]), or future-looking ([[ABS-111]] / [[ABS-112]] / [[ABS-113]]).
