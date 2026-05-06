"""Factory for constructing the configured LLMGateway.

Callers ask ``get_gateway()`` and get back the right backend per
deployment. Production picks Anthropic; tests pick Mock; future
deployments can pick OpenAI / Bedrock / on-prem by adding a branch.

The factory reads ``AdvisorSettings`` so deployment changes happen via
env vars without code changes.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from advisor.llm.anthropic_backend import AnthropicGateway
from advisor.llm.base import LLMGateway


class AdvisorLLMSettings(BaseSettings):
    """LLM-related settings for the advisor app.

    Values come from environment variables (or .env). The provider
    string drives the factory's branch; provider-specific keys live
    on this same model so deployment is one section of env.
    """

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    provider: str = Field(default="anthropic", alias="ADVISOR_LLM_PROVIDER")
    model: str = Field(default="claude-opus-4-5", alias="ADVISOR_LLM_MODEL")
    anthropic_api_key: str | None = Field(
        default=None, alias="ANTHROPIC_API_KEY"
    )


@lru_cache
def get_settings() -> AdvisorLLMSettings:
    return AdvisorLLMSettings()


def build_gateway(settings: AdvisorLLMSettings | None = None) -> LLMGateway:
    """Construct the configured gateway.

    Tests usually skip this and instantiate ``MockGateway`` directly so
    they can script responses without env-var setup. Production
    callers pass nothing and pick up settings from env.
    """
    s = settings or get_settings()
    if s.provider == "anthropic":
        if not s.anthropic_api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is required when ADVISOR_LLM_PROVIDER=anthropic"
            )
        return AnthropicGateway(api_key=s.anthropic_api_key)
    raise ValueError(
        f"unknown ADVISOR_LLM_PROVIDER {s.provider!r}; "
        "supported: 'anthropic'. Add a branch in registry.build_gateway."
    )
