"""Factory for constructing the configured LLMGateway.

Callers ask ``build_gateway()`` and get back the right backend per
deployment. Production picks Anthropic; tests pick Mock; future
deployments can pick OpenAI / Bedrock / on-prem by adding a branch.

The factory reads ``AdvisorLLMSettings`` so deployment changes happen via
env vars without code changes.

Providers
---------
``anthropic``
    The metered Messages API. Requires ``ANTHROPIC_API_KEY``.
``claude_code``
    The ``claude -p`` CLI, billed against an operator's Claude Code
    subscription rather than per token. Requires the *absence* of
    ``ANTHROPIC_API_KEY`` — see :func:`build_gateway`.

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

import os
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
    # ``claude_code`` provider knobs. Only read when
    # ``ADVISOR_LLM_PROVIDER=claude_code``; harmless on the Anthropic
    # path. The defaults are literals rather than imports from
    # ``advisor.llm.claude_code_backend`` on purpose — importing that
    # module here would defeat the lazy import in ``build_gateway`` and
    # pull the CLI backend into every production boot. They are pinned
    # to the backend's ``DEFAULT_TIMEOUT_S`` / ``DEFAULT_MAX_RETRIES``
    # by a test so the two can't drift.
    # ------------------------------------------------------------------

    # Path to the ``claude`` binary. ``None`` means "resolve ``claude``
    # on PATH", which is what a normal Claude Code install gives you;
    # set it when the CLI lives somewhere the service's PATH misses
    # (e.g. a container that installs it under /opt).
    claude_code_cli_path: str | None = Field(
        default=None, alias="ADVISOR_CLAUDE_CODE_CLI_PATH"
    )
    # Per-subprocess wall-clock budget, seconds. A research turn through
    # the CLI runs 5-30s typically; 300 leaves room for a long one
    # without letting a wedged process pin a worker indefinitely.
    claude_code_timeout_s: int = Field(
        default=300, alias="ADVISOR_CLAUDE_CODE_TIMEOUT_S"
    )
    # Total attempts, not retries-after-the-first: 3 means at most three
    # subprocess invocations, so worst-case latency for one turn is
    # ``max_retries * timeout_s``.
    claude_code_max_retries: int = Field(
        default=3, alias="ADVISOR_CLAUDE_CODE_MAX_RETRIES"
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

    Raises ``RuntimeError`` when the provider is selected but its
    environment is wrong: no key for ``anthropic``, a key present for
    ``claude_code`` (see :func:`_assert_no_api_key_billing`).
    """
    s = settings or get_settings()
    if s.provider == "anthropic":
        if not s.anthropic_api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is required when ADVISOR_LLM_PROVIDER=anthropic"
            )
        return AnthropicGateway(api_key=s.anthropic_api_key)
    if s.provider == "claude_code":
        _assert_no_api_key_billing()
        # Imported here, not at module scope. Production pins
        # ``ADVISOR_LLM_PROVIDER=anthropic`` and never takes this
        # branch, so the CLI backend and its imports must not be able
        # to break a production boot. A top-level import would run this
        # module's code on every start for no benefit.
        from advisor.llm.claude_code_backend import ClaudeCodeGateway

        return ClaudeCodeGateway(
            cli_path=s.claude_code_cli_path,
            timeout_s=s.claude_code_timeout_s,
            max_retries=s.claude_code_max_retries,
        )
    raise ValueError(
        f"unknown ADVISOR_LLM_PROVIDER {s.provider!r}; "
        "supported: 'anthropic', 'claude_code'. "
        "Add a branch in registry.build_gateway."
    )


def _assert_no_api_key_billing() -> None:
    """Refuse to boot the CLI backend with an API key in the environment.

    ``claude -p`` in headless mode has a known defect
    (anthropics/claude-code#43333 and related) where the presence of
    ``ANTHROPIC_API_KEY`` silently routes the turn to **API billing**
    instead of the operator's subscription — one report cited >$1,800 of
    unexpected charges over two days. Avoiding metered spend is the
    entire reason this backend exists, so an environment that would
    silently meter is a startup failure, not a runbook note.

    ``os.environ`` rather than ``settings.anthropic_api_key`` is
    deliberate: the check has to match what the CLI subprocess actually
    inherits. ``AdvisorLLMSettings`` also reads ``.env``, and a value
    that only lives in a ``.env`` file never reaches the subprocess —
    failing on it would block boots that were never at risk, while
    passing on a real process-env key would miss the one case that is.
    """
    if os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "ANTHROPIC_API_KEY is set with ADVISOR_LLM_PROVIDER=claude_code — "
            "refusing to start; the CLI would bill API rates (see GH #43333)."
        )
