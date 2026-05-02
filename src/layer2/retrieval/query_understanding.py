from __future__ import annotations

import re

from layer2.models.schemas import QueryUnderstanding

TOPIC_KEYWORDS = {
    "definition": ["define", "definition", "meaning"],
    "permitted_use": ["permitted", "allowed", "temporary use", "use"],
    "setback": ["setback", "yard"],
    "height": ["height", "storey", "storeys", "story", "stories"],
    "parking": ["parking", "stall"],
    "open_space": ["open space", "landscaped open space"],
    "density": ["density", "persons per acre"],
    "exception": ["except", "exception", "unless"],
    "cross_reference": ["subject to", "see section", "cross reference"],
    "applicability": ["apply", "applies", "applicable"],
}

SECTION_TYPE_KEYWORDS = {
    "schedule": ["schedule"],
    "table": ["table"],
    "section": ["section", "subsection", "clause"],
    "definition": ["definition", "defined"],
}

ZONE_RE = re.compile(r"\b([A-Z]{1,3}-?\d[A-Z]?(?:-?[A-Z])?)\b", flags=re.I)
DIMENSION_TERMS = {
    "minimum lot area": ["minimum lot area", "lot area minimum", "minimum area"],
    "maximum height": ["maximum height", "height maximum", "height limit"],
    "lot frontage": ["lot frontage", "frontage minimum", "minimum frontage"],
    "lot coverage": ["lot coverage", "coverage maximum", "maximum coverage"],
    "open space": ["open space", "landscaped open space"],
    "population density": ["population density", "persons per acre", "density"],
}


def understand_question(question_text: str) -> QueryUnderstanding:
    normalized = " ".join(question_text.lower().strip().split())
    topics = [topic for topic, keywords in TOPIC_KEYWORDS.items() if any(keyword in normalized for keyword in keywords)]
    if any(token in normalized for token in ["story", "stories", "storey", "storeys"]):
        if "height" not in topics:
            topics.append("height")
    section_types = [
        section_type
        for section_type, keywords in SECTION_TYPE_KEYWORDS.items()
        if any(keyword in normalized for keyword in keywords)
    ]
    legal_concepts = [term for term in ["temporary use", "residential zones", "minimum lot area"] if term in normalized]
    for concept, keywords in DIMENSION_TERMS.items():
        if any(keyword in normalized for keyword in keywords):
            legal_concepts.append(concept)
    if any(token in normalized for token in ["story", "stories", "storey", "storeys"]):
        legal_concepts.append("maximum height")
    zone_keywords = [match.upper().replace("-", "") for match in ZONE_RE.findall(question_text.upper())]
    use_keywords = [match.strip() for match in re.findall(r"(temporary use|residential use|parking)", normalized)]
    citation_guesses = re.findall(r"(?:section|subsection|clause|schedule)\s+([A-Za-z0-9\.\(\)]+)", question_text, flags=re.I)
    needs_definitions = "definition" in topics or normalized.startswith("what is")
    return QueryUnderstanding(
        normalized_question=normalized,
        topics=topics,
        legal_concepts=legal_concepts,
        section_types=section_types,
        zone_keywords=zone_keywords,
        use_keywords=use_keywords,
        citation_guesses=citation_guesses,
        needs_definitions=needs_definitions,
    )
