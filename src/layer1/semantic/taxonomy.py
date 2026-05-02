from __future__ import annotations

import json
from functools import lru_cache
from importlib.resources import files
from typing import Any


@lru_cache
def load_taxonomy() -> dict[str, Any]:
    text = files("layer1.semantic").joinpath("taxonomy.json").read_text(encoding="utf-8")
    return json.loads(text)


def standard_terms() -> list[str]:
    return list(load_taxonomy()["entity_types"]["standard"]["terms"])


def use_suffixes() -> list[str]:
    return list(load_taxonomy()["entity_types"]["use"]["suffixes"])


def use_aliases() -> dict[str, str]:
    aliases = load_taxonomy()["entity_types"]["use"].get("aliases", {})
    return {key: value["canonical"] for key, value in aliases.items()}
