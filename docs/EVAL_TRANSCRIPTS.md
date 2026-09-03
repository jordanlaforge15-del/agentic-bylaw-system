# Eval transcripts: what they guarantee, and how to RCA a failing case

`scripts/run_test_prompts.py` drives the Regional Centre prompt corpus
through `POST /v1/chat` and writes one JSON transcript per case under
`evals/runs/<run>/TC-NNN.json`. `scripts/verify_run.py` grades them —
one command, golden tier (gating) first and generated tier (advisory)
second. This doc covers the part that matters when a case *fails*: what
the transcript promises about the advisor's tool activity, and how to
read it.

## The version ladder

Every transcript carries a `parser_version`. It exists because the
meaning of `tool_calls` has changed twice, and a consumer that asserts on
that field has to know which promise it is holding. Gate on the stamp —
never on a run-directory allowlist, which goes stale on every run.

| `parser_version` | What `tool_calls` guarantees |
| --- | --- |
| absent | Nothing. Harvested from the synthetic SSE content stream, which structurally cannot carry `tool_use` blocks, so it reads `[]` no matter what the loop dispatched. Read `tool_loop_metrics` instead. (Pre-ABS-459.) |
| `2` | The calls the loop really dispatched, with each one's name, error state and latency — **and nothing else**. `input` is always null and there is no result. (ABS-459.) |
| `3` | Everything v2 promises, plus each call's `input` and a bounded record of its result. (ABS-517.) |

Version 3 is a pure superset of 2: every v2 field keeps its meaning, so a
v2-era consumer reads a v3 transcript unchanged, and a v3-era consumer
reads a v2 transcript as "payloads not recorded" rather than as an error.
Pre-v3 runs cannot be backfilled — the advisor never emitted the data.

## The v3 fields

Each entry in a turn's `tool_calls`:

| Field | Meaning |
| --- | --- |
| `name` | The tool that ran. |
| `is_error` | Whether the handler raised. |
| `latency_ms` | Handler wall time, excluding the gateway round trip. |
| `input` | The arguments the model passed, with long string values truncated. `null` **only** if the advisor predates ABS-517; `{}` means genuinely called with no arguments. |
| `result_excerpt` | Head of the handler's output — or its error text when `is_error`. `null` when the advisor has result capture switched off. |
| `result_chars` | Full output length *before* truncation, so you know how much the excerpt is hiding. |
| `result_truncated` | Whether the excerpt is a prefix. |
| `result_citations` | Every citation the result named, in result order (i.e. retrieval rank). |

## Why the payloads exist: retrieval gap vs synthesis gap

An eval case usually fails by omitting one specific fact — a lot-coverage
maximum, a footprint cap, a stepback. That single symptom has two
possible causes, and they are fixed in **different layers**:

1. **Retrieval gap** — the provision never came back from the tool. Fix
   indexing or query construction.
2. **Synthesis gap** — the provision came back and the answer dropped it.
   Fix the prompt or the tool loop.

A v2 transcript cannot tell them apart: it says `search_bylaw_evidence`
ran 33 times and stops there. Guessing sends work to the wrong layer, so
ABS-517 made the transcript answer the question directly.

### The lookup

```python
import json

doc = json.load(open("evals/runs/<run>/TC-024.json"))
assert doc["parser_version"] >= 3, "pre-ABS-517 run — payloads not recorded"

retrieved = {
    citation
    for turn in doc["turns"]
    for call in turn["tool_calls"]
    for citation in call["result_citations"]
}
print("s. 333(1)(a)" in retrieved)
```

`True` → the provision was retrieved and the answer dropped it: a
**synthesis gap**. `False` → it never came back: a **retrieval gap**, and
the calls' `input` fields show which queries were tried, so you can tell
a bad query from a bad index.

`result_citations` rather than `result_excerpt` is the right field for
this test. The excerpt is head-truncated, and a 50-match search response
runs to tens of kilobytes — a provision ranked twentieth would fall
outside it and read as "never retrieved". The citation index covers the
whole result, so the bound on the excerpt stays safe.

Order in `result_citations` is retrieval rank. "Returned but ranked last"
is a different diagnosis from "returned first", and both differ from
"never returned".

## Size knobs

The payloads ship on every turn's SSE stream, so both bounds are tunable
on the advisor process without a redeploy of the runner:

| Env var | Default | Effect |
| --- | --- | --- |
| `ADVISOR_TOOL_RESULT_EXCERPT_CHARS` | `4000` | Chars of handler output kept per call. `0` switches result capture off entirely — `result_excerpt` goes null while `result_chars` still ships, so "disabled" stays distinguishable from "the tool returned nothing". |
| `ADVISOR_TOOL_INPUT_VALUE_CHARS` | `500` | Cap per string value inside a captured input. Structure is preserved, only string leaves are cut. |

A run whose transcripts carry no inputs at all is not diagnosable, and it
otherwise looks perfectly healthy. The runner prints a warning at the end
of such a run rather than letting it be discovered later by whoever opens
the transcript.

## Where the code lives

* `scripts/run_test_prompts.py` — the runner; `extract_turn_artifacts`
  harvests the turn, `TRANSCRIPT_PARSER_VERSION` stamps the guarantee.
* `src/advisor/llm/base.py` — `ToolCallMetric`, the wire shape.
* `src/advisor/chat/tool_payloads.py` — the size bounds and the citation
  index.
* `src/advisor/chat/session.py` — builds the `tool_loop_metrics` event
  once, for both the blocking and streaming entry points.
* `web/e2e/functional/abs517-tool-payload-capture.spec.ts` — asserts the
  payloads survive the real SSE stream, and that committed v3 transcripts
  honour the guarantee.
