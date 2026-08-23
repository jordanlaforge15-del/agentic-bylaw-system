"""Post-generation guard: a heading may not contradict the section it introduces.

ABS-519 (surfaced grading ``evals/runs/zone-typology-all8`` against the golden
subset). A *correct* refusal carried a heading that asserted the opposite of its
own body:

    ### 1. Townhouse Dwelling Use — Permitted in ER-2 (with conditions)

    Table 1B confirms that **townhouse dwelling use is permitted in the ER-3
    zone** (marked with ⑭), but **not in ER-2**.

The prose is right and the summary table is right. The heading is wrong — and it
is the most scannable element on the page. A homeowner deciding whether to call
an architect reads "Permitted in ER-2 (with conditions)" as a qualified yes and
never reaches the paragraph that says no.

The substring grammar the golden grader uses cannot express this. Every phrasing
that catches the heading also catches the correct sentence: ``"permitted in
ER-2"`` is a substring of ``"...is not permitted in ER-2"``, so the rule fires on
a right answer. Polarity is a *structural* property of the claim, not a property
of any substring, so this module reads structure instead:

  1. Split the answer into ``#``-heading sections.
  2. Extract each heading's permission claim — the permission word, its polarity
     (asserted / denied), and the zone code it is about, if any.
  3. Extract the same claims from the section body, clause by clause, so
     ``"permitted in the ER-3 zone ... but not in ER-2"`` yields a POSITIVE claim
     about ER-3 and a NEGATIVE claim about ER-2 rather than one muddled verdict.
  4. A heading contradicts its body when the body carries claims about the same
     zone at the opposite polarity and none at the heading's polarity.

Two layers, mirroring ``advisor.chat.hedging`` (ABS-263):

  * ``docs/agent/persona.md`` tells the model to write headings that state the
    section's conclusion, and never a permission word whose polarity disagrees
    with the section.
  * this module is the deterministic net underneath it, applied to the final
    assistant turn in ``advisor.chat.session`` so a contradicting heading can
    never reach the user regardless of how the live model phrased it.

Repair is deliberately asymmetric, because the two directions have very
different costs:

  * heading asserts permission, body denies it → rewrite the heading to deny it
    ("Permitted in ER-2" → "Not Permitted in ER-2"), and drop a trailing
    "(with conditions)"-style parenthetical, which reads as a qualified yes.
  * heading denies permission, body asserts it → rewrite the heading to a
    NEUTRAL topic form ("Not Permitted in ER-2" → "Permission in ER-2"). We
    never auto-upgrade a heading into a permission the guard itself inferred:
    a false positive in that direction would tell a user to build.

Known limits (documented rather than papered over):

  * Only ATX (``#``) headings are examined. A bolded pseudo-heading on its own
    line is not treated as a heading.
  * The anchor vocabulary is zone codes (``ER-2``, ``CEN-2``, ``HR-1``). A claim
    anchored on something else (a use, a lot, an overlay) is only compared when
    the section discusses at most one zone — otherwise the section is skipped as
    ambiguous rather than guessed at.
  * A heading with no permission word is never touched. This guard is about
    permission polarity, not about headings in general.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from advisor.llm.base import ContentBlock, TextBlock

# Permission words whose plain form asserts permission. A preceding negator
# flips them.
_POSITIVE_WORDS: tuple[str, ...] = (
    "permitted",
    "allowed",
    "permissible",
)

# Permission words that deny permission on their own — no negator needed.
_NEGATIVE_WORDS: tuple[str, ...] = (
    "prohibited",
    "disallowed",
    "forbidden",
    "impermissible",
)

# Negators that flip a positive permission word when they precede it in the
# same clause. ``not`` covers "is not permitted" / "**not** permitted"; ``no``
# covers "no townhouse use permitted".
_NEGATORS: tuple[str, ...] = ("not", "never", "no", "nor", "non", "cannot", "without")

_WORD_ALTERNATION = "|".join(_POSITIVE_WORDS + _NEGATIVE_WORDS)
_NEGATOR_ALTERNATION = "|".join(_NEGATORS)

_CLAIM_RE = re.compile(
    rf"(?P<neg>\b(?:{_NEGATOR_ALTERNATION})\b[\s\-]+)?(?P<word>{_WORD_ALTERNATION})\b",
    re.IGNORECASE,
)

# Zone codes as the by-law writes them: ER-2, HR-1, CEN-2, RPK-1, DH. The
# two-part form is what a permission claim is anchored on in practice.
_ZONE_RE = re.compile(r"\b([A-Z]{2,4}-\d+[A-Z]?)\b")

_HEADING_RE = re.compile(r"^(?P<hashes>#{1,6})(?P<space>\s+)(?P<text>.+?)\s*$")

# "(with conditions)", "(conditional)", "(subject to conditions)" — a
# parenthetical that softens an assertion into a qualified yes. Dropped when we
# flip a heading to a denial, where it would keep reading as "yes, but".
_CONDITION_PARENTHETICAL_RE = re.compile(
    r"\s*\((?:[^)]*\b(?:condition|conditional|conditionally)\w*[^)]*)\)",
    re.IGNORECASE,
)

# Clause boundaries. Sentence punctuation, list bullets, and the coordinating
# conjunctions that carry a polarity switch ("permitted in ER-3, but not in
# ER-2") all end a clause.
_CLAUSE_SPLIT_RE = re.compile(
    r"(?:[.!?;:,]|\n|\|)+|\s+(?:but|however|whereas|while|although|though|except|"
    r"unless)\s+",
    re.IGNORECASE,
)

POSITIVE = "positive"
NEGATIVE = "negative"


@dataclass(frozen=True)
class PermissionClaim:
    """A permission assertion or denial, and what it is about."""

    word: str
    polarity: str
    zone: str | None


@dataclass(frozen=True)
class HeadingContradiction:
    """A heading whose permission claim the section body contradicts."""

    heading: str
    zone: str | None
    heading_polarity: str
    body_polarity: str
    suggested_heading: str

    def describe(self) -> str:
        about = f" about {self.zone}" if self.zone else ""
        asserted = "asserts" if self.heading_polarity == POSITIVE else "denies"
        found = "denies" if self.heading_polarity == POSITIVE else "asserts"
        return (
            f"heading {self.heading!r} {asserted} permission{about} while its "
            f"section body {found} it"
        )


def _strip_emphasis(text: str) -> str:
    """Drop markdown emphasis so ``**not** permitted`` reads as ``not permitted``."""
    return re.sub(r"[*_`]+", "", text)


def _zone_for(segment: str) -> str | None:
    """The zone code a claim segment is about, if exactly one is named.

    Two zones in one clause ("ER-2 and ER-3 both allow it") is not a claim this
    guard can anchor, so it reports none and the clause is ignored.
    """
    zones = _ZONE_RE.findall(segment)
    unique = list(dict.fromkeys(zones))
    return unique[0] if len(unique) == 1 else None


def _claim_polarity(match: re.Match[str]) -> str:
    word = match.group("word").lower()
    base = NEGATIVE if word in _NEGATIVE_WORDS else POSITIVE
    if match.group("neg"):
        return NEGATIVE if base == POSITIVE else POSITIVE
    return base


def heading_claim(heading_text: str) -> PermissionClaim | None:
    """The permission claim a heading makes, if it makes one."""
    clean = _strip_emphasis(heading_text)
    match = _CLAIM_RE.search(clean)
    if match is None:
        return None
    # The zone the claim is about is the one the claim word governs — look to
    # the right of the word first ("Permitted in ER-2"), then at the whole
    # heading ("ER-2: Permitted").
    zone = _zone_for(clean[match.end():]) or _zone_for(clean)
    return PermissionClaim(
        word=match.group("word").lower(),
        polarity=_claim_polarity(match),
        zone=zone,
    )


def body_claims(body: str) -> list[PermissionClaim]:
    """Every permission claim the body makes, clause by clause.

    Clause-local scoping is what lets ``"permitted in the ER-3 zone ... but not
    in ER-2"`` produce two opposed claims instead of one. A clause that names a
    zone but no permission word inherits the word from the last clause in the
    same line that had one, keeping its OWN negation — that is precisely the
    ``"but not in ER-2"`` shape.
    """
    claims: list[PermissionClaim] = []
    for line in _strip_emphasis(body).splitlines():
        inherited: PermissionClaim | None = None
        for clause in _CLAUSE_SPLIT_RE.split(line):
            if not clause or not clause.strip():
                continue
            zone = _zone_for(clause)
            match = _CLAIM_RE.search(clause)
            if match is not None:
                claim = PermissionClaim(
                    word=match.group("word").lower(),
                    polarity=_claim_polarity(match),
                    zone=zone if zone else _zone_for(clause[match.end():]),
                )
                claims.append(claim)
                inherited = claim
                continue
            if zone is None or inherited is None:
                continue
            # Bare continuation clause: "but not in ER-2". Polarity is the
            # inherited word's base polarity, flipped by this clause's own
            # negator.
            negated = re.search(rf"\b(?:{_NEGATOR_ALTERNATION})\b", clause, re.IGNORECASE)
            base = NEGATIVE if inherited.word in _NEGATIVE_WORDS else POSITIVE
            polarity = base
            if negated:
                polarity = NEGATIVE if base == POSITIVE else POSITIVE
            claims.append(
                PermissionClaim(word=inherited.word, polarity=polarity, zone=zone)
            )
    return claims


def _split_sections(text: str) -> list[tuple[int, str, list[str]]]:
    """``(line index, heading text, body lines)`` for each ATX heading."""
    lines = text.splitlines()
    sections: list[tuple[int, str, list[str]]] = []
    current: tuple[int, str, list[str]] | None = None
    for idx, line in enumerate(lines):
        match = _HEADING_RE.match(line)
        if match:
            if current is not None:
                sections.append(current)
            current = (idx, match.group("text"), [])
            continue
        if current is not None:
            current[2].append(line)
    if current is not None:
        sections.append(current)
    return sections


# A topic phrase that makes no claim, used for the unsafe repair direction
# (heading denies, body asserts): the heading stops contradicting the body
# without the guard itself telling anyone they may build.
_NEUTRAL_FORM = "Permission"


def _match_case(replacement: str, original: str) -> str:
    """Render ``replacement`` in the casing style of the word it replaces."""
    if original.isupper():
        return replacement.upper()
    if original[:1].isupper():
        return replacement
    return replacement.lower()


def _rewrite_heading(heading_text: str, target_polarity: str) -> str:
    """Rewrite a heading's permission claim to ``target_polarity``.

    Positive → negative asserts the denial ("Not Permitted"). Negative →
    positive neutralises instead ("Permission"), never asserting a permission
    the guard inferred rather than read.
    """
    match = _CLAIM_RE.search(_strip_emphasis(heading_text))
    if match is None:
        return heading_text
    # Locate the same claim in the ORIGINAL text (emphasis intact) so the
    # rewrite preserves surrounding markdown.
    original_match = _CLAIM_RE.search(heading_text)
    if original_match is None:
        return heading_text

    word = original_match.group("word")
    start = original_match.start()
    end = original_match.end()

    if target_polarity == NEGATIVE:
        if word.lower() in _NEGATIVE_WORDS:
            replacement = word
        else:
            replacement = f"{_match_case('Not', word)} {word}"
        rewritten = heading_text[:start] + replacement + heading_text[end:]
        # "(with conditions)" after a denial still reads as a qualified yes.
        return _CONDITION_PARENTHETICAL_RE.sub("", rewritten).rstrip()

    # The negator (if any) is inside the matched span, so replacing the span
    # removes it along with the permission word.
    replacement = _match_case(_NEUTRAL_FORM, word)
    return (heading_text[:start] + replacement + heading_text[end:]).rstrip()


def find_contradictions(text: str) -> list[HeadingContradiction]:
    """Headings in ``text`` whose permission claim their own section denies."""
    out: list[HeadingContradiction] = []
    for _, heading_text, body_lines in _split_sections(text):
        claim = heading_claim(heading_text)
        if claim is None:
            continue
        body = "\n".join(body_lines)
        if not body.strip():
            continue
        claims = body_claims(body)
        if not claims:
            continue

        if claim.zone is not None:
            relevant = [c for c in claims if c.zone == claim.zone]
        else:
            zones = {c.zone for c in claims if c.zone}
            if len(zones) > 1:
                # Section compares several zones and the heading names none —
                # which verdict it meant is genuinely ambiguous. Skip rather
                # than guess.
                continue
            relevant = claims
        if not relevant:
            continue

        polarities = {c.polarity for c in relevant}
        if claim.polarity in polarities or not polarities:
            continue
        opposite = NEGATIVE if claim.polarity == POSITIVE else POSITIVE
        out.append(
            HeadingContradiction(
                heading=heading_text,
                zone=claim.zone,
                heading_polarity=claim.polarity,
                body_polarity=opposite,
                suggested_heading=_rewrite_heading(heading_text, opposite),
            )
        )
    return out


def repair_headings(text: str) -> str:
    """Return ``text`` with every contradicting heading rewritten to agree.

    Returns the input string unchanged (same object) when nothing contradicts,
    so callers can detect a no-op with ``is``.
    """
    contradictions = {c.heading: c for c in find_contradictions(text)}
    if not contradictions:
        return text
    lines = text.splitlines(keepends=True)
    for idx, line in enumerate(lines):
        match = _HEADING_RE.match(line.rstrip("\r\n"))
        if not match:
            continue
        found = contradictions.get(match.group("text"))
        if found is None:
            continue
        newline = line[len(line.rstrip("\r\n")):]
        lines[idx] = (
            match.group("hashes") + match.group("space") + found.suggested_heading + newline
        )
    return "".join(lines)


def apply_heading_consistency(content: list[ContentBlock]) -> list[ContentBlock]:
    """Return ``content`` with contradicting headings repaired.

    Each text block is examined on its own: a heading and its body arrive in the
    same block in every shape we emit, and per-block rewriting keeps offsets
    honest. When nothing changes the original list object is returned so callers
    can detect a no-op with ``is``.
    """
    changed = False
    new_content: list[ContentBlock] = []
    for block in content:
        if isinstance(block, TextBlock):
            repaired = repair_headings(block.text)
            if repaired is not block.text and repaired != block.text:
                changed = True
                new_content.append(block.model_copy(update={"text": repaired}))
                continue
        new_content.append(block)
    return new_content if changed else content
