import json
from pathlib import Path

import pytest

GROUND_TRUTH_PATH = Path(__file__).parent.parent / "fixtures" / "halifax_ground_truth.json"


@pytest.fixture
def ground_truth():
    return json.loads(GROUND_TRUTH_PATH.read_text())


@pytest.fixture
def halifax_pdf_path():
    return "/Users/christopherrafuse/dev/agentic-bylaw-system/regionalcentrelub-eff-26april13-case24469toclinked.pdf"


@pytest.mark.integration
def test_content_zone_detection(halifax_pdf_path, ground_truth):
    pytest.skip("Filled in Task 5")


@pytest.mark.integration
def test_citation_pattern_extraction(halifax_pdf_path, ground_truth):
    pytest.skip("Filled in Task 5")
