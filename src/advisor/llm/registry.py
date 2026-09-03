"""Factory for constructing the advisor's LLMGateway.

Callers ask ``build_gateway()`` and get back the Anthropic Messages API
gateway. There is exactly one provider; the factory reads
``AdvisorLLMSettings`` so model selection and credentials happen via env
vars without code changes.

One provider (ABS-522)
----------------------
A second provider used to live here — ``claude_code``, which drove the
``claude -p`` CLI so turns billed against an operator's Claude Code
subscription instead of per token. It was removed because a controlled
experiment (``evals/runs/zone-typology-v3`` vs
``evals/runs/zone-typology-all8``, same code, same corpus, same attested
expectations, only ``ADVISOR_LLM_PROVIDER`` differing) showed it did not
just cost less, it *answered worse*: 0/8 golden passes against 3/8, with
the CLI backend stopping its research roughly four times sooner and
omitting figures the by-law requires. Production was always pinned to
``anthropic``, so it shipped nothing while making evals unreadable and
keeping a live metered-billing hazard on the boot path.

``ADVISOR_LLM_PROVIDER`` survives the removal as a *validated pin*, not a
selector: ``build_gateway`` has no branch, and any value other than
``anthropic`` is a hard startup failure. Silently coercing a stale
``claude_code`` to ``anthropic`` would flip a deployment from
subscription billing to metered billing without saying so, which is the
exact class of surprise this issue was opened to end.

Note on scope: ``claude -p`` is still used by the layer1/layer2 ingest and
enrichment subsystems (``src/layer1/_claude_code_client.py``,
``src/layer1/pipeline/audit.py``, ``src/layer1/learn_city_cmd.py``,
``src/layer2/llm/clients.py``, ``src/layer2/cli.py``). Those are not the
advisor's chat provider and nothing above says anything about their
quality — they were deliberately left alone. See
``tests/advisor/llm/test_registry.py`` for the test that records this.

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

#: The only accepted value of ``ADVISOR_LLM_PROVIDER``.
SUPPORTED_PROVIDER = "anthropic"

#: Providers this repo used to support, mapped to the issue that removed
#: them. A deployment still carrying one of these values gets told what
#: happened rather than a bare "unknown provider".
REMOVED_PROVIDERS = {
    "claude_code": (
        "removed in ABS-522 — the `claude -p` CLI backend answered worse "
        "than the API backend (0/8 vs 3/8 golden passes on an otherwise "
        "identical run) and was never on the production boot path"
    ),
}


#: Gateway names (``LLMGateway.name``) whose turns cost nothing per
#: token. Everything else is treated as metered — see ``is_metered``.
UNMETERED_PROVIDERS = frozenset({"mock"})


def is_metered(provider: str | None) -> bool:
    """Does a turn on this gateway bill per token? (ABS-515)

    Fail-closed on purpose: an unrecognised or missing provider name
    counts as metered. This answer is consumed by ``GET /healthz`` and,
    through it, by ``scripts/run_test_prompts.py``, which refuses to
    start an eval run against a metered advisor without an explicit
    ``--allow-metered``. Guessing "free" for something we cannot
    identify would spend the operator's money on the strength of a
    guess; guessing "metered" costs one flag.

    Since ABS-522 there is exactly one real provider and it is metered,
    so in practice this returns ``True`` for every advisor that can
    actually answer a question, and ``False`` only for the mock gateway
    the e2e stack runs on. That is the honest state of the world, not a
    degenerate case: the point of the check is that the runner can no
    longer *fail to notice* which one it is talking to.
    """
    return provider not in UNMETERED_PROVIDERS


class AdvisorLLMSettings(BaseSettings):
    """LLM-related settings for the advisor app.

    Values come from environment variables (or .env).
    """

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Not a selector — a pin. ``build_gateway`` rejects anything but
    # ``anthropic``; see the module docstring for why the var survives
    # the removal of the second provider instead of being deleted.
    provider: str = Field(default=SUPPORTED_PROVIDER, alias="ADVISOR_LLM_PROVIDER")
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
    """Construct the advisor's gateway.

    Tests usually skip this and instantiate ``MockGateway`` directly so
    they can script responses without env-var setup. Production callers
    pass nothing and pick up settings from env.

    The same gateway serves both the main model and the classifier —
    model selection is per-``CompletionRequest``, not per-gateway.

    Raises ``ValueError`` when ``ADVISOR_LLM_PROVIDER`` names anything
    other than ``anthropic``, and ``RuntimeError`` when the key it needs
    is missing.
    """
    s = settings or get_settings()
    if s.provider != SUPPORTED_PROVIDER:
        raise ValueError(_unsupported_provider_message(s.provider))
    if not s.anthropic_api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is required when ADVISOR_LLM_PROVIDER=anthropic"
        )
    return AnthropicGateway(api_key=s.anthropic_api_key)


def _unsupported_provider_message(provider: str) -> str:
    """Explain a rejected ``ADVISOR_LLM_PROVIDER`` value.

    A removed provider gets its removal reason, because the operator who
    set it did so on purpose and the useful answer is "that backend is
    gone and here is why", not "unknown".
    """
    head = f"unsupported ADVISOR_LLM_PROVIDER {provider!r}; "
    removal = REMOVED_PROVIDERS.get(provider)
    if removal is not None:
        return (
            f"{head}that provider was {removal}. "
            f"Set ADVISOR_LLM_PROVIDER={SUPPORTED_PROVIDER!r} (or unset it) — "
            "note that this switches billing to the metered Messages API."
        )
    return (
        f"{head}{SUPPORTED_PROVIDER!r} is the only supported provider. "
        "Add a branch in registry.build_gateway to support another."
    )
