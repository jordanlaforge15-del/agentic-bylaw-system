# ABS-466 — how much work sits on a weak address resolution?

Measured 2026-08-11 with `scripts/geocode_confidence_exposure.py`, which
buckets every linked `geocode_cache` row with the same classifier the runtime
uses (`layer2.retrieval.resolution_quality.classify_resolution`) and then
looks for `answer_log` rows and advisor cases naming one of the weak
addresses. The script is read-only; re-run it any time with
`DATABASE_URL=… .venv/bin/python scripts/geocode_confidence_exposure.py`.

## Production (`bylaw-postgres` on bylaw-prod)

| metric | value |
|---|---|
| `geocode_cache` rows | 2 |
| rooftop (0.95) | 2 |
| below rooftop | **0** |
| `answer_log` rows | 0 |
| advisor cases | 17 (none anchored on a weak resolution) |

The two cached addresses are `6521 Bayer's Road` and `5571 Fenwick Street`,
both `google_maps` at 0.95 — ROOFTOP quality. **No production user has been
given an answer built on an interpolated or centroid resolution**, so there is
nothing to notify anyone about and no follow-up issue is warranted on
production data. (The 17 cases exist but none of their anchors appear in the
weak set, because the weak set is empty.)

## Local dev database (`layer1` on the dev Postgres)

| metric | value |
|---|---|
| `geocode_cache` rows | 36 |
| rooftop | 19 |
| interpolated (0.85) | 13 |
| centroid (0.6) | 3 |
| not linked | 1 |
| below rooftop | **16** |
| `answer_log` rows on a weak address | 0 |
| advisor cases anchored on a weak address | 2 |

The centroid rows are exactly the ones the issue cites — `567 Windsor Street`,
`89 Jubilee Road`, plus `200 Bayers Road` — all `status=linked` at 0.6, which
is what the `confidence < 0.6` guard admitted by equality. The interpolated
set includes `1234 Oxford Street` and `100 Robie Street`, also as the issue
describes. The two dev advisor cases (`1489 South Park Street`,
`2100 Gottingen Street`) are development/eval traffic, not real users.

## Conclusion

The defect was real and reproducible, but its blast radius never reached
production: prod's cache contains only rooftop matches. The exposure lives in
dev/eval data, which is also where the eval-case address defect (filed
separately) was found. No user-notification follow-up is being opened.

Worth re-running this script after any production traffic spike, and worth
re-running it before assuming the same holds — with GEOMETRIC_CENTER now
rejected for civic addresses, a would-be 0.6 match returns a refusal (which
the model can act on) instead of a confident wrong zone, so the count should
stay at zero by construction.
