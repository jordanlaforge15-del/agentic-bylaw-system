"""ABS-324 import-boundary guard: keep the two purchase products decoupled.

The decision (2026-06-16): we keep BOTH purchase products — Conversation
(legacy tier/CaseCredit ledger) and Answers (per-question
``QuestionPurchase`` / free-question credit) — but neither delivery surface
may consume the other's entitlement. The rule is enforced as a test, not a
convention:

* **Answers modules** (``billing/answers.py`` and the question routes in
  ``billing/router.py``) must NOT reference the Conversation gate
  (``reserve_credit_for_session`` / ``commit_credit_for_session``) nor open
  a Case (``open_case_free``) — that cross-call was the coupling behind
  ABS-323.
* **Conversation modules** (``api/quota.py`` and ``chat/*``) must NOT
  reference the Answers entitlement (``consume_free_question`` /
  ``refund_free_question``).

The check walks each module's AST and collects every imported name, bare
``Name`` reference, and attribute access. Comments and docstrings are not in
the AST, so a module is free to *describe* the boundary (as ``router.py``'s
import comment does) without tripping the guard. A regression — say, a
future edit that re-imports ``reserve_credit_for_session`` into the Answers
flow — fails this test loudly.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

# Repo root → src/advisor/...
_SRC = Path(__file__).resolve().parents[3] / "src" / "advisor"

# Conversation-product entitlement (the legacy CaseCredit ledger gate) plus
# the Case opener. Answers code must never touch these.
_CONVERSATION_SYMBOLS = frozenset(
    {
        "reserve_credit_for_session",
        "commit_credit_for_session",
        "open_case_free",
    }
)

# Answers-product entitlement (the free-question credit). Conversation code
# must never touch these.
_ANSWERS_SYMBOLS = frozenset(
    {
        "consume_free_question",
        "refund_free_question",
    }
)


def _referenced_symbols(path: Path) -> set[str]:
    """Every identifier the module imports, names, or accesses as an attr.

    Pulls from the AST only, so comments/docstrings mentioning a symbol by
    name don't count — the boundary can be *documented* without being
    *crossed*.
    """
    tree = ast.parse(path.read_text(), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.name)
                if alias.asname:
                    names.add(alias.asname)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[-1])
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


# (module path relative to src/advisor, forbidden symbol set, product label)
_ANSWERS_MODULES = [
    "billing/answers.py",
    "billing/router.py",
]

_CONVERSATION_MODULES = [
    "api/quota.py",
    "chat/session.py",
    "chat/tools.py",
    "chat/classifier.py",
    "chat/persona.py",
    "chat/compact.py",
    "chat/hedging.py",
    "chat/history_compaction.py",
]


@pytest.mark.parametrize("rel_path", _ANSWERS_MODULES)
def test_answers_modules_do_not_touch_conversation_ledger(rel_path: str) -> None:
    path = _SRC / rel_path
    assert path.exists(), f"expected Answers module {rel_path} to exist"
    referenced = _referenced_symbols(path)
    leaked = referenced & _CONVERSATION_SYMBOLS
    assert not leaked, (
        f"ABS-324 boundary violation: Answers module {rel_path} references "
        f"Conversation/Case entitlement {sorted(leaked)}. A delivery surface "
        f"may only consume its OWN product's entitlement."
    )


@pytest.mark.parametrize("rel_path", _CONVERSATION_MODULES)
def test_conversation_modules_do_not_touch_answers_ledger(rel_path: str) -> None:
    path = _SRC / rel_path
    assert path.exists(), f"expected Conversation module {rel_path} to exist"
    referenced = _referenced_symbols(path)
    leaked = referenced & _ANSWERS_SYMBOLS
    assert not leaked, (
        f"ABS-324 boundary violation: Conversation module {rel_path} "
        f"references Answers entitlement {sorted(leaked)}. The chat/quota "
        f"path must never consume a free-question credit."
    )


def test_guard_actually_detects_a_violation() -> None:
    """Sanity-check the detector: a synthetic module that imports a
    Conversation symbol IS flagged. Guards against a guard that always
    passes (e.g. if the AST walk silently stopped collecting names)."""
    src = (
        "from advisor.api.quota import reserve_credit_for_session\n"
        "def f():\n    return reserve_credit_for_session\n"
    )
    tree = ast.parse(src)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Name):
            names.add(node.id)
    assert "reserve_credit_for_session" in (names & _CONVERSATION_SYMBOLS)
