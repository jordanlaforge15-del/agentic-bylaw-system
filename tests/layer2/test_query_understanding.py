from layer2.retrieval.query_understanding import understand_question


def test_understand_question_extracts_topics_and_citations():
    result = understand_question("Is a temporary use permitted under subsection 1.2 in R1?")
    assert "permitted_use" in result.topics
    assert "1.2" in result.citation_guesses
    assert "temporary use" in result.use_keywords


def test_understand_question_treats_stories_as_height_and_normalizes_zone():
    result = understand_question("How many stories are permitted in an R-1 zone?")
    assert "height" in result.topics
    assert "maximum height" in result.legal_concepts
    assert "R1" in result.zone_keywords
