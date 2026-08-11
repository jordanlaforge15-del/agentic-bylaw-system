"""Gateway factory: provider dispatch, settings, and the billing guard (ABS-456).

The expensive thing this file protects is not the dispatch — it is the
two environment invariants around it. ``claude_code`` must refuse to
boot while ``ANTHROPIC_API_KEY`` is in the process environment (the CLI
would silently meter the turn, GH #43333), and ``claude_code_backend``
must stay out of ``sys.modules`` on the ``anthropic`` path so a fault in
the CLI backend can never break a production start.

Every test builds ``AdvisorLLMSettings`` explicitly rather than letting
it read env. The repo carries a real ``.env``, and pydantic-settings
would happily source ``ANTHROPIC_API_KEY`` from it, which would make the
results depend on the developer's working copy.
"""
from __future__ import annotations

import subprocess
import sys

import pytest

from advisor.llm.anthropic_backend import AnthropicGateway
from advisor.llm.registry import AdvisorLLMSettings, build_gateway


def _settings(**overrides: object) -> AdvisorLLMSettings:
    """Settings built from explicit values only — no env, no ``.env``.

    Two things this helper hides. ``_env_file=None`` disables the dotenv
    source for the instance, so a key sitting in the developer's
    ``.env`` can't leak into a case asserting on its absence. And every
    field on the model is aliased (``ADVISOR_LLM_PROVIDER`` and
    friends), so keyword arguments have to be translated to those
    aliases — pydantic drops an unaliased ``provider=`` on the floor
    silently, which would make these tests pass against the default
    provider without ever exercising the branch under test.
    """
    aliased = {
        AdvisorLLMSettings.model_fields[name].alias or name: value
        for name, value in overrides.items()
    }
    return AdvisorLLMSettings(_env_file=None, **aliased)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _no_api_key_in_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test from an environment with no API key.

    The guard reads ``os.environ`` directly, so a key exported in the
    developer's shell would otherwise turn the happy-path cases red.
    Tests that want the key set it back themselves.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


# -- provider dispatch --------------------------------------------------------


def test_claude_code_provider_returns_claude_code_gateway() -> None:
    from advisor.llm.claude_code_backend import ClaudeCodeGateway

    gateway = build_gateway(_settings(provider="claude_code"))

    assert isinstance(gateway, ClaudeCodeGateway)


def test_anthropic_provider_still_returns_anthropic_gateway() -> None:
    """Regression: adding a branch must not disturb the existing one."""
    gateway = build_gateway(
        _settings(provider="anthropic", anthropic_api_key="sk-ant-test")
    )

    assert isinstance(gateway, AnthropicGateway)


def test_anthropic_provider_without_key_still_raises() -> None:
    """Regression: the pre-existing missing-key failure is untouched."""
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY is required"):
        build_gateway(_settings(provider="anthropic", anthropic_api_key=None))


def test_unknown_provider_lists_both_supported_providers() -> None:
    with pytest.raises(ValueError) as excinfo:
        build_gateway(_settings(provider="bedrock"))

    message = str(excinfo.value)
    assert "'anthropic'" in message
    assert "'claude_code'" in message


# -- the billing guard --------------------------------------------------------


def test_claude_code_with_api_key_in_env_refuses_to_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point of the backend is dodging metered spend."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-oops")

    with pytest.raises(RuntimeError) as excinfo:
        build_gateway(_settings(provider="claude_code"))

    assert "ANTHROPIC_API_KEY" in str(excinfo.value)


def test_guard_fires_before_the_gateway_is_constructed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``RuntimeError`` from the guard, not from the CLI backend.

    If the branch built the gateway first and checked after, a broken
    CLI install would mask the billing failure with its own error and
    the operator would fix the wrong thing.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-oops")

    with pytest.raises(RuntimeError) as excinfo:
        build_gateway(_settings(provider="claude_code", claude_code_cli_path="/nope"))

    assert type(excinfo.value) is RuntimeError
    assert "GH #43333" in str(excinfo.value)


def test_api_key_in_dotenv_only_does_not_trip_the_guard() -> None:
    """A ``.env`` value never reaches the CLI subprocess, so it isn't a risk.

    Failing on it would block boots that were never going to be
    metered; the guard is deliberately scoped to ``os.environ``.
    """
    from advisor.llm.claude_code_backend import ClaudeCodeGateway

    settings = _settings(provider="claude_code", anthropic_api_key="sk-from-dotenv")

    assert isinstance(build_gateway(settings), ClaudeCodeGateway)


# -- settings -----------------------------------------------------------------


def test_claude_code_settings_fall_back_to_documented_defaults() -> None:
    settings = _settings(provider="claude_code")

    assert settings.claude_code_cli_path is None
    assert settings.claude_code_timeout_s == 300
    assert settings.claude_code_max_retries == 3


def test_claude_code_settings_read_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADVISOR_CLAUDE_CODE_CLI_PATH", "/opt/claude/bin/claude")
    monkeypatch.setenv("ADVISOR_CLAUDE_CODE_TIMEOUT_S", "45")
    monkeypatch.setenv("ADVISOR_CLAUDE_CODE_MAX_RETRIES", "1")

    settings = AdvisorLLMSettings(_env_file=None)

    assert settings.claude_code_cli_path == "/opt/claude/bin/claude"
    assert settings.claude_code_timeout_s == 45
    assert settings.claude_code_max_retries == 1


def test_settings_defaults_match_the_backends_own_defaults() -> None:
    """Pin the two copies together.

    ``registry`` hardcodes the defaults instead of importing them, to
    keep ``claude_code_backend`` out of the module's import graph. That
    duplication is only safe if something notices when it drifts.
    """
    from advisor.llm.claude_code_backend import (
        DEFAULT_MAX_RETRIES,
        DEFAULT_TIMEOUT_S,
    )

    settings = _settings()

    assert settings.claude_code_timeout_s == DEFAULT_TIMEOUT_S
    assert settings.claude_code_max_retries == DEFAULT_MAX_RETRIES


def test_settings_reach_the_constructed_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Configuring the knobs has to actually change the gateway."""
    from advisor.llm import claude_code_backend

    captured: dict[str, object] = {}

    class _Spy(claude_code_backend.ClaudeCodeGateway):
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)
            super().__init__(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(claude_code_backend, "ClaudeCodeGateway", _Spy)

    build_gateway(
        _settings(
            provider="claude_code",
            claude_code_cli_path="/opt/claude/bin/claude",
            claude_code_timeout_s=45,
            claude_code_max_retries=1,
        )
    )

    assert captured == {
        "cli_path": "/opt/claude/bin/claude",
        "timeout_s": 45,
        "max_retries": 1,
    }


# -- production isolation -----------------------------------------------------


def test_registry_does_not_import_the_cli_backend_at_module_scope() -> None:
    """Production never takes the ``claude_code`` branch; keep it that way.

    Run in a subprocess because this test session has almost certainly
    imported ``claude_code_backend`` already — asserting on the current
    ``sys.modules`` would prove nothing.
    """
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import advisor.llm.registry, sys; "
                "assert 'advisor.llm.claude_code_backend' not in sys.modules"
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
