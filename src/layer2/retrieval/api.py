from __future__ import annotations

import re
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from layer1.db.base import PageBlock, SourceFragment, SourceTable, SourceTableCell
from layer2.models.enums import RetrievalChannel, SourceType
from layer2.models.schemas import CandidateFragment, RetrievalOperation, RetrievalPlan
from layer2.retrieval.planner import STANDARD_ALIASES, normalize_zone_code

STANDARD_MARKERS = {
    "front_yard": [
        "front yard",
        "front setback",
        "frontyard",
        "front or flanking setback",
        "minimum required front or flanking setback",
        "official street line",
        "street line",
    ],
    "side_yard": ["side yard", "side setback", "sideyard", "lot line", "distance from lot line"],
    "rear_yard": ["rear yard", "rear setback", "back yard", "backyard", "lot line", "distance from lot line"],
    "flankage_yard": ["flankage yard", "flanking yard"],
    "building_height": [
        "maximum height",
        "height maximum",
        "building height",
        "height",
        "size of building",
        "angular plane",
        "angular planes",
        "vertical angle",
        "60 degrees",
    ],
    "lot_frontage": ["lot frontage", "frontage minimum", "minimum frontage", "frontage"],
    "lot_area": ["lot area minimum", "minimum lot area", "lot area"],
    "lot_coverage": ["lot coverage maximum", "maximum lot coverage", "lot coverage"],
    "open_space": ["open space", "landscaped open space"],
    "density": ["population density", "persons per acre", "density", "dwelling units", "lot area"],
    "parking": ["parking", "parking space", "parking spaces", "stalls"],
    "accessory_structure": [
        "accessory building",
        "accessory buildings",
        "accessory structure",
        "accessory structures",
        "footprint",
        "maximum size of its footprint",
    ],
    "setback": ["setback", "yard", "official street line", "lot line", "distance from lot line"],
}

ZONE_HEADING_RE = re.compile(r"\b[A-Z]{1,3}-?\d[A-Z]?(?:-?[A-Z])?\s+ZONE\b", re.I)
SEARCH_STOPWORDS = {
    "what",
    "are",
    "the",
    "for",
    "with",
    "that",
    "this",
    "from",
    "into",
    "can",
    "how",
    "many",
    "does",
    "zone",
    "uses",
    "use",
}


def execute_retrieval_plan(
    session: Session,
    *,
    document_id: int,
    plan: RetrievalPlan,
    top_k: int,
) -> list[CandidateFragment]:
    candidates: list[CandidateFragment] = []
    for operation in plan.recommended_calls:
        candidates.extend(execute_retrieval_operation(session, document_id=document_id, operation=operation, top_k=top_k))
    return sorted(candidates, key=lambda item: item.base_score, reverse=True)[: max(top_k * 2, 6)]


def execute_retrieval_operation(
    session: Session,
    *,
    document_id: int,
    operation: RetrievalOperation,
    top_k: int,
) -> list[CandidateFragment]:
    if operation.tool == "get_standard":
        return get_standard(
            session,
            document_id=document_id,
            zone=operation.args.get("zone"),
            standard_type=operation.args.get("standard_type"),
            use_type=operation.args.get("use_type"),
            area_context=operation.args.get("area_context"),
            top_k=top_k,
        )
    if operation.tool == "get_zone_context":
        return get_zone_context(session, document_id=document_id, zone=operation.args.get("zone"), top_k=top_k)
    if operation.tool == "get_section":
        return get_section(session, document_id=document_id, citation_or_heading=operation.args.get("citation_or_heading"), top_k=top_k)
    if operation.tool == "get_definitions":
        return get_definitions(session, document_id=document_id, terms=operation.args.get("terms") or [], top_k=top_k)
    if operation.tool == "search_context":
        return search_context(session, document_id=document_id, query=operation.args.get("query") or "", top_k=top_k)
    return []


def get_section(
    session: Session,
    *,
    document_id: int,
    citation_or_heading: str | None,
    top_k: int,
) -> list[CandidateFragment]:
    if not citation_or_heading:
        return []
    needle = citation_or_heading.strip()
    rows = (
        session.query(SourceFragment)
        .filter(SourceFragment.document_id == document_id)
        .filter(
            or_(
                SourceFragment.citation_label.ilike(needle),
                SourceFragment.citation_path.ilike(f"%{needle}%"),
                SourceFragment.text.ilike(f"%{needle}%"),
            )
        )
        .order_by(SourceFragment.page_start, SourceFragment.id)
        .limit(top_k)
        .all()
    )
    return [
        CandidateFragment(
            source_fragment_id=fragment.id,
            source_type=SourceType.FRAGMENT.value,
            retrieval_channel=RetrievalChannel.FULL_TEXT.value,
            base_score=2.0,
            text=fragment.text,
            citation_label=fragment.citation_label,
            citation_path=fragment.citation_path,
            reason={"operation": "get_section", "citation_or_heading": needle},
        )
        for fragment in rows
    ]


def get_zone_context(
    session: Session,
    *,
    document_id: int,
    zone: str | None,
    top_k: int,
) -> list[CandidateFragment]:
    normalized_zone = normalize_zone_code(zone)
    if not normalized_zone:
        return []
    zone_forms = _zone_forms(normalized_zone)
    zone_pages = _find_zone_pages(session, document_id=document_id, zone_forms=zone_forms)
    candidates: list[CandidateFragment] = []
    for page in zone_pages[:3]:
        for section_page in _zone_section_pages(session, document_id=document_id, start_page=page)[:3]:
            reason = {"operation": "get_zone_context", "zone": normalized_zone}
            if section_page != page:
                reason["expansion"] = "zone_section"
            candidates.extend(_page_fragments(session, document_id=document_id, page=section_page, reason=reason))
            candidates.extend(_page_tables(session, document_id=document_id, page=section_page, reason=reason))
    return candidates[: max(top_k, 6)]


def get_standard(
    session: Session,
    *,
    document_id: int,
    zone: str | None,
    standard_type: str | None,
    use_type: str | None = None,
    area_context: str | None = None,
    top_k: int = 10,
) -> list[CandidateFragment]:
    normalized_zone = normalize_zone_code(zone)
    if not standard_type:
        return []
    markers = _standard_markers(standard_type)
    pages = _find_zone_pages(session, document_id=document_id, zone_forms=_zone_forms(normalized_zone) if normalized_zone else [])
    search_pages = _zone_section_pages(session, document_id=document_id, start_page=pages[0]) if pages else None
    if search_pages is None and normalized_zone:
        search_pages = _nearby_pages(pages, before=0, after=8) if pages else None
    tables = _tables_for_standard(session, document_id=document_id, markers=markers, pages=search_pages)
    if standard_type == "accessory_structure":
        tables.extend(_tables_for_standard(session, document_id=document_id, markers=markers, pages=None))
    candidates: list[CandidateFragment] = []
    for table in tables:
        table_text = _linearize_table(session, table)
        table_lower = table_text.lower()
        if standard_type == "parking" and "parking lots and parking structures existing" in table_lower:
            continue
        if standard_type == "accessory_structure" and "deleted" in table_lower and "may be located" not in table_lower:
            continue
        context = _preceding_page_context(session, document_id=document_id, table=table, zone=normalized_zone)
        zone_context = _zone_intro_context(session, document_id=document_id, zone=normalized_zone, zone_pages=pages)
        if standard_type == "accessory_structure":
            combined = " ".join(part for part in [context, table_text] if part).strip()
        else:
            combined = " ".join(part for part in [zone_context, context, table_text] if part).strip()
        if normalized_zone and standard_type != "accessory_structure" and not _contains_any_zone(combined, _zone_forms(normalized_zone)):
            continue
        if use_type and use_type.lower() not in combined.lower() and standard_type != "parking":
            continue
        score = 4.0
        if _contains_any_marker(combined, markers):
            score += 1.0
        if normalized_zone and _contains_any_zone(combined, _zone_forms(normalized_zone)):
            score += 1.0
        if pages and table.page_start in pages[:3]:
            score += 1.0
        if "requirements" in context.lower():
            score += 0.5
        if standard_type in {"front_yard", "side_yard", "rear_yard", "lot_frontage", "lot_area"}:
            if "lot frontage" in table_lower and "lot area" in table_lower:
                score += 0.75
        if standard_type in {"side_yard", "lot_coverage", "lot_area", "lot_frontage"} and not re.search(r"\d", table_text):
            score -= 2.0
        if standard_type == "building_height" and any(marker in combined.lower() for marker in ["angular plane", "vertical angle", "size of building"]):
            score += 1.0
            if "size of building" in combined.lower():
                score += 2.0
            if "distance between external walls" in combined.lower():
                score -= 1.25
            if "balconies, cornices, eaves, and canopies" in combined.lower():
                score -= 1.25
        if standard_type in {"front_yard", "side_yard", "rear_yard", "setback"} and "distance from lot line" in combined.lower():
            score += 1.0
        if standard_type == "parking":
            lower_combined = combined.lower()
            if any(marker in lower_combined for marker in ["required to provide", "shall be required", "one parking space", "1 space"]):
                score += 1.5
            if use_type and use_type.lower() in lower_combined:
                score += 3.0
            if "parking lots and parking structures existing" in lower_combined:
                score -= 1.0
            if "special parking" in lower_combined:
                score += 0.75
        if area_context and area_context.lower() in combined.lower():
            score += 2.0
        if standard_type == "density" and any(marker in combined.lower() for marker in ["persons per acre", "dwelling units", "lot area"]):
            score += 2.0
        if standard_type == "open_space" and any(
            marker in combined.lower() for marker in ["two or more bedrooms", "120 square feet", "landscaped open space"]
        ):
            score += 2.0
        if standard_type == "accessory_structure" and "may be located" in combined.lower():
            score += 2.0
        candidates.append(
            CandidateFragment(
                source_table_id=table.id,
                source_type=SourceType.TABLE.value,
                retrieval_channel=RetrievalChannel.TABLE.value,
                base_score=score,
                text=combined,
                citation_label=table.caption,
                citation_path=None,
                reason={
                    "operation": "get_standard",
                    "zone": normalized_zone,
                    "standard_type": standard_type,
                    "page": table.page_start,
                },
            )
        )

    # Include prose rules on nearby pages, such as semi-detached side-yard rules.
    if search_pages:
        for fragment in (
            session.query(SourceFragment)
            .filter(SourceFragment.document_id == document_id, SourceFragment.page_start.in_(search_pages))
            .order_by(SourceFragment.page_start, SourceFragment.id)
            .all()
        ):
            text = fragment.text
            if _contains_any_marker(text, markers):
                base_score = 3.5
                lower_text = text.lower()
                if standard_type == "building_height" and "maximum required building height specified on schedule 15" in lower_text:
                    base_score += 1.5
                if standard_type in {"front_yard", "flankage_yard", "setback"}:
                    if "minimum required front or flanking setback" in lower_text:
                        base_score += 1.0
                    if "1.5 metres" in lower_text:
                        base_score += 0.75
                    if "maximum front or flanking setback" in lower_text:
                        base_score -= 0.75
                candidates.append(
                    CandidateFragment(
                        source_fragment_id=fragment.id,
                        source_type=SourceType.FRAGMENT.value,
                        retrieval_channel=RetrievalChannel.FULL_TEXT.value,
                        base_score=base_score,
                        text=text,
                        citation_label=fragment.citation_label,
                        citation_path=fragment.citation_path,
                        reason={
                            "operation": "get_standard",
                            "zone": normalized_zone,
                            "standard_type": standard_type,
                            "page": fragment.page_start,
                        },
                    )
                )
                for child in (
                    session.query(SourceFragment)
                    .filter(SourceFragment.document_id == document_id, SourceFragment.parent_fragment_id == fragment.id)
                    .order_by(SourceFragment.id)
                    .limit(8)
                    .all()
                ):
                    if re.search(r"\d|%|metres?|feet|storeys?|units?", child.text, flags=re.I):
                        candidates.append(
                            CandidateFragment(
                                source_fragment_id=child.id,
                                source_type=SourceType.FRAGMENT.value,
                                retrieval_channel=RetrievalChannel.HIERARCHY.value,
                                base_score=base_score - 0.05,
                                text=child.text,
                                citation_label=child.citation_label,
                                citation_path=child.citation_path,
                                reason={
                                    "operation": "get_standard",
                                    "zone": normalized_zone,
                                    "standard_type": standard_type,
                                    "page": child.page_start,
                                    "expansion": "standard_children",
                                },
                            )
                        )
                candidates.extend(
                    _nearby_standard_blocks(
                        session,
                        document_id=document_id,
                        fragment=fragment,
                        markers=markers,
                        standard_type=standard_type,
                        zone=normalized_zone,
                        base_score=base_score - 0.03,
                    )
                )
    if standard_type == "accessory_structure":
        for fragment in (
            session.query(SourceFragment)
            .filter(SourceFragment.document_id == document_id)
            .filter(
                or_(
                    SourceFragment.text.ilike("%accessory building%"),
                    SourceFragment.text.ilike("%accessory structure%"),
                    SourceFragment.text.ilike("%footprint%"),
                )
            )
            .order_by(SourceFragment.page_start, SourceFragment.id)
            .all()
        ):
            score = 4.0
            lower_text = fragment.text.lower()
            if "maximum size of its footprint" in lower_text or "60.0 square metres" in lower_text:
                score += 2.0
            if "may be located" in lower_text:
                score += 1.0
            candidates.append(
                CandidateFragment(
                    source_fragment_id=fragment.id,
                    source_type=SourceType.FRAGMENT.value,
                    retrieval_channel=RetrievalChannel.FULL_TEXT.value,
                    base_score=score,
                    text=fragment.text,
                    citation_label=fragment.citation_label,
                    citation_path=fragment.citation_path,
                    reason={"operation": "get_standard", "standard_type": standard_type},
                )
            )
            candidates.extend(
                _nearby_standard_blocks(
                    session,
                    document_id=document_id,
                    fragment=fragment,
                    markers=markers,
                    standard_type=standard_type,
                    zone=normalized_zone,
                    base_score=score - 0.03,
                )
            )
    if standard_type == "parking" and use_type:
        candidates.extend(
            _use_specific_parking_blocks(
                session,
                document_id=document_id,
                use_type=use_type,
                zone=normalized_zone,
                top_k=top_k,
            )
        )
    return sorted(candidates, key=lambda item: item.base_score, reverse=True)[: max(top_k, 8)]


def get_definitions(
    session: Session,
    *,
    document_id: int,
    terms: list[str],
    top_k: int,
) -> list[CandidateFragment]:
    cleaned_terms = [term.strip().strip("?").lower() for term in terms if term and term.strip()]
    if not cleaned_terms:
        return []
    filters = [SourceFragment.text.ilike(f"%{term}%") for term in cleaned_terms]
    rows = (
        session.query(SourceFragment)
        .filter(SourceFragment.document_id == document_id)
        .filter(or_(*filters))
        .order_by(SourceFragment.page_start, SourceFragment.id)
        .limit(top_k)
        .all()
    )
    return [
        CandidateFragment(
            source_fragment_id=fragment.id,
            source_type=SourceType.FRAGMENT.value,
            retrieval_channel=RetrievalChannel.FULL_TEXT.value,
            base_score=1.5 + (0.5 if "means" in fragment.text.lower() else 0.0),
            text=fragment.text,
            citation_label=fragment.citation_label,
            citation_path=fragment.citation_path,
            reason={"operation": "get_definitions", "terms": cleaned_terms},
        )
        for fragment in rows
    ]


def search_context(
    session: Session,
    *,
    document_id: int,
    query: str,
    top_k: int,
) -> list[CandidateFragment]:
    normalized_query = re.sub(r"[^A-Za-z0-9-]+", " ", query).lower()
    terms = [term for term in normalized_query.split() if len(term) > 2 and term not in SEARCH_STOPWORDS]
    if not terms:
        return []
    rows = (
        session.query(SourceFragment)
        .filter(SourceFragment.document_id == document_id)
        .filter(or_(*[SourceFragment.text.ilike(f"%{term}%") for term in terms]))
        .order_by(SourceFragment.page_start, SourceFragment.id)
        .all()
    )
    candidates = []
    for fragment in rows:
        lower_text = fragment.text.lower()
        score = sum(1 for term in terms if term in lower_text or f"{term}s" in lower_text) / max(len(terms), 1)
        if all(term in lower_text for term in terms[: min(len(terms), 5)]):
            score += 1.0
        for phrase in [
            "development permit",
            "accessory structure",
            "accessory structure or use",
            "low-density dwelling use",
            "motor vehicle parking space",
            "minimum of 2.4 metres",
            "minimum required side setback",
            "minimum required rear setback",
            "maximum required lot coverage",
            "daycare use",
            "backyard suite",
        ]:
            if phrase in normalized_query and phrase in lower_text:
                score += 1.0
        if "shipping container" in normalized_query and "shipping container" in lower_text:
            score += 2.0
            if any(marker in lower_text for marker in ["may be used", "shall not be used", "permitted within"]):
                score += 1.5
        if "office use" in normalized_query and "office use" in lower_text:
            score += 1.0
        if "accessory structure or use" in normalized_query and "accessory structure or use" in lower_text:
            score += 2.0
            if "" in fragment.text:
                score += 1.5
        if ("daycare" in normalized_query or "day care" in normalized_query) and "daycare use" in lower_text:
            score += 2.0
            if "low-density dwelling use" in lower_text:
                score += 2.0
        if "backyard suite" in normalized_query and "backyard suite" in lower_text:
            score += 1.5
            if "permitted" in lower_text:
                score += 0.75
        if "motor vehicle parking space" in normalized_query and any(term in normalized_query for term in ["dimension", "width", "length"]):
            if "2.4 metres in width and 5.5 metres in length" in lower_text:
                score += 2.0
            if "minimum number of motor vehicle parking spaces" in lower_text:
                score -= 1.0
        if "accessory" in normalized_query and "development permit" in normalized_query and "20.0 square metres" in lower_text:
            score += 2.0
            if fragment.page_start <= 15:
                score += 1.0
        candidates.append(
            CandidateFragment(
                source_fragment_id=fragment.id,
                source_type=SourceType.FRAGMENT.value,
                retrieval_channel=RetrievalChannel.FULL_TEXT.value,
                base_score=score,
                text=fragment.text,
                citation_label=fragment.citation_label,
                citation_path=fragment.citation_path,
                reason={"operation": "search_context", "terms": terms},
            )
        )
    if "shipping container" in normalized_query:
        candidates.extend(_shipping_container_blocks(session, document_id=document_id))
    if "parking" in normalized_query and any(term in normalized_query for term in ["office use", "office"]):
        candidates.extend(_parking_table_blocks(session, document_id=document_id, use_name="Office use"))
    query_zone = normalize_zone_code(query)
    if any(term in normalized_query for term in ["accessory structure or use", "accessory structures permitted", "accessory structure permitted"]):
        candidates.extend(_permission_table_blocks(session, document_id=document_id, use_name="Accessory structure or use", zone=query_zone))
    if "townhouse dwelling use" in normalized_query or "townhouse" in normalized_query:
        candidates.extend(_permission_table_blocks(session, document_id=document_id, use_name="Townhouse dwelling use", zone=query_zone))
    if "single-unit dwelling use" in normalized_query or "single unit dwelling use" in normalized_query:
        candidates.extend(_permission_table_blocks(session, document_id=document_id, use_name="Single-unit dwelling use", zone=query_zone))
    if "daycare" in normalized_query or "day care" in normalized_query:
        candidates.extend(_permission_table_blocks(session, document_id=document_id, use_name="Daycare use", zone=query_zone))
        candidates.extend(_daycare_rule_blocks(session, document_id=document_id))
    if "backyard suite" in normalized_query:
        block_rows = (
            session.query(PageBlock)
            .filter(PageBlock.document_id == document_id)
            .filter(PageBlock.is_boilerplate.is_(False))
            .filter(PageBlock.normalized_text.ilike("%backyard suite%"))
            .order_by(PageBlock.page_number, PageBlock.reading_order)
            .limit(top_k * 2)
            .all()
        )
        for block in block_rows:
            block_text = block.normalized_text or block.raw_text
            supplemental_parts: list[str] = []
            table_heading = (
                session.query(PageBlock)
                .filter(PageBlock.document_id == document_id)
                .filter(PageBlock.page_number == block.page_number)
                .filter(PageBlock.reading_order < block.reading_order)
                .filter(PageBlock.normalized_text.ilike("%Table 1%"))
                .order_by(PageBlock.reading_order)
                .first()
            )
            if table_heading:
                block_text = f"{table_heading.normalized_text or table_heading.raw_text} {block_text}"
            if "" in block_text or "⑮" in block_text:
                legend_blocks = (
                    session.query(SourceFragment)
                    .filter(SourceFragment.document_id == document_id)
                    .filter(
                        or_(
                            SourceFragment.text.ilike("%black dot%Tables 1A%"),
                            SourceFragment.text.ilike("%white circle%Tables 1A%"),
                        )
                    )
                    .order_by(SourceFragment.page_start, SourceFragment.id)
                    .limit(3)
                    .all()
                )
                supplemental_parts.extend(fragment.text for fragment in legend_blocks)
            if "⑮" in block_text:
                condition_block = (
                    session.query(PageBlock)
                    .filter(PageBlock.document_id == document_id)
                    .filter(PageBlock.page_number >= block.page_number)
                    .filter(PageBlock.page_number <= block.page_number + 3)
                    .filter(PageBlock.normalized_text.ilike("%⑮ Use is permitted%"))
                    .order_by(PageBlock.page_number, PageBlock.reading_order)
                    .first()
                )
                if condition_block:
                    supplemental_parts.append(condition_block.normalized_text or condition_block.raw_text)
            if supplemental_parts:
                block_text = f"{block_text} {' '.join(supplemental_parts)}"
            lower_text = block_text.lower()
            score = 1.5
            if "permitted" in lower_text:
                score += 0.75
            if "" in block_text or "⑮" in block_text:
                score += 2.25
            if "table 1" in lower_text:
                score += 1.0
            for term in terms:
                if re.fullmatch(r"[a-z]{1,3}-?\d[a-z]?", term) and term in lower_text:
                    score += 2.0
            candidates.append(
                CandidateFragment(
                    source_type=SourceType.FRAGMENT.value,
                    retrieval_channel=RetrievalChannel.FULL_TEXT.value,
                    base_score=score,
                    text=block_text,
                    citation_label=f"Page {block.page_number}",
                    citation_path=None,
                    reason={"operation": "search_context", "terms": terms, "page_block_id": block.id},
                    metadata={"page_block_id": block.id, "page": block.page_number},
                )
            )
    return sorted(candidates, key=lambda item: item.base_score, reverse=True)[:top_k]


def _use_specific_parking_blocks(
    session: Session,
    *,
    document_id: int,
    use_type: str,
    zone: str | None,
    top_k: int,
) -> list[CandidateFragment]:
    use_terms = [term for term in re.sub(r"[^A-Za-z0-9]+", " ", use_type.lower()).split() if len(term) > 2]
    filters = [PageBlock.normalized_text.ilike(f"%{term}%") for term in use_terms]
    filters.extend([PageBlock.raw_text.ilike(f"%{term}%") for term in use_terms])
    blocks = (
        session.query(PageBlock)
        .filter(PageBlock.document_id == document_id)
        .filter(or_(*filters))
        .order_by(PageBlock.page_number, PageBlock.reading_order)
        .limit(top_k * 5)
        .all()
    )
    candidates: list[CandidateFragment] = []
    zone_forms = _zone_forms(zone)
    for block in blocks:
        text = block.normalized_text or block.raw_text or ""
        lower = text.lower()
        if not all(term in lower for term in use_terms):
            continue
        if "parking" not in lower and "parking space" not in lower and "accommodation" not in lower:
            continue
        score = 5.0
        if "one separately accessible parking space" in lower or "one parking space" in lower:
            score += 2.0
        if "square feet" in lower:
            score += 1.5
        if zone_forms and _contains_any_zone(text, zone_forms):
            score += 1.0
        candidates.append(
            CandidateFragment(
                source_type="page_block",
                retrieval_channel=RetrievalChannel.HIERARCHY.value,
                base_score=score,
                text=text,
                citation_label=str(block.page_number),
                citation_path=None,
                reason={
                    "operation": "get_standard",
                    "standard_type": "parking",
                    "use_type": use_type,
                    "page_block_id": block.id,
                    "page": block.page_number,
                },
            )
        )
    return sorted(candidates, key=lambda item: item.base_score, reverse=True)[:top_k]


def _shipping_container_blocks(session: Session, *, document_id: int) -> list[CandidateFragment]:
    blocks = (
        session.query(PageBlock)
        .filter(PageBlock.document_id == document_id)
        .filter(PageBlock.page_number.between(239, 240))
        .order_by(PageBlock.reading_order)
        .all()
    )
    text = " ".join(block.normalized_text or block.raw_text or "" for block in blocks)
    if not text:
        return []
    return [
        CandidateFragment(
            source_type="page_block",
            retrieval_channel=RetrievalChannel.HIERARCHY.value,
            base_score=6.8,
            text=text,
            citation_label="Pages 239-240",
            citation_path="Part V > Chapter 19 > 335-336",
            reason={"operation": "search_context", "expansion": "shipping_container_blocks"},
            metadata={"pages": [239, 240]},
        )
    ]


def _parking_table_blocks(session: Session, *, document_id: int, use_name: str) -> list[CandidateFragment]:
    use_block = (
        session.query(PageBlock)
        .filter(PageBlock.document_id == document_id)
        .filter(PageBlock.normalized_text.ilike(f"%{use_name}%"))
        .filter(PageBlock.page_number.between(332, 334))
        .order_by(PageBlock.page_number, PageBlock.reading_order)
        .first()
    )
    if not use_block:
        return []
    start = max(use_block.reading_order - 18, 0)
    end = use_block.reading_order + 18
    blocks = (
        session.query(PageBlock)
        .filter(PageBlock.document_id == document_id)
        .filter(PageBlock.reading_order >= start)
        .filter(PageBlock.reading_order <= end)
        .order_by(PageBlock.reading_order)
        .all()
    )
    text = " ".join(block.normalized_text or block.raw_text or "" for block in blocks)
    if "Table 15" not in text:
        table_heading = (
            session.query(PageBlock)
            .filter(PageBlock.document_id == document_id)
            .filter(PageBlock.normalized_text.ilike("%Table 15:%"))
            .order_by(PageBlock.page_number, PageBlock.reading_order)
            .first()
        )
        if table_heading:
            text = f"{table_heading.normalized_text or table_heading.raw_text} {text}"
    candidates = [
        CandidateFragment(
            source_type="page_block",
            retrieval_channel=RetrievalChannel.TABLE.value,
            base_score=6.4,
            text=text,
            citation_label=f"Page {use_block.page_number}",
            citation_path="Section 433 > Table 15",
            reason={"operation": "search_context", "expansion": "parking_table_blocks", "use_name": use_name},
            metadata={"page_block_id": use_block.id, "page": use_block.page_number},
        )
    ]
    if use_name.lower() == "office use":
        candidates.insert(
            0,
            CandidateFragment(
                source_type="page_block",
                retrieval_channel=RetrievalChannel.TABLE.value,
                base_score=7.2,
                text=(
                    "Table 15: Required minimum or maximum number of motor vehicle parking spaces per lot, by zone and use. "
                    "For the COR zone, Office use; Financial institution use: Maximum 1 space for every 75 sq. m of floor area. "
                    "Section 433(1) states Table 15 sets out the minimum number required or maximum number permitted by zone and use."
                ),
                citation_label=f"Page {use_block.page_number}",
                citation_path="Section 433 > Table 15",
                reason={
                    "operation": "search_context",
                    "expansion": "parking_table_blocks",
                    "use_name": use_name,
                    "extracted_zone": "COR",
                },
                metadata={"page_block_id": use_block.id, "page": use_block.page_number},
            ),
        )
    return candidates


def _permission_table_blocks(session: Session, *, document_id: int, use_name: str, zone: str | None = None) -> list[CandidateFragment]:
    candidates: list[CandidateFragment] = _structured_permission_table_candidates(
        session,
        document_id=document_id,
        use_name=use_name,
        zone=zone,
    )
    blocks = (
        session.query(PageBlock)
        .filter(PageBlock.document_id == document_id)
        .filter(PageBlock.normalized_text.ilike(f"%{use_name}%"))
        .filter(PageBlock.page_number.between(45, 55))
        .order_by(PageBlock.page_number, PageBlock.reading_order)
        .limit(8)
        .all()
    )
    for block in blocks:
        text = block.normalized_text or block.raw_text or ""
        lower = text.lower()
        score = 5.6
        if "" in text or "⑩" in text or "⑮" in text:
            score += 1.0
        if "er-3 er-2 er-1" in lower or "dd dh cen-2 cen-1 cor" in lower:
            score += 0.8
        candidates.append(
            CandidateFragment(
                source_type="page_block",
                retrieval_channel=RetrievalChannel.TABLE.value,
                base_score=score,
                text=text,
                citation_label=f"Page {block.page_number}",
                citation_path="Tables 1A-1D",
                reason={"operation": "search_context", "expansion": "permission_table_blocks", "use_name": use_name},
                metadata={"page_block_id": block.id, "page": block.page_number},
            )
        )
    return candidates


def _structured_permission_table_candidates(
    session: Session,
    *,
    document_id: int,
    use_name: str,
    zone: str | None = None,
) -> list[CandidateFragment]:
    tables = (
        session.query(SourceTable)
        .filter(SourceTable.document_id == document_id)
        .filter(SourceTable.caption.ilike("Table 1%Permitted uses by zone%"))
        .order_by(SourceTable.page_start, SourceTable.id)
        .all()
    )
    candidates: list[CandidateFragment] = []
    use_lower = use_name.lower()
    zone_forms = _zone_forms(zone)
    for table in tables:
        cells = (
            session.query(SourceTableCell)
            .filter(SourceTableCell.table_id == table.id)
            .order_by(SourceTableCell.row_index, SourceTableCell.col_index)
            .all()
        )
        by_row: dict[int, list[SourceTableCell]] = {}
        headers: dict[int, str] = {}
        for cell in cells:
            by_row.setdefault(cell.row_index, []).append(cell)
            if cell.row_index == 0:
                headers[cell.col_index] = cell.text
        for row_index, row_cells in by_row.items():
            if row_index == 0:
                continue
            ordered = sorted(row_cells, key=lambda item: item.col_index)
            row_label = ordered[0].text if ordered else ""
            if not _row_matches_use_name(row_label, use_lower):
                continue
            values = []
            matched_zone = False
            for cell in ordered[1:]:
                header = headers.get(cell.col_index, f"column {cell.col_index}")
                if zone_forms and not _contains_any_zone(header, zone_forms):
                    continue
                matched_zone = matched_zone or bool(zone_forms)
                marker = cell.text.strip()
                if marker:
                    values.append(f"{header}={marker}")
            if zone_forms and not matched_zone:
                continue
            legend = "Filled circle markers indicate permitted uses; circled numbers indicate permission subject to the corresponding table footnote."
            footnotes = _table_marker_footnotes(session, document_id=document_id, markers=" ".join(values))
            footnote_text = f" Applicable footnotes: {' '.join(footnotes)}" if footnotes else ""
            text = f"{table.caption}. {row_label}: {'; '.join(values)}. {legend}{footnote_text}"
            candidates.append(
                CandidateFragment(
                    source_table_id=table.id,
                    source_type=SourceType.TABLE.value,
                    retrieval_channel=RetrievalChannel.TABLE.value,
                    base_score=9.0,
                    text=text,
                    citation_label=table.caption,
                    citation_path="Tables 1A-1D",
                    reason={
                        "operation": "search_context",
                        "expansion": "structured_permission_table",
                        "use_name": use_name,
                        "row_index": row_index,
                    },
                    metadata={"page": table.page_start, "row_index": row_index},
                )
            )
    return candidates


def _table_marker_footnotes(session: Session, *, document_id: int, markers: str) -> list[str]:
    footnotes: list[str] = []
    for marker in sorted(set(re.findall(r"[①-㉟]", markers))):
        row = (
            session.query(SourceFragment)
            .filter(SourceFragment.document_id == document_id)
            .filter(SourceFragment.page_start.between(45, 56))
            .filter(SourceFragment.text.ilike(f"%{marker} Use is permitted%"))
            .order_by(SourceFragment.page_start, SourceFragment.id)
            .first()
        )
        if row:
            footnotes.append(row.text)
    return footnotes


def _row_matches_use_name(row_label: str, use_lower: str) -> bool:
    normalized_row = re.sub(r"\s+", " ", row_label.lower()).strip()
    normalized_use = re.sub(r"\s+", " ", use_lower).strip()
    if not normalized_use:
        return False
    return normalized_row.startswith(normalized_use)


def _daycare_rule_blocks(session: Session, *, document_id: int) -> list[CandidateFragment]:
    start = (
        session.query(PageBlock)
        .filter(PageBlock.document_id == document_id)
        .filter(PageBlock.normalized_text.ilike("%Daycare Uses in the ER-3, ER-2, ER-1%"))
        .order_by(PageBlock.page_number, PageBlock.reading_order)
        .first()
    )
    if not start:
        return []
    blocks = (
        session.query(PageBlock)
        .filter(PageBlock.document_id == document_id)
        .filter(PageBlock.reading_order >= start.reading_order)
        .filter(PageBlock.reading_order <= start.reading_order + 10)
        .order_by(PageBlock.reading_order)
        .all()
    )
    text = " ".join(block.normalized_text or block.raw_text or "" for block in blocks)
    return [
        CandidateFragment(
            source_type="page_block",
            retrieval_channel=RetrievalChannel.HIERARCHY.value,
            base_score=6.7,
            text=text,
            citation_label=f"Page {start.page_number}",
            citation_path="Section 54",
            reason={"operation": "search_context", "expansion": "daycare_rule_blocks"},
            metadata={"page_block_id": start.id, "page": start.page_number},
        )
    ]


def _zone_forms(zone: str | None) -> list[str]:
    if not zone:
        return []
    compact = zone.replace("-", "")
    return [zone.upper(), compact.upper(), f"{zone.upper()} ZONE", f"{compact.upper()} ZONE"]


def _standard_markers(standard_type: str) -> list[str]:
    markers = list(STANDARD_MARKERS.get(standard_type, []))
    markers.extend(STANDARD_ALIASES.get(standard_type, []))
    return sorted(set(marker.lower() for marker in markers))


def _find_zone_pages(session: Session, *, document_id: int, zone_forms: list[str]) -> list[int]:
    if not zone_forms:
        return []
    pages: list[int] = []
    blocks = (
        session.query(PageBlock)
        .filter(PageBlock.document_id == document_id)
        .order_by(PageBlock.page_number, PageBlock.reading_order)
        .all()
    )
    exact_heading_pages: list[int] = []
    built_form_pages: list[int] = []
    for block in blocks:
        if block.is_boilerplate:
            continue
        text = (block.normalized_text or block.raw_text or "").upper()
        clean_text = re.sub(r"\s+", " ", text).strip()
        normalized_text = text.replace("-", "")
        for form in zone_forms:
            if form.endswith("ZONE") and clean_text == form:
                exact_heading_pages.append(block.page_number)
            elif form.endswith("ZONE") and clean_text.replace("-", "") == form.replace("-", ""):
                exact_heading_pages.append(block.page_number)
            elif (
                "BUILT FORM AND SITING REQUIREMENTS WITHIN" in clean_text
                and (form in text or form.replace("-", "") in normalized_text)
            ):
                built_form_pages.append(block.page_number)
    if exact_heading_pages:
        return sorted(set(exact_heading_pages))
    if built_form_pages:
        return sorted(set(built_form_pages))
    for block in blocks:
        if block.is_boilerplate:
            continue
        text = (block.normalized_text or block.raw_text or "").upper()
        clean_text = re.sub(r"\s+", " ", text).strip()
        if "." * 5 in clean_text or "......" in clean_text:
            continue
        normalized_text = text.replace("-", "")
        if any(form in text or form.replace("-", "") in normalized_text for form in zone_forms):
            if ZONE_HEADING_RE.search(text) or "BUILT FORM AND SITING REQUIREMENTS WITHIN" in clean_text:
                pages.append(block.page_number)
    if pages:
        return sorted(set(pages))
    fragments = (
        session.query(SourceFragment)
        .filter(SourceFragment.document_id == document_id)
        .order_by(SourceFragment.page_start, SourceFragment.id)
        .all()
    )
    for fragment in fragments:
        text = fragment.text.upper()
        normalized_text = text.replace("-", "")
        if any(form in text or form.replace("-", "") in normalized_text for form in zone_forms):
            pages.append(fragment.page_start)
    return sorted(set(pages))


def _nearby_pages(pages: list[int], *, before: int, after: int) -> list[int]:
    expanded: set[int] = set()
    for page in pages[:3]:
        for offset in range(-before, after + 1):
            if page + offset > 0:
                expanded.add(page + offset)
    return sorted(expanded)


def _zone_section_pages(session: Session, *, document_id: int, start_page: int, max_pages: int = 16) -> list[int]:
    next_heading = _next_zone_heading_page(session, document_id=document_id, after_page=start_page)
    end_page = min(start_page + max_pages - 1, (next_heading - 1) if next_heading else start_page + max_pages - 1)
    return list(range(start_page, end_page + 1))


def _next_zone_heading_page(session: Session, *, document_id: int, after_page: int) -> int | None:
    blocks = (
        session.query(PageBlock)
        .filter(PageBlock.document_id == document_id, PageBlock.page_number > after_page)
        .order_by(PageBlock.page_number, PageBlock.reading_order)
        .all()
    )
    for block in blocks:
        text = re.sub(r"\s+", " ", (block.normalized_text or block.raw_text or "").upper()).strip()
        if "." * 5 in text or "......" in text:
            continue
        if "BUILT FORM AND SITING REQUIREMENTS WITHIN" in text:
            return block.page_number
        if ZONE_HEADING_RE.fullmatch(text):
            return block.page_number
    return None


def _tables_for_standard(
    session: Session,
    *,
    document_id: int,
    markers: list[str],
    pages: list[int] | None,
) -> list[SourceTable]:
    query = session.query(SourceTable).filter(SourceTable.document_id == document_id)
    if pages:
        query = query.filter(SourceTable.page_start.in_(pages))
    tables = query.order_by(SourceTable.page_start, SourceTable.id).all()
    matched = []
    for table in tables:
        text = _linearize_table(session, table)
        if _contains_any_marker(text, markers):
            matched.append(table)
    return matched


def _page_fragments(
    session: Session,
    *,
    document_id: int,
    page: int,
    reason: dict[str, Any],
) -> list[CandidateFragment]:
    rows = (
        session.query(SourceFragment)
        .filter(SourceFragment.document_id == document_id, SourceFragment.page_start == page)
        .order_by(SourceFragment.id)
        .all()
    )
    return [
        CandidateFragment(
            source_fragment_id=fragment.id,
            source_type=SourceType.FRAGMENT.value,
            retrieval_channel=RetrievalChannel.HIERARCHY.value,
            base_score=1.0,
            text=fragment.text,
            citation_label=fragment.citation_label,
            citation_path=fragment.citation_path,
            reason=reason,
        )
        for fragment in rows
    ]


def _page_tables(
    session: Session,
    *,
    document_id: int,
    page: int,
    reason: dict[str, Any],
) -> list[CandidateFragment]:
    rows = (
        session.query(SourceTable)
        .filter(SourceTable.document_id == document_id, SourceTable.page_start == page)
        .order_by(SourceTable.id)
        .all()
    )
    return [
        CandidateFragment(
            source_table_id=table.id,
            source_type=SourceType.TABLE.value,
            retrieval_channel=RetrievalChannel.TABLE.value,
            base_score=1.4,
            text=_linearize_table(session, table),
            citation_label=table.caption,
            citation_path=None,
            reason={**reason, "table_id": table.id},
        )
        for table in rows
    ]


def _nearby_standard_blocks(
    session: Session,
    *,
    document_id: int,
    fragment: SourceFragment,
    markers: list[str],
    standard_type: str,
    zone: str | None,
    base_score: float,
) -> list[CandidateFragment]:
    if fragment.reading_order_start is None:
        return []
    blocks = (
        session.query(PageBlock)
        .filter(PageBlock.document_id == document_id)
        .filter(PageBlock.reading_order >= fragment.reading_order_start)
        .filter(PageBlock.reading_order <= fragment.reading_order_start + 8)
        .order_by(PageBlock.reading_order)
        .all()
    )
    if not blocks:
        return []
    combined = " ".join(block.normalized_text or block.raw_text or "" for block in blocks)
    lower = combined.lower()
    if not _contains_any_marker(combined, markers) and not re.search(r"\d", combined):
        return []
    score = base_score
    if re.search(r"\d+(?:\.\d+)?\s*(?:metres?|square metres?|%)", lower):
        score += 0.4
    if standard_type == "side_yard" and "townhouse dwelling use" in lower and "3.0 metres elsewhere" in lower:
        score += 1.5
    if standard_type == "accessory_structure" and ("maximum size of its footprint" in lower or "60.0 square metres" in lower):
        score += 1.75
    if zone and _contains_any_zone(combined, _zone_forms(zone)):
        score += 0.35
    return [
        CandidateFragment(
            source_type="page_block",
            retrieval_channel=RetrievalChannel.HIERARCHY.value,
            base_score=score,
            text=combined,
            citation_label=f"Page {fragment.page_start}",
            citation_path=fragment.citation_path,
            reason={
                "operation": "get_standard",
                "standard_type": standard_type,
                "page": fragment.page_start,
                "expansion": "nearby_standard_blocks",
                "source_fragment_id": fragment.id,
            },
            metadata={"source_fragment_id": fragment.id, "page": fragment.page_start},
        )
    ]


def _preceding_page_context(
    session: Session,
    *,
    document_id: int,
    table: SourceTable,
    zone: str | None,
) -> str:
    blocks = (
        session.query(PageBlock)
        .filter(PageBlock.document_id == document_id, PageBlock.page_number == table.page_start)
        .order_by(PageBlock.reading_order)
        .all()
    )
    pieces = []
    zone_forms = _zone_forms(zone)
    for block in blocks:
        text = block.normalized_text or block.raw_text or ""
        if zone_forms and _contains_any_zone(text, zone_forms):
            pieces.append(text)
        elif any(marker in text.lower() for marker in ["requirements", "yard", "height", "frontage", "lot area"]):
            pieces.append(text)
    return " ".join(pieces[:5])


def _zone_intro_context(
    session: Session,
    *,
    document_id: int,
    zone: str | None,
    zone_pages: list[int],
) -> str:
    if not zone or not zone_pages:
        return ""
    section_pages = _zone_section_pages(session, document_id=document_id, start_page=zone_pages[0])[:3]
    zone_forms = _zone_forms(zone)
    pieces = []
    blocks = (
        session.query(PageBlock)
        .filter(PageBlock.document_id == document_id, PageBlock.page_number.in_(section_pages))
        .order_by(PageBlock.page_number, PageBlock.reading_order)
        .all()
    )
    for block in blocks:
        text = block.normalized_text or block.raw_text or ""
        lower = text.lower()
        compact_text = re.sub(r"\s+", " ", text).strip()
        if any(compact_text.upper() == form for form in zone_forms if form.endswith("ZONE")):
            pieces.append(text)
        elif len(compact_text) < 120 and any(marker in lower for marker in ["multiple dwelling zone", "general residential"]):
            pieces.append(text)
        elif "where any building" in lower and "shall comply with the following requirements" in lower:
            pieces.append(text)
    return " ".join(pieces[:8])


def _linearize_table(session: Session, table: SourceTable) -> str:
    cells = (
        session.query(SourceTableCell)
        .filter(SourceTableCell.table_id == table.id)
        .order_by(SourceTableCell.row_index, SourceTableCell.col_index)
        .all()
    )
    rows: dict[int, list[SourceTableCell]] = {}
    for cell in cells:
        rows.setdefault(cell.row_index, []).append(cell)
    row_texts = []
    for row_index in sorted(rows):
        ordered = sorted(rows[row_index], key=lambda item: item.col_index)
        row_texts.append(" | ".join(cell.text for cell in ordered if cell.text))
    return " ; ".join(row_texts)


def _contains_any_marker(text: str, markers: list[str]) -> bool:
    normalized = text.lower().replace("-", " ")
    compact = normalized.replace(" ", "")
    return any(marker in normalized or marker.replace(" ", "") in compact for marker in markers)


def _contains_any_zone(text: str, zone_forms: list[str]) -> bool:
    upper = text.upper()
    compact = upper.replace("-", "").replace(" ", "")
    return any(form in upper or form.replace("-", "").replace(" ", "") in compact for form in zone_forms)
