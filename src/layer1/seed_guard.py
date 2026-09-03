"""Hard-refusal preflight for e2e seed paths (ABS-430).

Doc 8 in the dev ``layer1`` database proved an e2e seed once ran against
dev. ABS-428 split the instances so that is *unlikely*; this module makes
it *impossible* even when someone points a seed at an arbitrary URL:
every ``scripts/seed_e2e_*.py`` (via the ``scripts/e2e_db_default``
bootstrap) and the e2e server entrypoint call
:func:`require_test_database` before touching the database, and abort
unless the target database's name ends in ``_test``.

Escape hatch: ``E2E_SEED_ALLOW_DB=<exact-db-name>`` whitelists exactly
one non-test database name for the current invocation. The value must
match the resolved name character-for-character — a wildcard or partial
match never passes.

Deliberately pure stdlib with no layer1/advisor imports: the seed
bootstrap runs it *before* ``layer1.config.get_settings`` (lru_cached)
ever resolves, so this module must not drag settings resolution in.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from urllib.parse import urlsplit

#: Env var naming the single non-test database a seed run may target.
ALLOW_ENV_VAR = "E2E_SEED_ALLOW_DB"

#: A database is a seedable test database iff its name ends with this.
TEST_DB_SUFFIX = "_test"

#: Default host port of the dedicated e2e Postgres instance (ABS-428).
E2E_PG_PORT_DEFAULT = "5433"


class SeedTargetRefusedError(SystemExit):
    """Seed preflight refused the target database.

    Subclasses :class:`SystemExit` carrying the refusal message, so an
    unhandled raise terminates a seed script with exit code 1 and the
    message on stderr — no per-script handling required — while unit
    tests can still assert on the specific type.
    """


def default_e2e_database_url(env: Mapping[str, str] | None = None) -> str:
    """URL of the dedicated e2e Postgres instance (ABS-428 default).

    Honors the per-worktree ``PG_PORT=543X`` convention; the database is
    always ``layer1_test``.
    """
    env = os.environ if env is None else env
    port = env.get("PG_PORT", E2E_PG_PORT_DEFAULT)
    return f"postgresql+psycopg://layer1:layer1@localhost:{port}/layer1_test"


def _url_port(url: str) -> str | None:
    """Host port of a SQLAlchemy/libpq-style URL, or ``None`` if absent.

    ``urlsplit().port`` raises on a malformed port; a URL we cannot parse
    carries no port we could disagree with, so it resolves to ``None``.
    """
    try:
        port = urlsplit(url).port
    except ValueError:
        return None
    return None if port is None else str(port)


def reconcile_database_url(
    env: Mapping[str, str] | None = None,
) -> tuple[str | None, str | None]:
    """Apply the ABS-501 precedence rule to ``DATABASE_URL`` / ``PG_PORT``.

    ``PG_PORT`` is the per-worktree e2e Postgres port: it is set by
    whoever owns the *currently running* stack. ``DATABASE_URL``, by
    contrast, is exported by ``scripts/e2e-up.sh`` (and by the Night
    Manager's agent runner) and then outlives the stack it described —
    a stale value pointing at a torn-down port. Preferring it, as every
    call site used to, misroutes seeds/tests at a dead database and
    reports a green branch as broken.

    So: **when both are set and their ports disagree, PG_PORT wins**, and
    the caller gets a warning naming both values. A disagreement is never
    an intent — an explicit ``DATABASE_URL`` for a *different* port is
    only ever reachable by unsetting ``PG_PORT``.

    Returns ``(effective_url, warning)``. ``effective_url`` is ``None``
    when ``DATABASE_URL`` is unset (nothing to reconcile — callers fall
    back to :func:`default_e2e_database_url` or their own default);
    ``warning`` is ``None`` unless a conflict was resolved.
    """
    env = os.environ if env is None else env
    url = env.get("DATABASE_URL")
    pg_port = env.get("PG_PORT")
    if not url or not pg_port:
        return url, None

    # sqlite has no port to reconcile.
    if urlsplit(url).scheme.partition("+")[0] == "sqlite":
        return url, None

    port = _url_port(url)
    if port is None or port == pg_port:
        return url, None

    parts = urlsplit(url)
    host = parts.hostname or "localhost"
    userinfo = ""
    if parts.username:
        userinfo = parts.username
        if parts.password:
            userinfo += f":{parts.password}"
        userinfo += "@"
    rewritten = parts._replace(netloc=f"{userinfo}{host}:{pg_port}").geturl()
    warning = (
        f"DATABASE_URL/PG_PORT conflict: inherited DATABASE_URL targets port "
        f"{port} but PG_PORT={pg_port}. PG_PORT wins — using {rewritten}. "
        f"A stale DATABASE_URL from a torn-down stack is the usual cause; "
        f"`unset DATABASE_URL` to silence this. [ABS-501]"
    )
    return rewritten, warning


def apply_pg_port_precedence(env: dict[str, str] | None = None) -> str | None:
    """Rewrite a conflicting ``DATABASE_URL`` in ``env`` in place (ABS-501).

    Thin imperative wrapper over :func:`reconcile_database_url` for the
    process environment: mutates ``DATABASE_URL`` when ``PG_PORT``
    overrides it and prints the warning to stderr so the redirect is
    never silent. Returns the effective URL (or ``None`` when unset).
    """
    target = os.environ if env is None else env
    url, warning = reconcile_database_url(target)
    if warning:
        import sys

        print(f"e2e env preflight: {warning}", file=sys.stderr)
        target["DATABASE_URL"] = url or ""
    return url


def resolve_database_name(url: str) -> str:
    """Extract the database name from a SQLAlchemy/libpq-style URL.

    ``postgresql+psycopg://user:pw@host:5432/layer1?sslmode=disable``
    resolves to ``layer1``. Returns ``""`` when the URL carries no
    database path — callers treat that as unverifiable and refuse.
    """
    return urlsplit(url).path.lstrip("/")


def _refusal_message(name: str, url_source: str) -> str:
    shown = name or "<none>"
    return (
        f"E2E SEED REFUSED: target database {shown!r} ({url_source}) is not "
        f"a test database.\n"
        f"Seed scripts and the e2e server only write to databases whose "
        f"name ends in {TEST_DB_SUFFIX!r} (e.g. layer1_test).\n"
        f"If you REALLY mean to seed {shown!r}, re-run with "
        f"{ALLOW_ENV_VAR}={shown} to whitelist that exact name. [ABS-430]"
    )


def require_test_database(
    url: str | None = None, *, env: Mapping[str, str] | None = None
) -> str:
    """Abort unless the effective target database is a test database.

    Resolution: explicit ``url`` argument if given, else ``DATABASE_URL``
    from ``env`` (default ``os.environ``). The resolved database name
    must end in ``_test``; otherwise the run is refused with
    :class:`SeedTargetRefusedError` (exit code 1) — unless
    ``E2E_SEED_ALLOW_DB`` equals the exact database name, which lets the
    run proceed with a stderr warning. sqlite URLs are exempt (local
    throwaway files, not the shared Postgres corpus).

    Returns the resolved database name on success.
    """
    env = os.environ if env is None else env
    if url is not None:
        effective, url_source = url, "explicit URL"
    else:
        effective, url_source = env.get("DATABASE_URL", ""), "from DATABASE_URL"

    # sqlite is exempt: it is always a throwaway local file (unit-test
    # harnesses point the e2e app at tmp_path sqlite DBs), never the
    # shared dev/prod Postgres corpus this guard exists to protect.
    scheme = urlsplit(effective).scheme.partition("+")[0] if effective else ""
    if scheme == "sqlite":
        return resolve_database_name(effective)

    name = resolve_database_name(effective) if effective else ""
    if name.endswith(TEST_DB_SUFFIX):
        return name

    allow = env.get(ALLOW_ENV_VAR)
    if name and allow == name:
        import sys

        print(
            f"e2e seed preflight: non-test database {name!r} explicitly "
            f"whitelisted via {ALLOW_ENV_VAR} — proceeding. [ABS-430]",
            file=sys.stderr,
        )
        return name

    raise SeedTargetRefusedError(_refusal_message(name, url_source))
