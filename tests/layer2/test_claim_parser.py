from layer2.claims.parser import parse_answer_payload


def test_parse_answer_payload_handles_valid_json():
    payload = parse_answer_payload(
        '{"answer_text":"ok","assumptions":["a"],"insufficient_source":false,"cited_fragment_ids":[7],"cited_citation_labels":["(i)"],"claims":[]}'
    )
    assert payload.answer_text == "ok"
    assert payload.cited_fragment_ids == [7]


def test_parse_answer_payload_handles_invalid_json():
    payload = parse_answer_payload("not json")
    assert payload.insufficient_source is True
    assert "could not be parsed" in payload.answer_text


def test_parse_answer_payload_extracts_fenced_json():
    payload = parse_answer_payload(
        '```json\n{"answer_text":"ok","assumptions":[],"insufficient_source":false,"cited_fragment_ids":[1],"cited_citation_labels":["28"],"claims":[]}\n```'
    )
    assert payload.answer_text == "ok"
    assert payload.cited_fragment_ids == [1]
