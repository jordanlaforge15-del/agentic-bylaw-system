"""Factory for constructing the configured LLMGateway.

Callers ask ``get_gateway()`` and get back the right backend per
deployment. Production picks Anthropic; tests pick Mock; future
deployments can pick OpenAI / Bedrock / on-prem by adding a branch.

The factory reads ``AdvisorLLMSettings`` so deployment changes happen via
env vars without code changes.

Two-model split
---------------
The case-based cost model uses two Anthropic models:

* ``advisor_llm_main_model`` (default ``claude-opus-4-5``) — the main
  research agent. Drives the chat tool loop.
* ``advisor_llm_classifier_model`` (default ``claude-haiku-4-5``) —
  the pre-flight Layer-2 classifier that recommends a tier before a
  credit is reserved. Cheap enough to run once per case-open.

The gateway is model-agnostic — both models flow through the same
``AnthropicGateway.complete()`` path with a per-call ``request.model``.
This module just exposes the configured model identifiers as
attributes; callers pass them to ``CompletionRequest``.
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
    # Main research agent — drives the chat tool loop. Sized for the
    # depth of legal/zoning research the product targets.
    advisor_llm_main_model: str = Field(
        default="claude-opus-4-5",
        alias="ADVISOR_LLM_MAIN_MODEL",
    )
    # Pre-flight Layer-2 classifier. Haiku is fine — it answers a
    # one-shot JSON question and we eat the latency before opening the
    # case so the user experiences it as part of the case-open click.
    advisor_llm_classifier_model: str = Field(
        default="claude-haiku-4-5",
        alias="ADVISOR_LLM_CLASSIFIER_MODEL",
    )
    anthropic_api_key: str | None = Field(
        default=None, alias="ANTHROPIC_API_KEY"
    )

    # ------------------------------------------------------------------
    # Backwards-compat alias for the v1 single-model setting. Existing
    # deployments that set ``ADVISOR_LLM_MODEL`` keep working — the
    # value is mirrored onto ``advisor_llm_main_model`` if the new var
    # isn't set. Kept until all deployment env files have migrated.
    # ------------------------------------------------------------------
    legacy_model: str | None = Field(default=None, alias="ADVISOR_LLM_MODEL")

    @property
    def main_model(self) -> str:
        """The model identifier the chat tool loop should use.

        Honours the new ``ADVISOR_LLM_MAIN_MODEL`` if set, otherwise
        falls back to the legacy ``ADVISOR_LLM_MODEL``, otherwise the
        default.
        """
        if self.legacy_model and self.advisor_llm_main_model == "claude-opus-4-5":
            # Legacy var present and main var is at the default — prefer
            # legacy so a deployment that hasn't migrated env still gets
            # its configured model.
            return self.legacy_model
        return self.advisor_llm_main_model

    @property
    def classifier_model(self) -> str:
        return self.advisor_llm_classifier_model


@lru_cache
def get_settings() -> AdvisorLLMSettings:
    return AdvisorLLMSettings()


def build_gateway(settings: AdvisorLLMSettings | None = None) -> LLMGateway:
    """Construct the configured gateway.

    Tests usually skip this and instantiate ``MockGateway`` directly so
    they can script responses without env-var setup. Production callers
    pass nothing and pick up settings from env.

    The same gateway serves both the main model and the classifier —
    model selection is per-``CompletionRequest``, not per-gateway.
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
