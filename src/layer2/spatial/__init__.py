"""Spatial analysis for case-anchor lots.

``lot_metrics`` computes pure-geometry characteristics (area, frontage,
depth, corner status) from a parcel polygon and its neighbours.

``extractor`` orchestrates: geocode an anchor address, find the
containing parcel, pull neighbour parcels, call ``lot_metrics``, and
return a spatial-facts dict shaped for ``Case.metadata_json``.
"""
from layer2.spatial.lot_metrics import LotMetrics, compute_lot_metrics
from layer2.spatial.extractor import extract_lot_facts, format_lot_facts_block

__all__ = [
    "LotMetrics",
    "compute_lot_metrics",
    "extract_lot_facts",
    "format_lot_facts_block",
]
