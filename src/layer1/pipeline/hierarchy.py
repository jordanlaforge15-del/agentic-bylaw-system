from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import re

from layer1.models.enums import BlockType, FragmentType, ParseStatus
from layer1.models.schemas import FragmentData, PageBlockData
from layer1.pipeline.block_classifier import (
    classify_text_block,
    detect_table_like_text,
    looks_like_requirement_value,
    normalize_text,
    split_toc_lines,
)
from layer1.pipeline.citations import CitationMatch, citation_path, parse_citation_label
from layer1.profiles import ParsingProfile, get_parsing_profile

LOW_LEVEL_FRAGMENT_TYPES = {FragmentType.CLAUSE, FragmentType.SUBCLAUSE}


@dataclass
class StackEntry:
    index: int
    level: int
    path: str | None


@dataclass
class HierarchyBlock:
    source_block_indices: list[int]
    block: PageBlockData
    page_end: int | None = None

ROMAN_SUBCLAUSE_TOKEN_RE = re.compile(r"^\((?:i|ii|iii|iv|v|vi|vii|viii|ix|x)\)$", re.IGNORECASE)
_COMPOUND_BASE_RE = re.compile(r"^(\d+[A-Z]*)\(")
DEFINITION_HEADING_RE = re.compile(r"^\s*definitions?\b", re.IGNORECASE)
DEFINITION_INTRO_RE = re.compile(r"^\s*in this by-?law\s*:\s*$", re.IGNORECASE)
LEADING_QUOTED_SPACE_RE = re.compile(r"^(\s*['\"“])\s+")

# ABS-461. A PDF page (or column) break can land inside a hyphenated token —
# "...is zoned ER-3, ER-" / "2, ER-1, CH-2..." across pages 171/172 of the
# Regional Centre LUB. The tail is a bare number, so the numeric section regex
# reads it as a new section and every following clause reparents under a
# phantom "Part V > 2". These two patterns detect the break so the halves can
# be rejoined before any citation matching runs.
HYPHEN_BREAK_TAIL_RE = re.compile(r"[A-Za-z0-9]-$")
HYPHEN_BREAK_HEAD_RE = re.compile(r"^[A-Za-z0-9]")
CONTINUATION_JOINABLE_BLOCK_TYPES = {BlockType.PARAGRAPH, BlockType.LIST_ITEM}

# A section/subsection "title" that opens with sentence punctuation is a wrapped
# line, not a heading: real headings never start with a comma or semicolon.
MID_SENTENCE_TITLE_RE = re.compile(r"^[,;:.\)\]\}]")

def _find_compound_base_parent(fragments: list[FragmentData], label: str) -> int | None:
    """Search backward through fragments for a section matching the base of a compound label.

    For labels like ``28AO(1)`` the base is ``28AO``; for ``7(3)`` it's ``7``.
    Returns the fragment index if found, else ``None``.
    """
    m = _COMPOUND_BASE_RE.match(label)
    if not m:
        return None
    base_label = m.group(1)
    for i in range(len(fragments) - 1, -1, -1):
        if (
            fragments[i].citation_label == base_label
            and fragments[i].fragment_type in {FragmentType.SECTION, FragmentType.SUBSECTION}
        ):
            return i
    return None


def _nearest_parent(stack: list[StackEntry], level: int) -> StackEntry | None:
    while stack and stack[-1].level >= level:
        stack.pop()
    return stack[-1] if stack else None


def _nearest_non_low_level_parent(stack: list[StackEntry]) -> StackEntry | None:
    for entry in reversed(stack):
        if entry.level < 5:
            return entry
    return None


def _nearest_heading_context_parent(fragments: list[FragmentData], stack: list[StackEntry]) -> StackEntry | None:
    for entry in reversed(stack):
        frag = fragments[entry.index]
        if frag.fragment_type in {FragmentType.PART, FragmentType.SCHEDULE, FragmentType.APPENDIX, FragmentType.HEADING}:
            return entry
    return None


def _is_definition_like(text: str, profile: ParsingProfile) -> bool:
    normalized = LEADING_QUOTED_SPACE_RE.sub(r"\1", text)
    return profile.term_definition_re.match(normalized) is not None or profile.term_reference_re.match(normalized) is not None


def _is_definition_container_heading(text: str) -> bool:
    return DEFINITION_HEADING_RE.match(text) is not None


def _is_definition_container_intro(text: str) -> bool:
    return DEFINITION_INTRO_RE.match(text) is not None


def _looks_like_heading_title(text: str) -> bool:
    words = text.split()
    if not words:
        return True
    if len(words) > 12:
        return False
    alpha_words = [word for word in words if any(ch.isalpha() for ch in word)]
    if not alpha_words:
        return False
    titleish = sum(1 for word in alpha_words[:6] if word[:1].isupper())
    lowerish = sum(1 for word in alpha_words[:6] if word[:1].islower())
    return titleish >= lowerish


def _heading_context_segment(text: str) -> str | None:
    cleaned = normalize_text(text)
    if not cleaned:
        return None
    return f"[{cleaned}]"


def _is_mid_sentence_number(match: CitationMatch) -> bool:
    """True when a numeric SECTION/SUBSECTION match is really a wrapped line.

    ``parse_citation_label`` is deliberately permissive about what follows a
    number, so the tail of a hyphen-broken zone list ("2, ER-1, CH-2, ... zone:")
    parses as section "2" with the title ", ER-1, CH-2, ...". No genuine section
    heading opens with sentence punctuation, so this is a cheap, precise veto
    (ABS-461).
    """
    if match.fragment_type not in {FragmentType.SECTION, FragmentType.SUBSECTION}:
        return False
    return MID_SENTENCE_TITLE_RE.match(match.title) is not None


def _should_use_citation_match(block: PageBlockData, text: str, profile: ParsingProfile) -> bool:
    if block.block_type == BlockType.TABLE_REGION or detect_table_like_text(block.raw_text, profile=profile):
        return False
    if looks_like_requirement_value(text):
        return False
    match = parse_citation_label(text, profile=profile)
    if not match:
        return False
    if _is_mid_sentence_number(match):
        return False

    if block.block_type == BlockType.FOOTNOTE:
        return False

    if match.fragment_type in {FragmentType.PART, FragmentType.SCHEDULE, FragmentType.APPENDIX}:
        return True

    title = match.title.strip()
    if match.fragment_type == FragmentType.SECTION and "." not in match.label:
        if block.block_type == BlockType.HEADING and title.upper() == "ANGLE":
            return False
        if block.block_type == BlockType.HEADING:
            return True
        if profile.allow_docling_section_labels_from_list_blocks and block.parser_source == "docling" and block.block_type in {BlockType.HEADING, BlockType.LIST_ITEM, BlockType.PARAGRAPH}:
            return True
        if title.lower().startswith(("minimum ", "maximum ")):
            return True
        return _looks_like_heading_title(title)

    if match.fragment_type in {FragmentType.SUBSECTION, FragmentType.CLAUSE, FragmentType.SUBCLAUSE}:
        return block.block_type in {BlockType.HEADING, BlockType.LIST_ITEM, BlockType.PARAGRAPH}

    return block.block_type in {BlockType.HEADING, BlockType.LIST_ITEM}


def _should_promote_roman_subclause(
    fragments: list[FragmentData], stack: list[StackEntry], profile: ParsingProfile
) -> bool:
    if not (stack and profile.promote_roman_subclauses_after_heading):
        return False
    previous = fragments[stack[-1].index]
    if previous.metadata.get("block_type") == BlockType.HEADING.value or previous.text.rstrip().endswith(":"):
        return True
    previous_words = previous.text.split()
    return (
        previous.fragment_type == FragmentType.CLAUSE
        and len(previous_words) <= 12
        and not previous.text.rstrip().endswith((".", ";"))
        and " means " not in previous.text.lower()
    ) or previous.fragment_type == FragmentType.SUBCLAUSE


def _append_fragment(
    fragments: list[FragmentData],
    block: PageBlockData,
    block_indices: list[int],
    fragment_type: FragmentType,
    text: str,
    label: str | None,
    parent_index: int | None,
    path: str | None,
    status: ParseStatus,
    confidence: float | None,
    page_end: int | None = None,
) -> int:
    fragments.append(
        FragmentData(
            fragment_type=fragment_type,
            citation_label=label,
            citation_path=path,
            parent_index=parent_index,
            page_start=block.page_number,
            page_end=max(page_end, block.page_number) if page_end is not None else block.page_number,
            reading_order_start=block.reading_order,
            reading_order_end=block.reading_order,
            text=text,
            parse_status=status,
            confidence=confidence,
            source_block_indices=list(block_indices),
            metadata={"block_type": block.block_type.value},
        )
    )
    return len(fragments) - 1


def reconstruct_hierarchy(blocks: list[PageBlockData], profile: ParsingProfile | None = None) -> list[FragmentData]:
    profile = get_parsing_profile(profile)
    fragments: list[FragmentData] = []
    stack: list[StackEntry] = []
    last_content_parent: int | None = None
    definition_context_index: int | None = None
    definition_container_index: int | None = None
    current_heading_context_index: int | None = None

    for hierarchy_block in _prepare_blocks_for_hierarchy(blocks, profile):
        block_indices = hierarchy_block.source_block_indices
        block = hierarchy_block.block
        block_page_end = hierarchy_block.page_end
        if block.is_boilerplate or block.block_type in {BlockType.HEADER, BlockType.FOOTER} or not block.raw_text.strip():
            continue
        text = block.normalized_text or " ".join(block.raw_text.split())
        effective_block_type = BlockType.PARAGRAPH if looks_like_requirement_value(text) else block.block_type
        match = parse_citation_label(text, profile=profile) if _should_use_citation_match(block, text, profile) else None

        if match:
            if ROMAN_SUBCLAUSE_TOKEN_RE.fullmatch(match.label):
                if match.fragment_type == FragmentType.CLAUSE and _should_promote_roman_subclause(fragments, stack, profile):
                    match = type(match)(
                        fragment_type=FragmentType.SUBCLAUSE,
                        label=match.label,
                        level=6,
                        title=match.title,
                        confidence=match.confidence,
                    )
            parent = _nearest_parent(stack, match.level)
            parent_path = parent.path if parent else None
            if (
                match.fragment_type == FragmentType.SUBCLAUSE
                and ROMAN_SUBCLAUSE_TOKEN_RE.fullmatch(match.label)
                and not _should_promote_roman_subclause(fragments, stack, profile)
                and not parent_path
            ):
                match = type(match)(
                    fragment_type=FragmentType.CLAUSE,
                    label=match.label,
                    level=5,
                    title=match.title,
                    confidence=match.confidence,
                )
                parent = _nearest_parent(stack, match.level)
                parent_path = parent.path if parent else None
            path_parent = parent_path
            can_address = not (match.fragment_type in LOW_LEVEL_FRAGMENT_TYPES and not parent_path)
            path = citation_path(parent_path, match.label) if can_address else None
            status = ParseStatus.PARSED if can_address else ParseStatus.UNCERTAIN
            confidence = match.confidence if can_address else min(match.confidence, 0.6)
            contextual_parent_index = parent.index if parent else None
            if (
                contextual_parent_index is None
                and match.fragment_type in {FragmentType.SECTION, FragmentType.SUBSECTION}
                and last_content_parent is not None
                and fragments[last_content_parent].fragment_type == FragmentType.HEADING
            ):
                contextual_parent_index = last_content_parent
            if match.fragment_type == FragmentType.SUBSECTION:
                base_m = _COMPOUND_BASE_RE.match(match.label)
                if base_m:
                    base_label = base_m.group(1)
                    stack_parent_label = (
                        fragments[contextual_parent_index].citation_label
                        if contextual_parent_index is not None
                        else None
                    )
                    if stack_parent_label != base_label:
                        found = _find_compound_base_parent(fragments, match.label)
                        if found is not None:
                            contextual_parent_index = found
                            parent_path = fragments[found].citation_path
                            path_parent = parent_path
                            path = citation_path(path_parent, match.label)
                            status = ParseStatus.PARSED
                            confidence = match.confidence
            if (
                contextual_parent_index is None
                and match.fragment_type in {FragmentType.SECTION, FragmentType.SUBSECTION}
                and current_heading_context_index is not None
            ):
                contextual_parent_index = current_heading_context_index
            if match.fragment_type in LOW_LEVEL_FRAGMENT_TYPES and contextual_parent_index is None and definition_context_index is not None:
                contextual_parent_index = definition_context_index
            if match.fragment_type in LOW_LEVEL_FRAGMENT_TYPES and contextual_parent_index is None and definition_container_index is not None:
                contextual_parent_index = definition_container_index
            if (
                match.fragment_type in LOW_LEVEL_FRAGMENT_TYPES
                and current_heading_context_index is not None
                and definition_context_index is None
                and definition_container_index is None
            ):
                contextual_parent_index = current_heading_context_index
                if path_parent:
                    heading_segment = _heading_context_segment(fragments[current_heading_context_index].text)
                    if heading_segment:
                        path_parent = f"{path_parent} > {heading_segment}"
            can_address = not (match.fragment_type in LOW_LEVEL_FRAGMENT_TYPES and not path_parent)
            path = citation_path(path_parent, match.label) if can_address else None
            status = ParseStatus.PARSED if can_address else ParseStatus.UNCERTAIN
            confidence = match.confidence if can_address else min(match.confidence, 0.6)
            idx = _append_fragment(
                fragments,
                block,
                block_indices,
                match.fragment_type,
                text,
                match.label,
                contextual_parent_index,
                path,
                status,
                confidence,
                page_end=block_page_end,
            )
            stack.append(StackEntry(index=idx, level=match.level, path=path))
            last_content_parent = idx
            if match.fragment_type not in LOW_LEVEL_FRAGMENT_TYPES:
                definition_context_index = None
                definition_container_index = None
                title_text = (match.title or "").strip()
                if _is_definition_container_heading(title_text) or _is_definition_container_intro(title_text):
                    definition_container_index = idx
                if match.fragment_type in {
                    FragmentType.PART,
                    FragmentType.SECTION,
                    FragmentType.SUBSECTION,
                    FragmentType.SCHEDULE,
                    FragmentType.APPENDIX,
                }:
                    current_heading_context_index = contextual_parent_index if contextual_parent_index is not None and fragments[contextual_parent_index].fragment_type == FragmentType.HEADING else current_heading_context_index
            continue

        parent = stack[-1] if stack else None
        structural_parent = _nearest_non_low_level_parent(stack)
        heading_parent = _nearest_heading_context_parent(fragments, stack)
        if effective_block_type == BlockType.HEADING:
            idx = _append_fragment(
                fragments,
                block,
                block_indices,
                FragmentType.HEADING,
                text,
                None,
                heading_parent.index if heading_parent else None,
                None,
                ParseStatus.UNCERTAIN,
                0.55,
                page_end=block_page_end,
            )
            last_content_parent = idx
            current_heading_context_index = idx
            if _is_definition_container_heading(text):
                definition_container_index = idx
                definition_context_index = None
            elif _is_definition_like(text, profile):
                definition_context_index = idx
            else:
                definition_context_index = None
        elif effective_block_type == BlockType.LIST_ITEM:
            list_parent = last_content_parent if last_content_parent is not None else (parent.index if parent else None)
            if _is_definition_like(text, profile):
                list_parent = definition_container_index or (heading_parent.index if heading_parent else None)
            idx = _append_fragment(
                fragments,
                block,
                block_indices,
                FragmentType.LIST_ITEM,
                text,
                None,
                list_parent,
                None,
                ParseStatus.UNCERTAIN if text.startswith(("-", "*", "•")) else ParseStatus.PARSED,
                0.7,
                page_end=block_page_end,
            )
            last_content_parent = idx
            if _is_definition_container_intro(text):
                definition_container_index = idx
                definition_context_index = None
            elif _is_definition_like(text, profile):
                definition_context_index = idx
        elif effective_block_type == BlockType.FOOTNOTE:
            _append_fragment(
                fragments,
                block,
                block_indices,
                FragmentType.FOOTNOTE,
                text,
                None,
                parent.index if parent else None,
                None,
                ParseStatus.PARSED,
                0.75,
                page_end=block_page_end,
            )
        elif effective_block_type == BlockType.TABLE_REGION:
            continue
        else:
            prose_parent = parent
            prose_parent_index: int | None = prose_parent.index if prose_parent else None
            if looks_like_requirement_value(text):
                previous_fragment = fragments[-1] if fragments and fragments[-1].page_end == block.page_number else None
                prose_parent_index = (
                    len(fragments) - 1
                    if previous_fragment is not None and previous_fragment.citation_label is None
                    else prose_parent_index
                )
            elif _is_definition_like(text, profile):
                prose_parent_index = (
                    definition_container_index
                    or (heading_parent.index if heading_parent else None)
                    or (
                        last_content_parent
                        if last_content_parent is not None
                        and fragments[last_content_parent].fragment_type in {FragmentType.HEADING, FragmentType.LIST_ITEM}
                        else None
                    )
                )
            elif parent and parent.level >= 5:
                prose_parent_index = structural_parent.index if structural_parent else None
            idx = _append_fragment(
                fragments,
                block,
                block_indices,
                FragmentType.PROSE,
                text,
                None,
                prose_parent_index,
                None,
                ParseStatus.PARSED if prose_parent_index is not None else ParseStatus.UNCERTAIN,
                0.8 if prose_parent_index is not None else 0.5,
                page_end=block_page_end,
            )
            if _is_definition_like(text, profile):
                definition_context_index = idx
            else:
                definition_context_index = None
    _clear_duplicate_citation_paths(fragments)
    return fragments


def _prepare_blocks_for_hierarchy(blocks: list[PageBlockData], profile: ParsingProfile) -> list[HierarchyBlock]:
    prepared: list[HierarchyBlock] = []
    for joined in _join_hyphen_broken_blocks(blocks):
        block_indices = joined.source_block_indices
        block = joined.block
        page_end = joined.page_end
        toc_segments = _split_toc_block(block, profile)
        if toc_segments:
            prepared.extend(HierarchyBlock(block_indices, segment, page_end) for segment in toc_segments)
            continue
        definition_segments = _split_definition_block(block, profile)
        if definition_segments:
            prepared.extend(HierarchyBlock(block_indices, segment, page_end) for segment in definition_segments)
            continue
        if block.block_type == BlockType.TABLE_REGION or detect_table_like_text(block.raw_text, profile=profile):
            prepared.append(HierarchyBlock(block_indices, block, page_end))
            continue
        prepared.extend(_split_block_for_hierarchy(block_indices, block, page_end, profile))
    return prepared


def _joinable_continuation_block(block: PageBlockData) -> bool:
    return (
        not block.is_boilerplate
        and block.block_type in CONTINUATION_JOINABLE_BLOCK_TYPES
        and bool(block.raw_text.strip())
    )


def _block_text(block: PageBlockData) -> str:
    return block.normalized_text or normalize_text(block.raw_text)


def _is_hyphen_break_continuation(previous: PageBlockData, candidate: PageBlockData) -> bool:
    """True when ``candidate`` resumes a token ``previous`` broke on a hyphen.

    The hyphen has to be flanked by alphanumerics on both sides of the break —
    that is what distinguishes a mid-token page break ("ER-" / "2, ER-1...")
    from a paragraph that merely happens to end in a dash.
    """
    if not (_joinable_continuation_block(previous) and _joinable_continuation_block(candidate)):
        return False
    if not HYPHEN_BREAK_TAIL_RE.search(_block_text(previous).rstrip()):
        return False
    return HYPHEN_BREAK_HEAD_RE.match(_block_text(candidate).lstrip()) is not None


def _merge_continuation_block(head: HierarchyBlock, tail: PageBlockData, tail_index: int) -> HierarchyBlock:
    """Splice a hyphen-broken continuation back onto its head block.

    Joined with no separator so the hyphen closes over the break the way the
    page rendered it ("ER-" + "2" -> "ER-2"), which is what the zone codes in
    this corpus require. Provenance keeps both source blocks and the fragment's
    page range widens to cover the break.
    """
    block = head.block
    merged_raw = f"{block.raw_text.rstrip()}{tail.raw_text.lstrip()}"
    merged_normalized = f"{_block_text(block).rstrip()}{_block_text(tail).lstrip()}"
    metadata = {**block.metadata, "joined_hyphen_break_blocks": [*head.source_block_indices, tail_index]}
    return HierarchyBlock(
        source_block_indices=[*head.source_block_indices, tail_index],
        block=PageBlockData(
            page_number=block.page_number,
            block_type=block.block_type,
            bbox=block.bbox,
            reading_order=block.reading_order,
            raw_text=merged_raw,
            normalized_text=normalize_text(merged_normalized),
            is_boilerplate=block.is_boilerplate,
            parser_source=block.parser_source,
            confidence=block.confidence,
            metadata=metadata,
        ),
        page_end=max(tail.page_number, head.page_end or block.page_number),
    )


def _join_hyphen_broken_blocks(blocks: list[PageBlockData]) -> list[HierarchyBlock]:
    """Rejoin blocks the parser split mid-token on a trailing hyphen (ABS-461)."""
    joined: list[HierarchyBlock] = []
    for block_index, block in enumerate(blocks):
        if joined and _is_hyphen_break_continuation(joined[-1].block, block):
            joined[-1] = _merge_continuation_block(joined[-1], block, block_index)
            continue
        joined.append(HierarchyBlock([block_index], block))
    return joined


def _split_block_for_hierarchy(
    block_indices: list[int], block: PageBlockData, page_end: int | None, profile: ParsingProfile
) -> list[HierarchyBlock]:
    lines = [line.strip() for line in block.raw_text.splitlines() if line.strip()]
    if len(lines) < 2:
        return [HierarchyBlock(block_indices, block, page_end)]

    citation_starts = [idx for idx, line in enumerate(lines) if _is_citation_start_line(line, profile)]
    if len(citation_starts) <= 1:
        return [HierarchyBlock(block_indices, block, page_end)]

    segment_starts = [0]
    for start in citation_starts[1:]:
        if start > 0:
            segment_starts.append(start)
    segment_starts = sorted(set(segment_starts))
    if len(segment_starts) <= 1:
        return [HierarchyBlock(block_indices, block, page_end)]

    segments: list[HierarchyBlock] = []
    for seg_idx, start in enumerate(segment_starts):
        end = segment_starts[seg_idx + 1] if seg_idx + 1 < len(segment_starts) else len(lines)
        segment_lines = lines[start:end]
        segment_text = "\n".join(segment_lines).strip()
        if not segment_text:
            continue
        segment_type = classify_text_block(segment_text, profile=profile)
        segments.append(
            HierarchyBlock(
                block_indices,
                PageBlockData(
                    page_number=block.page_number,
                    block_type=segment_type,
                    bbox=block.bbox,
                    reading_order=block.reading_order,
                    raw_text=segment_text,
                    normalized_text=normalize_text(segment_text),
                    is_boilerplate=block.is_boilerplate,
                    parser_source=block.parser_source,
                    confidence=block.confidence,
                    metadata={**block.metadata, "split_from_block": True},
                ),
                page_end,
            )
        )
    return segments or [HierarchyBlock(block_indices, block, page_end)]


def _is_citation_start_line(text: str, profile: ParsingProfile) -> bool:
    match = parse_citation_label(normalize_text(text), profile=profile)
    if not match:
        return False
    if _is_mid_sentence_number(match):
        return False
    if match.fragment_type in {FragmentType.SECTION, FragmentType.SUBSECTION} and match.title:
        first = match.title.split()[0]
        if first[:1].islower():
            return False
    return True


def _split_definition_block(block: PageBlockData, profile: ParsingProfile) -> list[PageBlockData]:
    if block.block_type not in {BlockType.PARAGRAPH, BlockType.HEADING}:
        return []
    matches = list(profile.definition_start_re.finditer(block.raw_text))
    if len(matches) <= 1:
        return []
    segments: list[PageBlockData] = []
    for idx, match in enumerate(matches):
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(block.raw_text)
        segment_text = block.raw_text[start:end].strip()
        if not segment_text:
            continue
        segment_type = classify_text_block(segment_text, profile=profile)
        segments.append(
            PageBlockData(
                page_number=block.page_number,
                block_type=segment_type,
                bbox=block.bbox,
                reading_order=block.reading_order,
                raw_text=segment_text,
                normalized_text=normalize_text(segment_text),
                is_boilerplate=block.is_boilerplate,
                parser_source=block.parser_source,
                confidence=block.confidence,
                metadata={**block.metadata, "split_definition_block": True},
            )
        )
    return segments


def _split_toc_block(block: PageBlockData, profile: ParsingProfile) -> list[PageBlockData]:
    if block.block_type not in {BlockType.PARAGRAPH, BlockType.HEADING}:
        return []
    segments_text = split_toc_lines(block.raw_text)
    if not segments_text:
        return []
    segments: list[PageBlockData] = []
    for segment_text in segments_text:
        segment_type = classify_text_block(segment_text, profile=profile)
        segments.append(
            PageBlockData(
                page_number=block.page_number,
                block_type=segment_type,
                bbox=block.bbox,
                reading_order=block.reading_order,
                raw_text=segment_text,
                normalized_text=normalize_text(segment_text),
                is_boilerplate=block.is_boilerplate,
                parser_source=block.parser_source,
                confidence=block.confidence,
                metadata={**block.metadata, "split_toc_block": True},
            )
        )
    return segments if len(segments) > 1 else []


def _clear_duplicate_citation_paths(fragments: list[FragmentData]) -> None:
    counts = Counter(fragment.citation_path for fragment in fragments if fragment.citation_path)
    duplicate_paths = {path for path, count in counts.items() if count > 1}
    if not duplicate_paths:
        return
    for fragment in fragments:
        if fragment.citation_path not in duplicate_paths:
            continue
        metadata = dict(fragment.metadata)
        metadata["duplicate_citation_path"] = fragment.citation_path
        fragment.metadata = metadata
        fragment.citation_path = None
        if fragment.parse_status == ParseStatus.PARSED:
            fragment.parse_status = ParseStatus.UNCERTAIN
        if fragment.confidence is not None:
            fragment.confidence = min(fragment.confidence, 0.6)
