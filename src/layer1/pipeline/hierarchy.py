from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import re

from layer1.models.enums import BlockType, FragmentType, ParseStatus
from layer1.models.schemas import FragmentData, PageBlockData
from layer1.pipeline.block_classifier import classify_text_block, detect_table_like_text, normalize_text, split_toc_lines
from layer1.pipeline.citations import citation_path, parse_citation_label
from layer1.profiles import ParsingProfile, get_parsing_profile


@dataclass
class StackEntry:
    index: int
    level: int
    path: str | None


@dataclass
class HierarchyBlock:
    source_block_index: int
    block: PageBlockData


LOW_LEVEL_FRAGMENT_TYPES = {FragmentType.CLAUSE, FragmentType.SUBCLAUSE}
ROMAN_SUBCLAUSE_TOKEN_RE = re.compile(r"^\((?:i|ii|iii|iv|v|vi|vii|viii|ix|x)\)$", re.IGNORECASE)

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
    return profile.term_definition_re.match(text) is not None or profile.term_reference_re.match(text) is not None


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


def _should_use_citation_match(block: PageBlockData, text: str, profile: ParsingProfile) -> bool:
    if block.block_type == BlockType.TABLE_REGION or detect_table_like_text(block.raw_text, profile=profile):
        return False
    match = parse_citation_label(text, profile=profile)
    if not match:
        return False

    if block.block_type == BlockType.FOOTNOTE:
        return False

    if match.fragment_type in {FragmentType.PART, FragmentType.SCHEDULE, FragmentType.APPENDIX}:
        return True

    title = match.title.strip()
    if match.fragment_type == FragmentType.SECTION and "." not in match.label:
        if profile.allow_docling_section_labels_from_list_blocks and block.parser_source == "docling" and block.block_type in {BlockType.HEADING, BlockType.LIST_ITEM, BlockType.PARAGRAPH}:
            return True
        return block.block_type == BlockType.HEADING and _looks_like_heading_title(title)

    if match.fragment_type in {FragmentType.SUBSECTION, FragmentType.CLAUSE, FragmentType.SUBCLAUSE}:
        return block.block_type in {BlockType.HEADING, BlockType.LIST_ITEM, BlockType.PARAGRAPH}

    return block.block_type in {BlockType.HEADING, BlockType.LIST_ITEM}


def _append_fragment(
    fragments: list[FragmentData],
    block: PageBlockData,
    block_index: int,
    fragment_type: FragmentType,
    text: str,
    label: str | None,
    parent_index: int | None,
    path: str | None,
    status: ParseStatus,
    confidence: float | None,
) -> int:
    fragments.append(
        FragmentData(
            fragment_type=fragment_type,
            citation_label=label,
            citation_path=path,
            parent_index=parent_index,
            page_start=block.page_number,
            page_end=block.page_number,
            reading_order_start=block.reading_order,
            reading_order_end=block.reading_order,
            text=text,
            parse_status=status,
            confidence=confidence,
            source_block_indices=[block_index],
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

    for hierarchy_block in _prepare_blocks_for_hierarchy(blocks, profile):
        block_index = hierarchy_block.source_block_index
        block = hierarchy_block.block
        if block.is_boilerplate or block.block_type in {BlockType.HEADER, BlockType.FOOTER} or not block.raw_text.strip():
            continue
        text = block.normalized_text or " ".join(block.raw_text.split())
        match = parse_citation_label(text, profile=profile) if _should_use_citation_match(block, text, profile) else None

        if match:
            if (
                match.fragment_type == FragmentType.CLAUSE
                and ROMAN_SUBCLAUSE_TOKEN_RE.fullmatch(match.label)
                and stack
                and stack[-1].level >= 5
                and profile.promote_roman_subclauses_after_heading
                and (
                    fragments[stack[-1].index].text.rstrip().endswith(":")
                    or fragments[stack[-1].index].metadata.get("block_type") == BlockType.HEADING.value
                )
            ):
                match = type(match)(
                    fragment_type=FragmentType.SUBCLAUSE,
                    label=match.label,
                    level=6,
                    title=match.title,
                    confidence=match.confidence,
                )
            parent = _nearest_parent(stack, match.level)
            parent_path = parent.path if parent else None
            can_address = not (match.fragment_type in LOW_LEVEL_FRAGMENT_TYPES and not parent_path)
            path = citation_path(parent_path, match.label) if can_address else None
            status = ParseStatus.PARSED if can_address else ParseStatus.UNCERTAIN
            confidence = match.confidence if can_address else min(match.confidence, 0.6)
            contextual_parent_index = parent.index if parent else None
            if match.fragment_type in LOW_LEVEL_FRAGMENT_TYPES and contextual_parent_index is None and definition_context_index is not None:
                contextual_parent_index = definition_context_index
            idx = _append_fragment(
                fragments,
                block,
                block_index,
                match.fragment_type,
                text,
                match.label,
                contextual_parent_index,
                path,
                status,
                confidence,
            )
            stack.append(StackEntry(index=idx, level=match.level, path=path))
            last_content_parent = idx
            continue

        parent = stack[-1] if stack else None
        structural_parent = _nearest_non_low_level_parent(stack)
        heading_parent = _nearest_heading_context_parent(fragments, stack)
        if block.block_type == BlockType.HEADING:
            idx = _append_fragment(
                fragments,
                block,
                block_index,
                FragmentType.HEADING,
                text,
                None,
                heading_parent.index if heading_parent else None,
                None,
                ParseStatus.UNCERTAIN,
                0.55,
            )
            last_content_parent = idx
            definition_context_index = idx if _is_definition_like(text, profile) else None
        elif block.block_type == BlockType.LIST_ITEM:
            list_parent = last_content_parent if last_content_parent is not None else (parent.index if parent else None)
            if _is_definition_like(text, profile):
                list_parent = heading_parent.index if heading_parent else None
            idx = _append_fragment(
                fragments,
                block,
                block_index,
                FragmentType.LIST_ITEM,
                text,
                None,
                list_parent,
                None,
                ParseStatus.UNCERTAIN if text.startswith(("-", "*", "•")) else ParseStatus.PARSED,
                0.7,
            )
            last_content_parent = idx
            definition_context_index = idx if _is_definition_like(text, profile) else definition_context_index
        elif block.block_type == BlockType.FOOTNOTE:
            _append_fragment(
                fragments,
                block,
                block_index,
                FragmentType.FOOTNOTE,
                text,
                None,
                parent.index if parent else None,
                None,
                ParseStatus.PARSED,
                0.75,
            )
        elif block.block_type == BlockType.TABLE_REGION:
            continue
        else:
            prose_parent = parent
            if _is_definition_like(text, profile):
                prose_parent = heading_parent
                definition_context_index = None
            elif parent and parent.level >= 5:
                prose_parent = structural_parent
            _append_fragment(
                fragments,
                block,
                block_index,
                FragmentType.PROSE,
                text,
                None,
                prose_parent.index if prose_parent else None,
                None,
                ParseStatus.PARSED if prose_parent else ParseStatus.UNCERTAIN,
                0.8 if prose_parent else 0.5,
            )
            if not _is_definition_like(text, profile):
                definition_context_index = None
    _clear_duplicate_citation_paths(fragments)
    return fragments


def _prepare_blocks_for_hierarchy(blocks: list[PageBlockData], profile: ParsingProfile) -> list[HierarchyBlock]:
    prepared: list[HierarchyBlock] = []
    for block_index, block in enumerate(blocks):
        toc_segments = _split_toc_block(block, profile)
        if toc_segments:
            prepared.extend(HierarchyBlock(block_index, segment) for segment in toc_segments)
            continue
        definition_segments = _split_definition_block(block, profile)
        if definition_segments:
            prepared.extend(HierarchyBlock(block_index, segment) for segment in definition_segments)
            continue
        if block.block_type == BlockType.TABLE_REGION or detect_table_like_text(block.raw_text, profile=profile):
            prepared.append(HierarchyBlock(block_index, block))
            continue
        prepared.extend(_split_block_for_hierarchy(block_index, block, profile))
    return prepared


def _split_block_for_hierarchy(block_index: int, block: PageBlockData, profile: ParsingProfile) -> list[HierarchyBlock]:
    lines = [line.strip() for line in block.raw_text.splitlines() if line.strip()]
    if len(lines) < 2:
        return [HierarchyBlock(block_index, block)]

    citation_starts = [idx for idx, line in enumerate(lines) if _is_citation_start_line(line, profile)]
    if len(citation_starts) <= 1:
        return [HierarchyBlock(block_index, block)]

    segment_starts = [0]
    for start in citation_starts[1:]:
        if start > 0:
            segment_starts.append(start)
    segment_starts = sorted(set(segment_starts))
    if len(segment_starts) <= 1:
        return [HierarchyBlock(block_index, block)]

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
                block_index,
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
            )
        )
    return segments or [HierarchyBlock(block_index, block)]


def _is_citation_start_line(text: str, profile: ParsingProfile) -> bool:
    match = parse_citation_label(normalize_text(text), profile=profile)
    if not match:
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
