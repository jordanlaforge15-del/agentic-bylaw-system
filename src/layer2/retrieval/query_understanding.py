from __future__ import annotations

import re

from layer2.models.schemas import QueryUnderstanding

TOPIC_KEYWORDS = {
    "definition": ["define", "definition", "meaning"],
    "permitted_use": ["permitted", "allowed", "temporary use", "use"],
    "setback": ["setback", "yard"],
    "height": ["height", "storey"],
    "parking": ["parking", "stall"],
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


def understand_question(question_text: str) -> QueryUnderstanding:
    normalized = " ".join(question_text.lower().strip().split())
    topics = [topic for topic, keywords in TOPIC_KEYWORDS.items() if any(keyword in normalized for keyword in keywords)]
    section_types = [
        section_type
        for section_type, keywords in SECTION_TYPE_KEYWORDS.items()
        if any(keyword in normalized for keyword in keywords)
    ]
    legal_concepts = [term for term in ["temporary use", "residential zones", "minimum lot area"] if term in normalized]
    zone_keywords = re.findall(r"\b[A-Z]{1,3}\d\b", question_text.upper())
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
