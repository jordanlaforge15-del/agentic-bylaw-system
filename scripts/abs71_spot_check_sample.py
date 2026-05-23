"""ABS-71 spot-check sampler.

Pulls a precision sample (stratified across the tagged-attribute
distribution with a floor of 1 per tagged attribute) and a recall
sample (random across parsed fragments) and emits markdown tables
ready for a human verdict column.

Run from worktree root with the prod tunnel up:

    DATABASE_URL=postgresql+psycopg://layer1:<pwd>@localhost:5471/layer1 \
        .venv/bin/python scripts/abs71_spot_check_sample.py
"""
from __future__ import annotations

import json
import os
import random
from collections import defaultdict

from sqlalchemy import select

from layer1.db.base import SourceFragment
from layer1.db.session import session_scope
from layer1.models.enums import ParseStatus


DOCUMENT_ID = 4
PRECISION_N = 50
RECALL_N = 50
SEED = 71  # deterministic so the report can be reproduced

MAX_TEXT_PREVIEW = 220


def _trunc(s: str | None, n: int = MAX_TEXT_PREVIEW) -> str:
    if not s:
        return ""
    s = " ".join(s.split())
    if len(s) <= n:
        return s
    return s[: n - 1] + "…"


def main() -> None:
    rng = random.Random(SEED)

    with session_scope(os.environ.get("DATABASE_URL")) as session:
        all_parsed = (
            session.execute(
                select(SourceFragment)
                .where(SourceFragment.document_id == DOCUMENT_ID)
                .where(SourceFragment.parse_status == ParseStatus.PARSED)
            )
            .scalars()
            .all()
        )

    tagged = [f for f in all_parsed if f.attribute_tags]
    by_attr: dict[str, list[SourceFragment]] = defaultdict(list)
    for f in tagged:
        # Each fragment may carry multiple tags; index it once per tag
        # so the stratified sample sees the right distribution.
        for tag in f.attribute_tags:
            by_attr[tag].append(f)

    # Stratified precision sample: floor of 1 per attribute, then
    # weight remaining quota by tag frequency.
    attrs = sorted(by_attr.keys())
    precision_sample: list[tuple[SourceFragment, str]] = []
    seen_ids: set[int] = set()
    for attr in attrs:
        if len(precision_sample) >= PRECISION_N:
            break
        candidates = [f for f in by_attr[attr] if f.id not in seen_ids]
        if not candidates:
            continue
        chosen = rng.choice(candidates)
        precision_sample.append((chosen, attr))
        seen_ids.add(chosen.id)

    # Fill the remaining quota proportionally to attribute weight.
    weights = [(attr, len(by_attr[attr])) for attr in attrs]
    while len(precision_sample) < PRECISION_N:
        attr = rng.choices(
            [w[0] for w in weights],
            weights=[w[1] for w in weights],
            k=1,
        )[0]
        candidates = [f for f in by_attr[attr] if f.id not in seen_ids]
        if not candidates:
            continue
        chosen = rng.choice(candidates)
        precision_sample.append((chosen, attr))
        seen_ids.add(chosen.id)

    # Recall sample: random across all parsed fragments, independent
    # of tag state.
    rng2 = random.Random(SEED + 1)
    recall_sample = rng2.sample(all_parsed, min(RECALL_N, len(all_parsed)))

    print("## Precision sample (n=%d, stratified by tagged attribute)" % PRECISION_N)
    print()
    print(
        "| # | fragment_id | citation_path | sampled_for_attr | "
        "all_tags | rationale | clause | verdict |"
    )
    print(
        "|---|---|---|---|---|---|---|---|"
    )
    for i, (frag, sampled_for_attr) in enumerate(precision_sample, start=1):
        rationales = (frag.metadata_json or {}).get("attribute_tag_rationales") or []
        rationale_for_attr = next(
            (r["rationale"] for r in rationales if r.get("attribute_id") == sampled_for_attr),
            "",
        )
        print(
            f"| {i} | {frag.id} | {_trunc(frag.citation_path, 80)} | "
            f"{sampled_for_attr} | {','.join(frag.attribute_tags or [])} | "
            f"{_trunc(rationale_for_attr, 180)} | {_trunc(frag.text)} | |"
        )

    print()
    print("## Recall sample (n=%d, random across parsed fragments)" % RECALL_N)
    print()
    print(
        "| # | fragment_id | citation_path | "
        "all_tags | clause | should_be_tagged_as | "
        "in_prefilter? |"
    )
    print("|---|---|---|---|---|---|---|")
    for i, frag in enumerate(recall_sample, start=1):
        in_prefilter = (
            "yes (LLM-rejected)" if (frag.metadata_json or {}).get("attribute_tag_audit")
            else "no (prefiltered out)"
        )
        print(
            f"| {i} | {frag.id} | {_trunc(frag.citation_path, 80)} | "
            f"{','.join(frag.attribute_tags or []) or '(none)'} | "
            f"{_trunc(frag.text)} | | {in_prefilter} |"
        )


if __name__ == "__main__":
    main()
