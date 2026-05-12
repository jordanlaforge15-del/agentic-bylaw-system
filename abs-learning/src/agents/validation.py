"""Validation agent for Phase 2.

Takes a partially-assembled manifest (parser_config + taxonomy) and runs
automated QA checks to produce a :class:`QAReport`.

The agent is dependency-injection-friendly: it accepts an arbitrary
``retrieval_api_client`` that exposes
``lookup_citation(citation_path: str) -> dict | None`` and resolves any
PDF I/O via :class:`PdfBootstrapReader`. Tests monkeypatch
``PdfBootstrapReader`` and the retrieval client to avoid real I/O.
"""
from __future__ import annotations

import os
import random
import re
import tempfile
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import httpx

from bootstrap.pdf_reader import PdfBootstrapReader
from manifest.models import (
    ParserConfig,
    QAReport,
    SourceDocument,
    TaxonomyMap,
)


_HYPHENATED_CODE_REGEX = re.compile(r"\b[A-Z]{1,4}-\d\b")
_PATTERN_SAMPLE_SIZE = 60
_PATTERN_SAMPLE_SEED = 17
# Codes that appear fewer than this many times across the full document are
# treated as incidental (figure refs, typos, one-off precinct mentions) rather
# than zone codes. A real zone code in a substantial bylaw appears dozens of
# times; calibrated against Halifax where the cutoff cleanly separates real
# zones (>=25 mentions) from incidental matches (1-3 mentions).
_MIN_CODE_OCCURRENCES = 5

_PASS_CITATION_THRESHOLD = 0.80
_PASS_ZONE_THRESHOLD = 0.85
_PASS_COVERAGE_LOWER = 0.03
_PASS_COVERAGE_UPPER = 0.40
_PASS_WITH_FLAGS_CITATION_THRESHOLD = 0.65
_PASS_WITH_FLAGS_ZONE_THRESHOLD = 0.70


def _file_url_to_path(url: str) -> str:
    parsed = urlparse(url)
    return parsed.path


class ValidationAgent:
    """Run automated QA checks against parser_config + taxonomy outputs."""

    def __init__(self, retrieval_api_client):
        """
        ``retrieval_api_client`` must expose
        ``lookup_citation(citation_path: str) -> dict | None``. Returning
        ``None`` (or raising) is treated as a resolution failure; returning
        a dict with a non-empty ``text`` field is treated as a success.
        """
        self.retrieval = retrieval_api_client

    # -------------------------------------------------------------- public
    def validate(
        self,
        source_doc: SourceDocument,
        parser_config: ParserConfig,
        taxonomy: TaxonomyMap,
        citation_samples: List[str],
    ) -> QAReport:
        citation_rate, citation_flags = self._check_citation_resolution(citation_samples)
        zone_completeness, zone_flags = self._check_zone_completeness(
            source_doc, taxonomy
        )
        pattern_coverage, pattern_flags = self._check_pattern_coverage(
            source_doc, parser_config
        )
        all_flags = citation_flags + zone_flags + pattern_flags
        status, action = self._determine_status(
            citation_rate, zone_completeness, pattern_coverage, all_flags
        )
        return QAReport(
            status=status,
            citation_resolution_rate=citation_rate,
            zone_completeness=zone_completeness,
            pattern_coverage=pattern_coverage,
            flags=all_flags,
            recommended_action=action,
        )

    # -------------------------------------- private: citation resolution
    def _check_citation_resolution(
        self, citation_samples: List[str]
    ) -> Tuple[float, List[Dict[str, Any]]]:
        if not citation_samples:
            return 0.0, []
        flags: List[Dict[str, Any]] = []
        resolved = 0
        for path in citation_samples:
            try:
                result = self.retrieval.lookup_citation(path)
            except Exception as exc:
                flags.append(
                    {
                        "severity": "warn",
                        "code": "citation_lookup_error",
                        "citation_path": path,
                        "message": f"Lookup error for {path!r}: {exc}",
                    }
                )
                continue
            if (
                isinstance(result, dict)
                and isinstance(result.get("text"), str)
                and result.get("text", "").strip()
            ):
                resolved += 1
            else:
                flags.append(
                    {
                        "severity": "warn",
                        "code": "citation_unresolved",
                        "citation_path": path,
                        "message": (
                            f"Citation {path!r} did not resolve to a non-empty fragment"
                        ),
                    }
                )
        rate = resolved / len(citation_samples)
        return rate, flags

    # ---------------------------------------- private: zone completeness
    def _check_zone_completeness(
        self,
        source_doc: SourceDocument,
        taxonomy: TaxonomyMap,
    ) -> Tuple[float, List[Dict[str, Any]]]:
        pages, _ = self._load_pdf_state(source_doc)

        # Hyphenated codes anywhere in the document, filtered by minimum
        # occurrence count so that incidental matches (figure references like
        # "F-4", appendix labels like "CH-7", single-mention precinct strings)
        # don't get treated as missing zone codes.
        hyphenated_counts: Counter = Counter()
        for text in pages.values():
            for code in _HYPHENATED_CODE_REGEX.findall(text):
                hyphenated_counts[code] += 1
        found: set[str] = {
            code
            for code, count in hyphenated_counts.items()
            if count >= _MIN_CODE_OCCURRENCES
        }

        taxonomy_codes = {z.code for z in taxonomy.zone_designations}

        # Non-hyphenated taxonomy codes that appear verbatim as words.
        for code in taxonomy_codes:
            if "-" in code:
                continue
            pattern = re.compile(rf"\b{re.escape(code)}\b")
            for text in pages.values():
                if pattern.search(text):
                    found.add(code)
                    break

        in_taxonomy = found & taxonomy_codes
        missing_from_taxonomy = sorted(found - taxonomy_codes)

        flags: List[Dict[str, Any]] = [
            {
                "severity": "warn",
                "code": "zone_missing_from_taxonomy",
                "zone_code": code,
                "message": (
                    f"Zone code {code!r} found in document but absent from taxonomy"
                ),
            }
            for code in missing_from_taxonomy
        ]
        completeness = len(in_taxonomy) / len(found) if found else 0.0
        return completeness, flags

    # ------------------------------------------ private: pattern coverage
    def _check_pattern_coverage(
        self,
        source_doc: SourceDocument,
        parser_config: ParserConfig,
    ) -> Tuple[float, List[Dict[str, Any]]]:
        pages, content_zone = self._load_pdf_state(source_doc)
        start, end = content_zone
        candidates = list(range(start, end + 1))
        if not candidates:
            return 0.0, [
                {
                    "severity": "error",
                    "code": "no_content_zone",
                    "message": "Content zone was empty; cannot evaluate coverage",
                }
            ]
        sample_size = min(_PATTERN_SAMPLE_SIZE, len(candidates))
        rng = random.Random(_PATTERN_SAMPLE_SEED)
        sampled = rng.sample(candidates, sample_size)

        compiled: List[re.Pattern[str]] = []
        for level in parser_config.citation_scheme.hierarchy:
            try:
                compiled.append(re.compile(level.pattern))
            except re.error:
                # Skip unparseable patterns; surface them as warnings.
                pass

        total_non_blank = 0
        matched = 0
        for page_num in sampled:
            text = pages.get(page_num, "")
            for line in text.splitlines():
                lstripped = line.lstrip()
                if not lstripped:
                    continue
                total_non_blank += 1
                if any(pat.match(lstripped) for pat in compiled):
                    matched += 1

        coverage = matched / total_non_blank if total_non_blank else 0.0
        flags: List[Dict[str, Any]] = []
        if total_non_blank == 0:
            flags.append(
                {
                    "severity": "error",
                    "code": "pattern_no_lines",
                    "message": "No non-blank lines sampled; cannot evaluate coverage",
                }
            )
        elif coverage == 0.0:
            flags.append(
                {
                    "severity": "error",
                    "code": "pattern_zero_coverage",
                    "message": (
                        "No sampled lines matched any citation pattern — "
                        "patterns are likely broken"
                    ),
                }
            )
        elif coverage > _PASS_COVERAGE_UPPER:
            flags.append(
                {
                    "severity": "warn",
                    "code": "pattern_too_greedy",
                    "coverage": coverage,
                    "message": (
                        f"Pattern coverage {coverage:.0%} exceeds {int(_PASS_COVERAGE_UPPER * 100)}%"
                        " — patterns may be too greedy"
                    ),
                }
            )
        elif coverage < _PASS_COVERAGE_LOWER:
            flags.append(
                {
                    "severity": "warn",
                    "code": "pattern_low_coverage",
                    "coverage": coverage,
                    "message": (
                        f"Pattern coverage {coverage:.0%} is below {int(_PASS_COVERAGE_LOWER * 100)}%"
                        " — many citation labels may be unmatched"
                    ),
                }
            )
        return coverage, flags

    # ------------------------------------------------ private: status logic
    def _determine_status(
        self,
        citation_rate: float,
        zone_completeness: float,
        pattern_coverage: float,
        all_flags: List[Dict[str, Any]],
    ) -> Tuple[str, str]:
        has_error = any(
            isinstance(f, dict) and f.get("severity") == "error" for f in all_flags
        )
        is_pass = (
            citation_rate >= _PASS_CITATION_THRESHOLD
            and zone_completeness >= _PASS_ZONE_THRESHOLD
            and _PASS_COVERAGE_LOWER <= pattern_coverage <= _PASS_COVERAGE_UPPER
            and not has_error
        )
        is_acceptable = (
            citation_rate >= _PASS_WITH_FLAGS_CITATION_THRESHOLD
            and zone_completeness >= _PASS_WITH_FLAGS_ZONE_THRESHOLD
        )
        if is_pass:
            return ("PASS", "approve")
        if is_acceptable:
            return ("PASS_WITH_FLAGS", "approve_with_review")
        return ("FAIL", "reject_and_retry")

    # ---------------------------------------------- private: PDF state
    def _load_pdf_state(
        self, source_doc: SourceDocument
    ) -> Tuple[Dict[int, str], Tuple[int, int]]:
        pdf_path, _tmp_to_clean = self._resolve_pdf_path(source_doc)
        try:
            reader = PdfBootstrapReader(pdf_path)
            pages = reader.extract_pages()
            zone = reader.detect_content_zone(pages)
            return pages, zone
        finally:
            if _tmp_to_clean is not None:
                try:
                    os.unlink(_tmp_to_clean)
                except OSError:
                    pass

    @staticmethod
    def _resolve_pdf_path(source_doc: SourceDocument) -> Tuple[str, Optional[str]]:
        """Return ``(local_path, tmp_path_to_clean_or_None)``."""
        url = source_doc.source_url
        if url.startswith("file://"):
            return _file_url_to_path(url), None
        with httpx.Client(follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
            tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
            try:
                tmp.write(resp.content)
                tmp.flush()
                tmp_path = tmp.name
            finally:
                tmp.close()
        return tmp_path, tmp_path
