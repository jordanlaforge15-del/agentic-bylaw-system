"""Near-miss matching for permitted-use matrix row terms (ABS-351).

The permission-matrix resolver binds each row to a ``use`` entity whose
``canonical_name`` is ``normalize_use(row_label)`` — e.g. the row "Multi-unit
dwelling use" binds to ``"multi-unit dwelling use"``. A paid answer run resolves
a ``(use, zone)`` cell by that exact canonical name, so a human-style near miss
("Multiple-unit dwelling", "multi unit dwelling", "Dwelling unit") returns
``unknown_use`` and the agent burns budget guessing the canonical spelling.

This module supplies a two-tier, advisory-first matcher:

* **resolve** — when a query and a candidate row share a
  :func:`use_match_key`, they denote the *same* row (a deterministic
  spelling/normalization equivalence, not a fuzzy guess), so the caller may
  address the cell directly.
* **suggest** — otherwise the closest candidates are ranked and returned so one
  failed lookup is self-correcting. An ambiguous term never silently picks a
  row: wrong-row citations are worse than a miss.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz, process

from layer1.semantic.extractors import normalize_use

_MULTIPLE_RE = re.compile(r"\bmultiple\b")
_TRAILING_USE_RE = re.compile(r"\s+uses?$")
_TRAILING_PLURAL_RE = re.compile(r"s$")
_WS_RE = re.compile(r"\s+")

# rapidfuzz token_set_ratio floor for the advisory suggestion list. A genuine
# near miss shares most of its tokens with the intended row and scores well
# above this ("Dwelling unit" vs "Multi-unit dwelling use" ~76); the cap keeps
# token-poor coincidences ("Residential use" vs "Restaurant use" ~62) out of the
# list so a suggestion is never actively misleading.
SUGGESTION_SCORE_CUTOFF = 65.0
# At most this many suggestions ride back in an unknown_use response — enough
# that the right row is almost always present, few enough to fit one tool_result
# without the agent re-reading a wall of candidates (mirrors the citation-path
# suggestion budget in RetrievalService).
SUGGESTION_LIMIT = 5


def use_match_key(name: str) -> str:
    """Deterministic equivalence key for near-miss permitted-use phrasings.

    Collapses only the differences that make a human-typed use term miss an
    exact row binding *without* changing which row it denotes: case,
    hyphen-vs-space, "multiple"→"multi", a trailing "use"/"uses" qualifier, and a
    trailing plural "s" (on top of whatever :func:`normalize_use` aliasing
    already applies). Two terms that share a key denote the same row —
    "Multiple-unit dwelling", "multi unit dwelling", "Multi-unit dwellings", and
    the canonical "Multi-unit dwelling use" all key to ``"multi unit dwelling"``
    — so matching on the key is a normalization, safe to resolve silently. Terms
    that only *overlap* (e.g. "Dwelling unit" keys to ``"dwelling unit"``) get
    distinct keys and fall through to fuzzy suggestions.
    """
    key = normalize_use(name).replace("-", " ")
    key = _MULTIPLE_RE.sub("multi", key)
    key = _TRAILING_USE_RE.sub("", key)
    key = _WS_RE.sub(" ", key).strip()
    return _TRAILING_PLURAL_RE.sub("", key)


def _score_normalize(text: str) -> str:
    """Light preprocessing for the fuzzy scorer: lowercase + hyphen→space.

    Keeps every token (unlike :func:`use_match_key`, which strips "use" and
    plurals) so ``token_set_ratio`` compares on the words themselves, but folds
    the case and hyphenation differences that would otherwise sink a genuine
    near miss ("Dwelling unit" vs "Multi-unit dwelling use").
    """
    return text.lower().replace("-", " ")


@dataclass
class UseMatch:
    """Outcome of matching a query use term against a matrix's bound rows.

    Exactly one tier is populated: ``resolved`` names the unambiguous canonical
    row to address, OR ``suggestions`` ranks the closest rows for an advisory
    ``unknown_use`` response. Both empty means nothing was close enough to
    surface.
    """

    resolved: str | None = None
    suggestions: list[str] = field(default_factory=list)


def match_use(query: str, candidates: list[str]) -> UseMatch:
    """Match ``query`` against candidate use-row labels (ABS-351).

    ``candidates`` are the row labels of a permission matrix (canonical names or
    the human-readable ``raw_label`` — the caller chooses which it wants echoed
    back). Two-tier, advisory-first:

    * **resolve** — when exactly one candidate shares ``query``'s
      :func:`use_match_key`, it is returned in :attr:`UseMatch.resolved`. A
      normalized-spelling equivalence, never a fuzzy guess, so the caller can
      address the cell directly.
    * **suggest** — otherwise :attr:`UseMatch.resolved` stays ``None`` and
      :attr:`UseMatch.suggestions` holds the closest candidates (rapidfuzz
      ``token_set_ratio``, ranked, de-duped, capped at :data:`SUGGESTION_LIMIT`,
      floored at :data:`SUGGESTION_SCORE_CUTOFF`). When two or more candidates
      *share* the query's key (genuinely ambiguous), the term is treated as a
      suggestion case rather than silently picking one.
    """
    deduped = list(dict.fromkeys(c for c in candidates if c and c.strip()))
    if not deduped:
        return UseMatch()
    query_key = use_match_key(query)
    keyed = [c for c in deduped if use_match_key(c) == query_key]
    if len(keyed) == 1:
        return UseMatch(resolved=keyed[0])
    ranked = process.extract(
        query,
        deduped,
        scorer=fuzz.token_set_ratio,
        processor=_score_normalize,
        limit=SUGGESTION_LIMIT,
        score_cutoff=SUGGESTION_SCORE_CUTOFF,
    )
    return UseMatch(suggestions=[choice for choice, _score, _idx in ranked])
