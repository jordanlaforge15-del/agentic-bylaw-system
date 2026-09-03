"""Gateway factory: one provider, and the removal that made it one (ABS-522).

Before ABS-522 this file's expensive invariants were about a *second*
provider — ``claude_code``, the ``claude -p`` CLI backend — and the
billing guard that kept it off a metered key. That backend is gone, so
what is worth protecting changed shape:

* ``ADVISOR_LLM_PROVIDER=claude_code`` must **fail**, not quietly resolve
  to Anthropic. Coercion would move a deployment from subscription
  billing to metered billing silently, which is precisely the surprise
  the removal was meant to end.
* The CLI backend modules must be gone from the package, not merely
  unreferenced — a leftover ``claude_code_backend.py`` is a backend
  someone can still import and wire up.
* The layer1/layer2 ``claude -p`` usages must be **untouched**. They are
  ingest/enrichment subsystems, not the advisor's chat provider, and
  nothing in the eval evidence that condemned the advisor backend says
  anything about them. A future grep-driven cleanup that sweeps them up
  should turn this file red.

Every test builds ``AdvisorLLMSettings`` explicitly rather than letting
it read env. The repo carries a real ``.env``, and pydantic-settings
would happily source ``ANTHROPIC_API_KEY`` from it, which would make the
results depend on the developer's working copy.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from advisor.llm.anthropic_backend import AnthropicGateway
from advisor.llm.registry import (
    REMOVED_PROVIDERS,
    SUPPORTED_PROVIDER,
    UNMETERED_PROVIDERS,
    AdvisorLLMSettings,
    build_gateway,
    is_metered,
)


def _settings(**overrides: object) -> AdvisorLLMSettings:
    """Settings built from explicit values only — no env, no ``.env``.

    Two things this helper hides. ``_env_file=None`` disables the dotenv
    source for the instance, so a key sitting in the developer's
    ``.env`` can't leak into a case asserting on its absence. And every
    field on the model is aliased (``ADVISOR_LLM_PROVIDER`` and
    friends), so keyword arguments have to be translated to those
    aliases — pydantic drops an unaliased ``provider=`` on the floor
    silently, which would make these tests pass against the default
    provider without ever exercising the value under test.
    """
    aliased = {
        AdvisorLLMSettings.model_fields[name].alias or name: value
        for name, value in overrides.items()
    }
    return AdvisorLLMSettings(_env_file=None, **aliased)  # type: ignore[arg-type]


# -- provider resolution ------------------------------------------------------


def test_anthropic_provider_returns_anthropic_gateway() -> None:
    gateway = build_gateway(
        _settings(provider="anthropic", anthropic_api_key="sk-ant-test")
    )

    assert isinstance(gateway, AnthropicGateway)


def test_default_provider_is_anthropic() -> None:
    """An operator who sets nothing gets the one provider that ships."""
    settings = _settings(anthropic_api_key="sk-ant-test")

    assert settings.provider == SUPPORTED_PROVIDER
    assert isinstance(build_gateway(settings), AnthropicGateway)


def test_anthropic_provider_without_key_still_raises() -> None:
    """Regression: the pre-existing missing-key failure is untouched."""
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY is required"):
        build_gateway(_settings(provider="anthropic", anthropic_api_key=None))


# -- the removal ---------------------------------------------------------------


def test_claude_code_provider_no_longer_resolves_to_a_gateway() -> None:
    """ABS-522 acceptance criterion #1, stated as a test.

    It has to *raise*, not fall back. A deployment carrying
    ``claude_code`` chose it to avoid metered spend; silently handing it
    the metered gateway would bill money the operator did not agree to.
    """
    with pytest.raises(ValueError) as excinfo:
        build_gateway(
            _settings(provider="claude_code", anthropic_api_key="sk-ant-test")
        )

    message = str(excinfo.value)
    assert "claude_code" in message
    # The operator set it deliberately; tell them what happened to it.
    assert "ABS-522" in message
    assert "billing" in message


def test_unknown_provider_raises_and_names_the_supported_one() -> None:
    with pytest.raises(ValueError) as excinfo:
        build_gateway(_settings(provider="bedrock", anthropic_api_key="sk-ant-test"))

    message = str(excinfo.value)
    assert "bedrock" in message
    assert "'anthropic'" in message


def test_provider_rejection_beats_the_missing_key_check() -> None:
    """Report the removed provider, not a key that path never needed.

    ``claude_code`` deployments have no ``ANTHROPIC_API_KEY`` by
    construction — that was the point. If the key check ran first, every
    one of them would boot-fail with "ANTHROPIC_API_KEY is required" and
    the operator would fix the wrong thing.
    """
    with pytest.raises(ValueError, match="claude_code"):
        build_gateway(_settings(provider="claude_code", anthropic_api_key=None))


@pytest.mark.parametrize(
    "module",
    [
        "advisor.llm.claude_code_backend",
        "advisor.llm.claude_code_translation",
    ],
)
def test_cli_backend_modules_are_gone_from_the_package(module: str) -> None:
    """Unreferenced is not removed — the files themselves must be gone."""
    assert importlib.util.find_spec(module) is None


def test_registry_carries_the_removal_reason() -> None:
    """The 'why' travels with the code, not only with the Linear issue."""
    assert set(REMOVED_PROVIDERS) == {"claude_code"}
    assert "ABS-522" in REMOVED_PROVIDERS["claude_code"]


def test_registry_module_imports_cleanly_in_a_fresh_interpreter() -> None:
    """Nothing left behind imports a module that no longer exists.

    Run in a subprocess: this session has plenty of modules loaded
    already, so an ``ImportError`` on a cold start is exactly the thing
    an in-process assertion cannot see.
    """
    result = subprocess.run(
        [sys.executable, "-c", "import advisor.llm.registry"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


# -- production stays metered, on purpose (ABS-515) ----------------------------


def test_production_compose_still_defaults_to_anthropic() -> None:
    """ABS-515 changed the *eval* default, not the production one.

    The issue asked for test/eval runs to stop billing metered credits by
    default. Production is the opposite case: it must keep the provider that
    answers best (0/8 vs 3/8 on the run that condemned the CLI backend), and
    a container started with no ``ADVISOR_LLM_PROVIDER`` at all must still
    land on it rather than on an "unsupported ''" boot failure.
    """
    compose = (_REPO_ROOT / "docker-compose.production.yml").read_text(
        encoding="utf-8"
    )

    assert f"ADVISOR_LLM_PROVIDER: ${{ADVISOR_LLM_PROVIDER:-{SUPPORTED_PROVIDER}}}" in (
        compose
    ), "production compose no longer defaults the provider to anthropic"


def test_the_supported_provider_is_metered() -> None:
    """The one provider that ships bills per token, and says so.

    ``is_metered`` is what ``GET /healthz`` reports and what
    ``scripts/run_test_prompts.py`` gates an eval run on. If the only real
    provider ever read as unmetered, the runner's consent gate would open
    silently for every run — the exact failure ABS-515 closed.
    """
    assert is_metered(SUPPORTED_PROVIDER) is True
    assert SUPPORTED_PROVIDER not in UNMETERED_PROVIDERS


def test_unknown_and_removed_providers_are_treated_as_metered() -> None:
    """Fail closed: only a name we know to be free reads as free."""
    assert is_metered("claude_code") is True
    assert is_metered("bedrock") is True
    assert is_metered(None) is True
    assert is_metered("mock") is False


# -- deliberately out of scope: layer1 / layer2 --------------------------------

# ABS-522 removed the advisor's ``claude -p`` chat provider on eval
# evidence about *advisor answer quality*. These modules also shell out
# to ``claude -p``, for ingest and enrichment work the evidence says
# nothing about. Leaving them is a decision, not an oversight, and this
# list is here so a later grep-driven sweep has to argue with a test.
_LAYER_CLAUDE_CODE_USAGES = (
    "src/layer1/_claude_code_client.py",
    "src/layer1/pipeline/audit.py",
    "src/layer1/learn_city_cmd.py",
    "src/layer2/llm/clients.py",
    "src/layer2/cli.py",
)

_REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("relative_path", _LAYER_CLAUDE_CODE_USAGES)
def test_layer1_and_layer2_claude_code_usage_was_left_alone(
    relative_path: str,
) -> None:
    path = _REPO_ROOT / relative_path

    assert path.is_file(), (
        f"{relative_path} is missing. ABS-522 scoped its removal to the "
        "advisor's chat provider; layer1/layer2 ingest and enrichment were "
        "explicitly out of scope. If this file moved on purpose, update "
        "_LAYER_CLAUDE_CODE_USAGES — don't delete the guard."
    )
    # Case-insensitive: the reference is a bare ``claude -p`` in some of
    # these and a ``ClaudeCodeLLMClient`` symbol in others.
    assert "claude" in path.read_text(encoding="utf-8").lower(), (
        f"{relative_path} no longer references the Claude Code CLI. ABS-522 "
        "did not authorise that; confirm it was an intentional, separately "
        "justified change."
    )
