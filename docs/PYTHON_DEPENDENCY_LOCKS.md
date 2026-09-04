# Python dependency locks

> Status: active. Introduced by ABS-532 after the anthropic 1.x production
> outage (ABS-531).

`pyproject.toml` declares what the project *depends on*. `requirements/*.txt`
declares what it *installs*. This document explains why those are two different
files, how to regenerate the second, and how to upgrade a dependency on purpose.

## The problem this replaces

`pyproject.toml` declares floors — `fastapi>=0.115`, `pydantic>=2.6`,
`shapely>=2.0`. A floor is not a version. The version that ends up installed is
decided by *when pip last ran*, and there were five places where pip ran
independently:

| Site | What it used to do |
| -- | -- |
| `Dockerfile.advisor` | `pip install ".[advisor]"` into an **empty** venv, on every image build |
| `scripts/dev-setup.sh` | `pip install -e ".[dev,advisor]"` into a venv created once, months ago |
| `.github/workflows/ci.yml` (Python tests) | `pip install -e ".[dev,advisor]"` on a fresh runner |
| `.github/workflows/ci.yml` (golden gate) | `pip install -e "."` on a fresh runner |
| `.github/workflows/dependency-audit.yml` | `pip install ".[dev]"` on a fresh runner |

Five resolutions, five possible answers, nothing recording any of them and
nothing comparing them.

That is not hypothetical. `anthropic>=0.40` let the 1.x major into a production
image build while the dev venv kept the `0.100.0` it had installed in May and
never revisited — 0.100.0 satisfies the floor, so pip had no reason to touch it.
anthropic 1.x removed the sampling parameters from the Messages API, so the
`temperature` kwarg `AnthropicGateway._to_anthropic_params` sends on every
request became a `TypeError`. Every case-open in production failed. The whole
test suite was green, because the tests ran against the older resolution.

Exact-pinning the ~40 names in `pyproject.toml` would not have been enough:
`httpx`, `starlette`, `anyio`, `pydantic-core` and the rest are transitive and
would have stayed unpinned, so the identical failure recurs one level down.

## The mechanism

**`uv pip compile --universal --generate-hashes`**, driven by
[`scripts/lock-python-deps.sh`](../scripts/lock-python-deps.sh), emitting plain
pip-readable requirements files that every install site reads with
`pip install --require-hashes`.

### Why `uv`, and why universal

The deploy target is linux / CPython 3.11 (`Dockerfile.advisor`, CI's
`python-version`). The local dev venv is macOS / CPython 3.12. A hash-pinned
lock produced by `pip-compile` is baked to the interpreter and platform that
compiled it and will not reliably install on the other one. That leaves three
options, and the ticket named all three:

| Option | Rejected because |
| -- | -- |
| Compile for linux/3.11, give dev a separate constraints-without-hashes file | Two files resolved independently → the dev venv and the image drift apart again. That is the original bug, one level down. |
| Move the dev venv to 3.11 | Fixes the symptom by shrinking the supported surface, and does nothing for a future contributor on a different platform. |
| **A universal resolver (`uv`)** | **Chosen.** |

`uv pip compile --universal` resolves for *every* platform and *every* Python
version at or above the project's `requires-python`, in one pass. Where a
requirement genuinely must differ it forks into marker-guarded lines — see the
two `numpy` entries in each lock, one for `python_full_version < '3.12'` and one
for `>= '3.12'`. Every platform's wheel hash is emitted, so a single committed
file installs identically on linux/3.11 and macOS/3.12.

### `uv` is a compile-time tool only

Nothing installs with `uv`. All five sites run
`pip install --require-hashes -r requirements/<lock>.txt`. The locks are
ordinary pip requirements files; no install site grows a new tool dependency,
and the deployed image never sees `uv`. The resolver's own version is pinned in
`scripts/lock-python-deps.sh` (`UV_VERSION`) — an unpinned locker is itself an
unlocked dependency.

### `--require-hashes` is the load-bearing flag

Not `--no-deps`. `--require-hashes` makes pip **refuse** any requirement in the
file that lacks an exact version and a matching hash, and refuse to install
anything not listed. So a transitive dependency cannot slip in unrecorded, and a
tampered-with artifact cannot be substituted for a recorded one.

## The three locks

| File | Extras | Installed by |
| -- | -- | -- |
| `requirements/base.txt` | none | `ci.yml` golden-gate job |
| `requirements/runtime.txt` | `[advisor]` | `Dockerfile.advisor` |
| `requirements/dev.txt` | `[dev,advisor]` | `dev-setup.sh`, `ci.yml` Python tests, `dependency-audit.yml`, `make install` |

`base.txt` exists specifically for the golden-gate job, whose imports are
required to stay inside the no-extras dependency set. That gate once died
importing FastAPI and reported a hold it had never evaluated; `base.txt` is the
machine-readable form of that constraint. Pointing the gate at `runtime.txt`
would hand it FastAPI and silently delete it.

All three are regenerated together by one script invocation, so a package
appearing in more than one lock carries the same version in all of them.
`tests/test_dependency_locks.py` asserts that, and asserts that every install
site still points at a lock.

### What is deliberately not locked

- **`[parsers]`** (Camelot, PaddleOCR, Docling) — opt-in local-ingest
  toolchain, on no deploy path, and heavy and platform-fragile enough that
  locking it would stall every regeneration of the files that do ship.
  `dev-setup.sh --with-parsers` installs it unlocked but passes the dev lock as
  a pip *constraints* file, so a parsers dependency that conflicts with a
  shipped version fails loudly instead of silently upgrading it.
- **`[ingest]`, `[bim]`, `[mcp]`** — no install site uses them today. Add a lock
  the moment one does.
- **`setuptools` / `wheel` / `pip`** in the image build — build backends, not
  runtime imports. `Dockerfile.advisor` pins `setuptools>=83` for its own
  reasons (ABS-401, PYSEC-2026-3447).

## Everyday use

```sh
make install          # install the dev lock into the active venv
./scripts/dev-setup.sh   # first-time venv setup, installs the dev lock
```

Neither resolves anything. If a version surprises you, it is in the lock and it
is in the git history.

## Regenerating after a pyproject.toml change

Adding, removing or re-flooring a dependency changes `pyproject.toml`; the locks
do not follow automatically.

```sh
./scripts/lock-python-deps.sh
git add pyproject.toml requirements/
git commit -m "[ABS-nnn] add <dep>"
```

Without `--upgrade` the script **holds every version the committed locks already
name** that is still satisfiable, so only what your edit forces will move. That
keeps the diff readable: a one-package addition shows up as one package plus its
new transitive deps, not a 900-line churn.

CI's **Python lock drift** job runs `./scripts/lock-python-deps.sh --check` and
fails if the committed locks differ from a fresh compile. It is not an upgrade
check — a new release on PyPI does not turn it red, because pins are held. It
goes red on exactly two things: a `pyproject.toml` edit that was not followed by
a regeneration, and a hand-edited lock.

**Do not hand-edit `requirements/*.txt`.** They are generated. The drift job
will catch it, and the hashes make a partial hand-edit an install failure rather
than a subtle wrong version.

## Upgrading a dependency on purpose

This is the procedure the outage existed for the absence of. An upgrade is an
explicit act with its own commit and its own test evidence — never a side
effect of a rebuild.

### 1. Upgrade one thing, or everything, deliberately

```sh
# Everything that can move, moves:
./scripts/lock-python-deps.sh --upgrade

# Or just one package — edit its floor in pyproject.toml first if the new
# version needs a new floor, then:
./scripts/lock-python-deps.sh
```

For a single package with no floor change, the narrow form is to bump its pin in
`pyproject.toml` (as `[advisor]` currently does for `anthropic==0.100.0`) and
regenerate.

### 2. Read the diff

```sh
git diff --stat requirements/
git diff requirements/runtime.txt | grep -E '^[-+][a-z0-9]'
```

That last command prints just the version lines. Look for **major** bumps —
those are the ones that remove APIs. `anthropic 0.100.0 → 1.3.0` was one line in
a diff nobody had to read, because there was no diff.

### 3. Prove it on the code, not just on the resolver

The lock guarantees everyone installs the same thing. It does **not** guarantee
the new thing works.

```sh
make install        # reinstall the venv from the new lock
make test           # Python unit tests
make e2e            # full Playwright suite — see docs/E2E_TESTING.md
```

The e2e suite is what checks the *installed* SDK surface rather than the
project's assumptions about it: `web/e2e/functional/abs531-anthropic-sdk-param-compat.spec.ts`
compares the kwargs the gateway emits against the real `AsyncMessages.create`
signature, and `web/e2e/functional/abs532-dependency-lock.spec.ts` asserts the
running stack's installed versions match the committed lock. Both go red on the
class of change that took prod down.

For a major bump of anything in `[advisor]`, also build the image — the lock is
shared, but the image build is the only place the runtime install is exercised
end to end:

```sh
docker build -f Dockerfile.advisor -t bylaw-advisor:lock-check .
```

### 4. Commit the upgrade on its own

Version bumps do not belong in a feature commit. A reviewer should be able to
answer "what moved, and why" from the commit message and the diff alone.

## Troubleshooting

**`pip` refuses to install: "Hashes are required in --require-hashes mode"**
Something is being installed that is not in the lock. Almost always a missing
`--no-deps` on the project install (`pip install -e . --no-deps`) — without it
pip re-reads `pyproject.toml` and tries to resolve the floors again.

**A locked wheel has no artifact for my platform**
Universal resolution emits hashes for the platforms uv knows about at compile
time. If you are on something unusual, regenerate with the pinned uv on your own
machine and check whether the diff is empty; if it is not, that is a real gap
worth a ticket rather than a local workaround.

**The drift job is red and I did not touch `pyproject.toml`**
Check `UV_VERSION` in `scripts/lock-python-deps.sh` against what your local
`.uv-lock-venv` holds. The script rebuilds that venv when the pin moves; a stale
one compiles with the wrong resolver.

## Related

- ABS-531 — the anthropic 1.x outage and the `anthropic==0.100.0` pin.
- [docs/E2E_TESTING.md](E2E_TESTING.md) — running the suite the upgrade
  procedure depends on.
- `web/package-lock.json` — the JS side, locked by npm since the start. This
  document brings the Python side to parity.
