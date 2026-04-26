from layer2.retrieval.query_understanding import understand_question


def test_understand_question_extracts_topics_and_citations():
    result = understand_question("Is a temporary use permitted under subsection 1.2 in R1?")
    assert "permitted_use" in result.topics
    assert "1.2" in result.citation_guesses
    assert "temporary use" in result.use_keywords

