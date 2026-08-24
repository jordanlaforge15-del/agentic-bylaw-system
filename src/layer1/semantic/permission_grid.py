"""Densify a ragged permission matrix so a blank cell survives extraction.

The defect (ABS-520)
--------------------
The PDF table parser emits a *ragged* grid: it materializes a
``source_table_cell`` only where a text run landed. In the Regional Centre LUB's
permission matrices (Table 1A / Table 1B) the by-law's own convention is that a
cell with **no glyph** means "not permitted" — so every prohibition that isn't
followed by another marker on the same row is simply absent from the grid:

    Townhouse dwelling use | ⑮              <- Table 1B, page 48, as extracted
    Townhouse dwelling use | ⑮ |  |  |  |   <- what the by-law prints

ER-3 carries a conditional marker; ER-2, ER-1, CH-2 and CH-1 are blank, and the
parser drops all four. Retrieval then addresses (Townhouse dwelling use, ER-2),
finds *no cell at all*, and — correctly, under ABS-483's rules — reports
``unknown``. A flat statutory prohibition reaches the user as "the permission
could not be extracted", which reads as "possibly allowed". That is the most
expensive direction for this error.

The fix
-------
Materialize the missing intersections as explicit blank cells, but **only where
the parse can be shown to have lost nothing**. That distinction is the whole
point: ABS-483 exists because "we could not read this" and "the legislature
prohibited this" are different claims, and blanket densification would erase it
again — this time in the fabricating direction.

So this module reads the geometry the parser already stored
(``source_table_cell.bbox_json``) and fills a row only when that geometry
gives no sign the row lost anything:

* **column drift** — the x-centres of the cells in each column must form
  bands that are disjoint and ordered by ``col_index``. Overlapping bands mean
  no cell position can be trusted, so the whole table is refused.
* **foreign content** — a value cell holding ordinary text rather than a
  permission marker (a reprinted "ER-2" column header, say) is not data; its
  row is refused.
* **unlabelled row** — value cells on a row with no row-label cell are content
  the parser could not attach to a use. The rows bracketing them are refused.
* **orphan cell** — a value cell whose y-band does not overlap its own row
  label's y-band was attached to the wrong row, so a row was dropped nearby.
  Both bracketing rows are refused. (This is real: "Cluster housing use" is
  missing from Table 1B on page 48 and its two ● dots were absorbed into the
  following section-header row.)
* **row-pitch gap** — a vertical gap between consecutive row labels wider than
  a fraction of the table's row pitch means a label was dropped between them.
  Both neighbours are refused.
* **no geometry** — without bboxes nothing can be shown, so nothing is filled.
  A corpus seeded without geometry (the ABS-484 fixture) therefore keeps its
  holes and its ``unknown`` verdicts, exactly as before.

A filled cell is labelled ``metadata_json.grid_fill = "absent_cell"``. Nothing
is claimed beyond "the parser found no glyph in this cell", and the label keeps
every materialized cell greppable, countable and reversible.

What is deliberately NOT filled
-------------------------------
A cell that is *present* but carries an unmapped symbol-font glyph stays
``unknown`` (see :mod:`layer1.semantic.permission_markers`). That is ABS-483's
real case and this module never touches it.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from statistics import median
from typing import Any, Iterable, Sequence

logger = logging.getLogger(__name__)


# Marker written to a filled cell's ``metadata_json`` so the materialization is
# self-identifying in the corpus and in any downstream audit.
GRID_FILL_KEY = "grid_fill"
GRID_FILL_ABSENT_CELL = "absent_cell"

# Reasons a row is refused. Exported so the audit scripts and tests name them
# from one place.
REASON_NO_GEOMETRY = "no_geometry"
REASON_COLUMN_DRIFT = "column_drift"
REASON_FOREIGN_CONTENT = "foreign_content"
REASON_UNLABELLED_ROW = "unlabelled_row"
REASON_ORPHAN_CELL = "orphan_cell"
REASON_ROW_PITCH_GAP = "row_pitch_gap"

# A value cell's y-band must overlap its row label's y-band. Markers sit ~1pt
# above their label's baseline box in the Regional Centre LUB, so a small slack
# keeps ordinary rows sound without letting a whole row's height slip through.
_Y_OVERLAP_SLACK_PT = 3.0

# A gap between consecutive row labels wider than this fraction of the table's
# median row pitch means a label was dropped between them.
_ROW_GAP_PITCH_FRACTION = 0.6

# Below these, the table is not a grid whose geometry says anything. One value
# column is enough — the HCD-SV slices of Table 1A are single-column matrices,
# and the row checks below carry the same weight there as anywhere else.
_MIN_COLUMNS = 1
_MIN_LABEL_ROWS = 2


@dataclass(frozen=True)
class _Band:
    """A 1-D interval, used for both column x-bands and row y-bands."""

    low: float
    high: float

    @property
    def centre(self) -> float:
        return (self.low + self.high) / 2.0

    def overlaps(self, other: "_Band", slack: float = 0.0) -> bool:
        return self.high + slack >= other.low and self.low - slack <= other.high


@dataclass
class PermissionGridAudit:
    """What the geometry says about one permission-matrix table's grid.

    ``gaps`` are the intersections safe to materialize as blank cells;
    ``refused`` records every intersection that is missing but *not* safe, so a
    caller reports the residue instead of treating it as covered.
    """

    table_id: int | None = None
    column_indices: list[int] = field(default_factory=list)
    label_row_indices: list[int] = field(default_factory=list)
    sound_row_indices: list[int] = field(default_factory=list)
    gaps: list[tuple[int, int]] = field(default_factory=list)
    refused: list[tuple[int, int]] = field(default_factory=list)
    row_reasons: dict[int, str] = field(default_factory=dict)
    table_reason: str | None = None

    @property
    def usable(self) -> bool:
        """True when the geometry supported filling anything at all."""
        return self.table_reason is None and bool(self.sound_row_indices)

    def reason_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        if self.table_reason is not None:
            counts[self.table_reason] = counts.get(self.table_reason, 0) + 1
        for reason in self.row_reasons.values():
            counts[reason] = counts.get(reason, 0) + 1
        return counts


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------


def is_grid_filled(cell: Any) -> bool:
    """True when ``cell`` was materialized by :func:`densify_permission_matrix`."""
    metadata = getattr(cell, "metadata_json", None) or {}
    return metadata.get(GRID_FILL_KEY) == GRID_FILL_ABSENT_CELL


def _bbox_band(bbox: Any, low_key: str, high_key: str) -> _Band | None:
    if not isinstance(bbox, dict):
        return None
    low, high = bbox.get(low_key), bbox.get(high_key)
    if low is None or high is None:
        return None
    try:
        low_f, high_f = float(low), float(high)
    except (TypeError, ValueError):
        return None
    if high_f < low_f:
        low_f, high_f = high_f, low_f
    return _Band(low_f, high_f)


def _x_band(cell: Any) -> _Band | None:
    return _bbox_band(getattr(cell, "bbox_json", None), "x0", "x1")


def _y_band(cell: Any) -> _Band | None:
    return _bbox_band(getattr(cell, "bbox_json", None), "y0", "y1")


def _is_marker_text(text: str | None, conventions: Any = None) -> bool:
    """True when a value cell's text is a permission marker (or blank).

    Blank, a ● dot, a circled footnote number, and an unmapped symbol-font
    glyph all belong in the value grid. Ordinary words do not — a value cell
    reading "ER-2" is a reprinted column header, and a value cell reading
    "Restaurant use" is a row label the parser put in the wrong column. Both
    say the row is not what it claims to be.
    """
    from layer1.semantic.permission_markers import NOT_PERMITTED, classify_permission_marker

    if text is None:
        return True
    stripped = text.strip()
    if not stripped:
        return True
    marker = classify_permission_marker(stripped, conventions)["permission_marker"]
    # Only ``not_permitted`` is reachable from non-empty ordinary text: every
    # recognised glyph classifies as permitted / conditional / unknown.
    return marker != NOT_PERMITTED


def _column_bands(values: Sequence[Any]) -> dict[int, _Band] | None:
    """x-centre band per ``col_index``, or ``None`` when the columns overlap.

    The bands are built from the cells the parser actually placed, not from a
    header row — Table 1A/1B continuation slices carry no header row at all, so
    a header-anchored reading finds no columns on half the corpus.
    """
    centres: dict[int, list[float]] = {}
    for cell in values:
        band = _x_band(cell)
        if band is None:
            return None
        centres.setdefault(cell.col_index, []).append(band.centre)
    bands = {
        col_index: _Band(min(xs), max(xs)) for col_index, xs in centres.items()
    }
    ordered = sorted(bands.items())
    for (_col_a, band_a), (_col_b, band_b) in zip(ordered, ordered[1:]):
        if band_b.low <= band_a.high:
            return None
    return bands


def _median_row_pitch(label_bands: dict[int, _Band]) -> float | None:
    ordered = [band for _index, band in sorted(label_bands.items())]
    pitches = [
        following.low - current.low
        for current, following in zip(ordered, ordered[1:])
        if following.low > current.low
    ]
    return median(pitches) if pitches else None


def _bracketing_rows(labels: dict[int, _Band], y_centre: float) -> list[int]:
    """The label rows immediately above and below ``y_centre``."""
    above = [row for row, band in labels.items() if band.high <= y_centre]
    below = [row for row, band in labels.items() if band.low >= y_centre]
    neighbours: list[int] = []
    if above:
        neighbours.append(max(above, key=lambda row: labels[row].high))
    if below:
        neighbours.append(min(below, key=lambda row: labels[row].low))
    return neighbours


def _missing_intersections(
    rows: Iterable[int], columns: Iterable[int], occupied: set[tuple[int, int]]
) -> list[tuple[int, int]]:
    return [
        (row, col)
        for row in sorted(rows)
        for col in sorted(columns)
        if (row, col) not in occupied
    ]


# ---------------------------------------------------------------------------
# The audit
# ---------------------------------------------------------------------------


def audit_permission_grid(
    cells: Iterable[Any],
    *,
    table_id: int | None = None,
    conventions: Any = None,
) -> PermissionGridAudit:
    """Decide which missing intersections of a permission matrix are safe to fill.

    ``cells`` is the table's full cell list; column 0 holds the row labels.
    Returns a :class:`PermissionGridAudit` whose ``gaps`` is empty whenever the
    geometry cannot vouch for the parse.
    """
    audit = PermissionGridAudit(table_id=table_id)

    labels: dict[int, _Band] = {}
    label_rows: set[int] = set()
    values: list[Any] = []
    occupied: set[tuple[int, int]] = set()
    for cell in cells:
        occupied.add((cell.row_index, cell.col_index))
        if is_grid_filled(cell):
            # A cell this module materialized on an earlier pass. It occupies
            # its intersection but carries no geometry, so it must not take
            # part in the geometry checks — otherwise densifying a table would
            # make the table read as ungeometried on the next audit.
            continue
        if cell.col_index == 0:
            label_rows.add(cell.row_index)
            band = _y_band(cell)
            if band is not None:
                labels[cell.row_index] = band
        else:
            values.append(cell)

    bands = _column_bands(values)
    audit.column_indices = sorted(bands) if bands else []
    audit.label_row_indices = sorted(labels)

    if bands is None:
        audit.table_reason = (
            REASON_COLUMN_DRIFT
            if all(_x_band(cell) is not None for cell in values)
            else REASON_NO_GEOMETRY
        )
    elif len(bands) < _MIN_COLUMNS or len(labels) < _MIN_LABEL_ROWS:
        audit.table_reason = REASON_NO_GEOMETRY
    elif len(labels) < len(label_rows):
        # Some row labels carry no geometry: the rectangle is not knowable.
        audit.table_reason = REASON_NO_GEOMETRY

    if audit.table_reason is not None:
        if audit.table_reason == REASON_COLUMN_DRIFT:
            logger.warning(
                "permission matrix table_id=%s: column x-bands overlap; "
                "refusing to densify the grid",
                table_id,
            )
        audit.refused = _missing_intersections(labels, audit.column_indices, occupied)
        return audit

    unsound: dict[int, str] = {}
    for cell in values:
        y_band = _y_band(cell)
        is_marker = _is_marker_text(getattr(cell, "text", None), conventions)
        if not is_marker:
            unsound.setdefault(cell.row_index, REASON_FOREIGN_CONTENT)
        label_band = labels.get(cell.row_index)
        if label_band is None:
            # A value cell on a row the parser gave no label. If it is a
            # permission marker, a use row was lost here and the neighbours may
            # have absorbed its content, so they are refused too. If it is
            # ordinary text it is a reprinted column header split across two
            # grid rows — noise, but no row went missing with it.
            unsound.setdefault(cell.row_index, REASON_UNLABELLED_ROW)
            if is_marker and y_band is not None:
                for neighbour in _bracketing_rows(labels, y_band.centre):
                    unsound.setdefault(neighbour, REASON_UNLABELLED_ROW)
            continue
        if y_band is None:
            unsound.setdefault(cell.row_index, REASON_NO_GEOMETRY)
        elif not y_band.overlaps(label_band, slack=_Y_OVERLAP_SLACK_PT):
            unsound.setdefault(cell.row_index, REASON_ORPHAN_CELL)
            for neighbour in _bracketing_rows(labels, y_band.centre):
                unsound.setdefault(neighbour, REASON_ORPHAN_CELL)

    pitch = _median_row_pitch(labels)
    if pitch is not None:
        threshold = pitch * _ROW_GAP_PITCH_FRACTION
        ordered = sorted(labels.items())
        for (row_a, band_a), (row_b, band_b) in zip(ordered, ordered[1:]):
            if band_b.low - band_a.high > threshold:
                unsound.setdefault(row_a, REASON_ROW_PITCH_GAP)
                unsound.setdefault(row_b, REASON_ROW_PITCH_GAP)

    audit.row_reasons = unsound
    audit.sound_row_indices = [row for row in sorted(labels) if row not in unsound]
    for row_index, col_index in _missing_intersections(
        labels, audit.column_indices, occupied
    ):
        if row_index in unsound:
            audit.refused.append((row_index, col_index))
        else:
            audit.gaps.append((row_index, col_index))
    return audit


def densify_permission_matrix(
    session: Any,
    table: Any,
    cells: Sequence[Any],
    *,
    apply: bool = True,
    conventions: Any = None,
) -> PermissionGridAudit:
    """Materialize the safe blank cells of one permission-matrix table.

    Returns the audit; ``audit.gaps`` is what was (or, with ``apply=False``,
    would be) created. New cells carry the empty text the by-law prints, the
    ``not_permitted`` marker that empty text classifies to, and
    ``grid_fill='absent_cell'`` provenance. Idempotent: a second call finds the
    intersections occupied and creates nothing.
    """
    from layer1.db.base import SourceTableCell
    from layer1.semantic.permission_markers import classify_permission_marker

    audit = audit_permission_grid(
        cells, table_id=getattr(table, "id", None), conventions=conventions
    )
    if not audit.gaps or not apply:
        return audit

    classified = classify_permission_marker("", conventions)
    row_labels = {
        cell.row_index: (getattr(cell, "text", "") or "").strip()
        for cell in cells
        if cell.col_index == 0
    }
    for row_index, col_index in audit.gaps:
        session.add(
            SourceTableCell(
                table_id=table.id,
                row_index=row_index,
                col_index=col_index,
                row_header_path=row_labels.get(row_index) or None,
                col_header_path=None,
                text="",
                bbox_json=None,
                metadata_json={
                    "col_span": 1,
                    "row_span": 1,
                    "row_header": False,
                    "row_section": False,
                    "column_header": False,
                    "permission_marker": classified["permission_marker"],
                    GRID_FILL_KEY: GRID_FILL_ABSENT_CELL,
                },
            )
        )
    return audit
