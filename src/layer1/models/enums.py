from __future__ import annotations

from enum import StrEnum


class BlockType(StrEnum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    FOOTNOTE = "footnote"
    TABLE_REGION = "table_region"
    HEADER = "header"
    FOOTER = "footer"
    UNKNOWN = "unknown"


class FragmentType(StrEnum):
    PART = "part"
    SECTION = "section"
    SUBSECTION = "subsection"
    CLAUSE = "clause"
    SUBCLAUSE = "subclause"
    SCHEDULE = "schedule"
    APPENDIX = "appendix"
    HEADING = "heading"
    PROSE = "prose"
    LIST_ITEM = "list_item"
    FOOTNOTE = "footnote"


class ParseStatus(StrEnum):
    PARSED = "parsed"
    UNCERTAIN = "uncertain"
    FALLBACK = "fallback"
    ERROR = "error"


class ResolutionStatus(StrEnum):
    UNRESOLVED = "unresolved"
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    NO_TARGET = "no_target"


class IngestionStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_WARNINGS = "completed_with_warnings"
    FAILED = "failed"
