// ABS-532 — the running stack's installed Python packages must match the
// committed lock.
//
// WHAT BROKE, AND WHY A LOCK ALONE IS NOT THE FIX. `pyproject.toml` declared
// floors (`anthropic>=0.40`, `fastapi>=0.115`, …), never versions, and five
// places resolved those floors independently: the image build (into an EMPTY
// venv, on every build), dev-setup.sh (into a venv created once, months
// earlier), two CI jobs, and the vulnerability audit. Nothing recorded what any
// of them landed on and nothing compared them. anthropic 1.x reached production
// that way while the suite ran green against the 0.100.0 the dev venv still
// held; 1.x had removed the sampling parameters, so `temperature` became a
// TypeError on every case-open (ABS-531).
//
// `requirements/*.txt` now pins every version with hashes, and every install
// site reads it. But a lock file is a *claim*. The thing that actually decides
// what runs is the venv, and a lock that nothing compares against the venv is
// the same trust-without-evidence that produced the outage — one layer up. Ways
// the claim silently stops being true, none of which a lock file notices:
//
//   * `pip install -e .` without `--no-deps` after the locked install. pip
//     re-reads pyproject.toml, sees the floors, and is free to upgrade straight
//     past every pin it just honoured.
//   * A `pip install <anything>` run by hand in the venv, which resolves and
//     upgrades whatever it likes.
//   * A venv that predates the current lock and was simply never reinstalled —
//     precisely the dev-venv half of the original bug.
//
// So this spec asks the running stack, not the repo: POST
// /v1/_test/installed-versions reports `importlib.metadata` for the interpreter
// serving the e2e suite, and every version is compared against
// requirements/dev.txt. It needs no key, no network and no model call.
//
// It deliberately does not go through any UI. There is no user-facing surface
// that renders a dependency version, and inventing one to have something to
// click would test the invention rather than the risk. This follows the pattern
// ABS-531 established for the same class of problem
// (abs531-anthropic-sdk-param-compat.spec.ts).

import { readFileSync } from "node:fs";
import path from "node:path";

import { expect, test } from "@playwright/test";

const E2E_API_URL = process.env.E2E_API_URL ?? "http://127.0.0.1:8001";
const INSTALLED_VERSIONS = `${E2E_API_URL}/v1/_test/installed-versions`;

// web/e2e/functional/ -> repo root
const REPO_ROOT = path.resolve(__dirname, "..", "..", "..");
const DEV_LOCK = path.join(REPO_ROOT, "requirements", "dev.txt");

type InstalledVersions = {
  python_version: string;
  python_implementation: string;
  platform: string;
  executable: string;
  distributions: Record<string, string>;
};

/** PEP 503 name normalisation — `PyYAML`, `pyyaml` and `py_yaml` are one name. */
function normalise(name: string): string {
  return name.replace(/[-_.]+/g, "-").toLowerCase();
}

type Lock = {
  /** normalised name -> every version the lock pins it to */
  pins: Map<string, Set<string>>;
  /**
   * Names whose pin carries a PEP 508 environment marker, so whether the
   * package installs at all depends on the interpreter and platform.
   */
  conditional: Set<string>;
};

/**
 * Parse a uv-generated lock into name -> versions.
 *
 * Two things make this less trivial than it looks, both consequences of
 * `--universal`.
 *
 * A pin can be *forked*: `uv pip compile --universal` emits a requirement more
 * than once when it genuinely must differ across environments — `numpy` is
 * pinned twice, once under `python_full_version < '3.12'` and once under
 * `>= '3.12'`. That fork is the mechanism that lets ONE committed file install
 * on both the linux/CPython 3.11 deploy target and the macOS/CPython 3.12 dev
 * venv, which is why uv was chosen over pip-compile
 * (docs/PYTHON_DEPENDENCY_LOCKS.md). So a name maps to a *set* of versions.
 *
 * A pin can also be *conditional* without being forked: `colorama` and `tzdata`
 * carry `sys_platform == 'win32'`, and `greenlet` a platform_machine list that
 * excludes Apple silicon. Those are correctly absent from this venv.
 *
 * Rather than reimplement PEP 508 marker evaluation inside a test — where a bug
 * in the evaluator would silently weaken every assertion below — markered names
 * are exempted from the "must be installed" check only. If such a package IS
 * installed it must still match one of its pins, which is the direction that
 * matters: a wrong version is the failure mode, a legitimately-skipped one is
 * not. Duplicate pins with no marker to separate them would be a corrupt lock
 * and are asserted against directly, so the leniency has nothing to hide
 * behind.
 */
function readLock(filePath: string): Lock {
  const pins = new Map<string, Set<string>>();
  const conditional = new Set<string>();
  const markerless = new Map<string, number>();

  for (const line of readFileSync(filePath, "utf8").split("\n")) {
    const match = /^([A-Za-z0-9._-]+)==([^\s;\\]+)(.*)$/.exec(line);
    if (!match) continue;

    const name = normalise(match[1]);
    const version = match[2];
    if (match[3].includes(";")) {
      conditional.add(name);
    } else {
      markerless.set(name, (markerless.get(name) ?? 0) + 1);
    }

    const existing = pins.get(name);
    if (existing) existing.add(version);
    else pins.set(name, new Set([version]));
  }

  const unmarkedDuplicates = [...markerless]
    .filter(([, count]) => count > 1)
    .map(([name]) => name)
    .sort();

  expect(
    unmarkedDuplicates,
    `requirements/dev.txt pins these packages more than once with no environment ` +
      `marker to separate the pins: ${unmarkedDuplicates.join(", ")}. That is not a ` +
      `universal-resolution fork, it is a corrupt lock — pip would install ` +
      `whichever it saw last. Regenerate with ./scripts/lock-python-deps.sh.`,
  ).toEqual([]);

  return { pins, conditional };
}

async function fetchInstalled(
  request: import("@playwright/test").APIRequestContext,
): Promise<InstalledVersions> {
  const response = await request.post(INSTALLED_VERSIONS, { data: {} });
  expect(
    response.status(),
    `installed-versions probe failed: ${await response.text()}`,
  ).toBe(200);
  return (await response.json()) as InstalledVersions;
}

test.describe("ABS-532 Python dependency lock", () => {
  test("the lock the stack is checked against is real and hash-pinned", async () => {
    // Guards the rest of the file. If the lock failed to parse — renamed,
    // emptied, reformatted by a resolver change — every comparison below would
    // trivially pass against an empty pin map and report a green it had not
    // earned. That failure mode is exactly what let the outage through.
    const { pins } = readLock(DEV_LOCK);

    expect(
      pins.size,
      `requirements/dev.txt parsed to ${pins.size} pinned packages. It should hold ` +
        `the full [dev,advisor] closure — dozens of entries. Generate it with ` +
        `./scripts/lock-python-deps.sh.`,
    ).toBeGreaterThan(50);

    // Sampled across the three groups that must all be present: a base runtime
    // dep, an [advisor] dep, and a transitive dep nobody declared. The last is
    // the point — exact-pinning only the ~40 declared names would have left
    // httpx and friends free to move, and the identical failure recurs one
    // level down.
    for (const required of ["sqlalchemy", "fastapi", "anthropic", "httpx", "starlette"]) {
      expect(pins.has(required), `requirements/dev.txt does not pin ${required}`).toBe(
        true,
      );
    }

    const text = readFileSync(DEV_LOCK, "utf8");
    expect(
      text.includes("--hash=sha256:"),
      "requirements/dev.txt carries no hashes. pip --require-hashes would reject it, " +
        "so every install site is broken.",
    ).toBe(true);
  });

  test("every locked package is installed at its locked version", async ({ request }) => {
    const { pins, conditional } = readLock(DEV_LOCK);
    const installed = await fetchInstalled(request);

    const mismatched: string[] = [];
    const absent: string[] = [];

    for (const [name, versions] of pins) {
      const actual = installed.distributions[name];
      if (actual === undefined) {
        // A markered pin can be legitimately absent — `colorama` is win32-only.
        // An unconditional one cannot: the lock says it installs everywhere the
        // project runs, so its absence means the venv was built from something
        // other than this lock.
        if (!conditional.has(name)) absent.push(name);
        continue;
      }
      if (!versions.has(actual)) {
        mismatched.push(`${name}: installed ${actual}, lock pins ${[...versions].join(" or ")}`);
      }
    }

    expect(
      mismatched,
      `the running stack does not match requirements/dev.txt:\n  ` +
        `${mismatched.join("\n  ")}\n\n` +
        `This venv (${installed.executable}, Python ${installed.python_version}) was not ` +
        `installed from the current lock, or something re-resolved on top of it. ` +
        `Reinstall with \`make install\`, and if a version genuinely needs to move, ` +
        `that is a deliberate upgrade — see docs/PYTHON_DEPENDENCY_LOCKS.md.`,
    ).toEqual([]);

    expect(
      absent,
      `requirements/dev.txt pins these unconditionally but they are not installed in ` +
        `the running stack: ${absent.join(", ")}. The lock and the venv disagree about ` +
        `what the software even consists of. Reinstall with \`make install\`.`,
    ).toEqual([]);
  });

  test("nothing is installed that the lock does not account for", async ({ request }) => {
    // The other direction, and the one --require-hashes exists to enforce: a
    // transitive dependency that slipped in unrecorded is a package nobody
    // reviewed, nobody audits, and nobody can reproduce.
    //
    // The allowlist is short on purpose. `layer1-bylaw-ingest` is the project
    // itself, installed with --no-deps and so never a lock entry. pip,
    // setuptools and wheel are venv bootstrap: they are build-time machinery
    // rather than runtime imports, and Dockerfile.advisor pins setuptools on its
    // own terms (ABS-401). Anything else appearing here means a resolution
    // happened that the lock did not govern.
    const bootstrap = new Set(["layer1-bylaw-ingest", "pip", "setuptools", "wheel"]);

    const { pins } = readLock(DEV_LOCK);
    const installed = await fetchInstalled(request);

    const unaccounted = Object.keys(installed.distributions)
      .filter((name) => !pins.has(name) && !bootstrap.has(name))
      .sort();

    expect(
      unaccounted,
      `installed in the running stack but absent from requirements/dev.txt: ` +
        `${unaccounted.join(", ")}. Either something was pip-installed into this venv ` +
        `by hand, or a project install ran without --no-deps and pip re-resolved ` +
        `pyproject.toml's floors on top of the lock. Reinstall with \`make install\`; ` +
        `if the package genuinely belongs, declare it in pyproject.toml and run ` +
        `./scripts/lock-python-deps.sh.`,
    ).toEqual([]);
  });

  test("the Anthropic SDK is at the version ABS-531 pinned", async ({ request }) => {
    // Named separately from the sweep above because this is the specific pin
    // the outage bought, and a failure here should say so rather than arrive as
    // one line in a list of forty. The pin lives in pyproject.toml, but a pin in
    // a declaration only binds a resolution that reads the declaration — now
    // that every site installs from a lock, what matters is that the lock
    // carried it through and the venv honoured it.
    const { pins } = readLock(DEV_LOCK);
    const installed = await fetchInstalled(request);

    expect(pins.get("anthropic"), "requirements/dev.txt does not pin anthropic").toEqual(
      new Set(["0.100.0"]),
    );
    expect(
      installed.distributions["anthropic"],
      `the stack is running anthropic ${installed.distributions["anthropic"]}, not the ` +
        `0.100.0 the suite is actually tested against. 1.x removed the sampling ` +
        `parameters the gateway sends on every request; that is the outage, not a ` +
        `version-number nit.`,
    ).toBe("0.100.0");
  });

  test("the stack runs an interpreter the project supports", async ({ request }) => {
    // The lock is compiled against linux/CPython 3.11 (the deploy target) but
    // resolved universally so it also installs on the macOS/3.12 dev venv. That
    // only holds while the interpreter stays inside the project's declared
    // requires-python; outside it, the marker-guarded forks in the lock stop
    // covering the environment and the file quietly under-installs.
    const installed = await fetchInstalled(request);

    expect(installed.python_implementation).toBe("CPython");

    const [major, minor] = installed.python_version.split(".").map(Number);
    expect(
      major > 3 || (major === 3 && minor >= 11),
      `the stack is on Python ${installed.python_version}; pyproject.toml declares ` +
        `requires-python = ">=3.11" and the locks are compiled against 3.11.`,
    ).toBe(true);
  });
});
