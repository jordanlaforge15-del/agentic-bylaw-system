from layer1.profiles import available_profile_names, get_parsing_profile


def test_profile_registry_exposes_default_and_halifax():
    assert available_profile_names() == ["default", "halifax"]


def test_halifax_profile_enables_compound_sections():
    default = get_parsing_profile("default")
    halifax = get_parsing_profile("halifax")
    assert default.allow_compound_section_labels is False
    assert halifax.allow_compound_section_labels is True


def test_unknown_profile_raises():
    try:
        get_parsing_profile("missing")
    except ValueError as exc:
        assert "Unknown parsing profile" in str(exc)
    else:
        raise AssertionError("Expected ValueError for unknown profile")
