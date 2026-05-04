from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Layer2Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = Field(
        default="postgresql+psycopg://layer1:layer1@localhost:5432/layer1",
        alias="DATABASE_URL",
    )
    llm_base_url: str | None = Field(default=None, alias="LAYER2_LLM_BASE_URL")
    llm_api_key: str | None = Field(default=None, alias="LAYER2_LLM_API_KEY")
    llm_model: str = Field(default="mock-layer2", alias="LAYER2_LLM_MODEL")
    embedding_model: str = Field(default="hashing-bge-small-en-v1.5", alias="LAYER2_EMBEDDING_MODEL")
    embedding_base_url: str | None = Field(default=None, alias="LAYER2_EMBEDDING_BASE_URL")
    embedding_api_key: str | None = Field(default=None, alias="LAYER2_EMBEDDING_API_KEY")
    embedding_dimensions: int = Field(default=384, alias="LAYER2_EMBEDDING_DIMENSIONS")
    prompt_version: str = Field(default="v1", alias="LAYER2_PROMPT_VERSION")
    retrieval_version: str = Field(default="v1", alias="LAYER2_RETRIEVAL_VERSION")
    token_budget: int = Field(default=3000, alias="LAYER2_TOKEN_BUDGET")
    top_k: int = Field(default=8, alias="LAYER2_TOP_K")
    max_cached_claims: int = Field(default=4, alias="LAYER2_MAX_CACHED_CLAIMS")
    semantic_graph_max_depth: int = Field(default=5, alias="LAYER2_SEMANTIC_GRAPH_MAX_DEPTH")
    semantic_graph_max_fragments: int = Field(default=25, alias="LAYER2_SEMANTIC_GRAPH_MAX_FRAGMENTS")
    semantic_graph_max_nodes: int = Field(default=100, alias="LAYER2_SEMANTIC_GRAPH_MAX_NODES")
    semantic_graph_allowed_edge_types: str = Field(
        default="conditioned_by,references,defines,applies_to,excepts,modifies",
        alias="LAYER2_SEMANTIC_GRAPH_ALLOWED_EDGE_TYPES",
    )
    # External geocoder fallback. The key is read from a file at runtime so
    # it never lives in source. The default path matches the convention the
    # repo already uses for OpenAI keys; both files are gitignored.
    google_maps_api_key_path: str = Field(
        default="google_maps_api_key", alias="GOOGLE_MAPS_API_KEY_PATH"
    )
    google_maps_region_bias: str = Field(default="ca", alias="GOOGLE_MAPS_REGION_BIAS")
    google_maps_request_timeout_s: float = Field(
        default=5.0, alias="GOOGLE_MAPS_REQUEST_TIMEOUT_S"
    )


@lru_cache
def get_settings() -> Layer2Settings:
    return Layer2Settings()
