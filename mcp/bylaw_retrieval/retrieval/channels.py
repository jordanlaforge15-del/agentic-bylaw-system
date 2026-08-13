"""What one retrieval channel hands the fuser (ABS-500).

The text channel's return value is ``{fragment_id: score}`` and every caller
reads it that way. It also, as a by-product of the single scan it makes over
the in-scope fragments, learns which of the query's terms are rare enough in
this corpus to carry scope — the document-frequency cut ``_discriminating_tokens``
applies. The table channel needs exactly that set: without it, it ranks on
"in", "a" and "for", which every long row header carries, and the longest
header wins every query regardless of what it says.

Recomputing the cut inside the table channel would mean measuring the same
corpus property twice, over two different corpora (7,100 fragments vs ~800 axis
labels), and getting two different answers — the failure this class exists to
prevent. So the text channel carries it along.

A ``dict`` subclass rather than a wrapper object: the score map *is* the
channel's result and is consumed as a mapping in half a dozen places, including
a test that reads it directly. Widening it costs those callers nothing.
"""
from __future__ import annotations


class TextChannelScores(dict):
    """``{fragment_id: score}`` plus the query terms that carried scope."""

    __slots__ = ("discriminating",)

    def __init__(self, *args, discriminating: frozenset[str] = frozenset(), **kwargs):
        super().__init__(*args, **kwargs)
        self.discriminating = discriminating
