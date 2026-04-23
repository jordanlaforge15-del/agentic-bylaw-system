from __future__ import annotations

from dataclasses import dataclass

from layer1.models.enums import BlockType, FragmentType, ParseStatus
from layer1.models.schemas import FragmentData, PageBlockData
from layer1.pipeline.citations import citation_path, parse_citation_label


@dataclass
class StackEntry:
    index: int
    level: int
    path: str | None


def _nearest_parent(stack: list[StackEntry], level: int) -> StackEntry | None:
    while stack and stack[-1].level >= level:
        stack.pop()
    return stack[-1] if stack else None


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


def reconstruct_hierarchy(blocks: list[PageBlockData]) -> list[FragmentData]:
    fragments: list[FragmentData] = []
    stack: list[StackEntry] = []
    last_content_parent: int | None = None

    for block_index, block in enumerate(blocks):
        if block.is_boilerplate or not block.raw_text.strip():
            continue
        text = block.normalized_text or " ".join(block.raw_text.split())
        match = parse_citation_label(text)

        if match:
            parent = _nearest_parent(stack, match.level)
            path = citation_path(parent.path if parent else None, match.label)
            idx = _append_fragment(
                fragments,
                block,
                block_index,
                match.fragment_type,
                text,
                match.label,
                parent.index if parent else None,
                path,
                ParseStatus.PARSED,
                match.confidence,
            )
            stack.append(StackEntry(index=idx, level=match.level, path=path))
            last_content_parent = idx
            continue

        parent = stack[-1] if stack else None
        if block.block_type == BlockType.HEADING:
            idx = _append_fragment(
                fragments,
                block,
                block_index,
                FragmentType.HEADING,
                text,
                None,
                parent.index if parent else None,
                None,
                ParseStatus.UNCERTAIN,
                0.55,
            )
            last_content_parent = idx
        elif block.block_type == BlockType.LIST_ITEM:
            idx = _append_fragment(
                fragments,
                block,
                block_index,
                FragmentType.LIST_ITEM,
                text,
                None,
                last_content_parent if last_content_parent is not None else (parent.index if parent else None),
                None,
                ParseStatus.UNCERTAIN if text.startswith(("-", "*", "•")) else ParseStatus.PARSED,
                0.7,
            )
            last_content_parent = idx
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
            _append_fragment(
                fragments,
                block,
                block_index,
                FragmentType.PROSE,
                text,
                None,
                parent.index if parent else None,
                None,
                ParseStatus.PARSED if parent else ParseStatus.UNCERTAIN,
                0.8 if parent else 0.5,
            )
    return fragments
