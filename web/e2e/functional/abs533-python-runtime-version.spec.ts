// ABS-533 — the interpreter serving the running stack must be the one the
// advisor image ships.
//
// WHAT THIS CATCHES THAT A UNIT TEST CANNOT. tests/test_python_runtime_version.py
// asserts that every *declaration* of a Python version agrees: both
// Dockerfile.advisor stages, Dockerfile.layer, the four ci.yml jobs,
// dependency-audit.yml, requires-python, ruff's target-version, and the version
// the ABS-532 locks are compiled against. That is a check on files.
//
// This is a check on a process. The e2e stack boots FastAPI from the dev venv,
// and the dev venv is created once — by whichever interpreter was on PATH the
// day someone ran dev-setup.sh — and then lives for months. Nothing about a
// venv makes it revisit that choice, so the declarations can all be moved to
// 3.13 while the suite that certifies the move keeps running on the 3.11 or 3.12
// the box happened to have. The result is a green e2e run describing an
// interpreter that does not ship: precisely ABS-531's failure, where the suite
// passed against an anthropic the image did not have, one layer down.
//
// So this asks the running process, not the repo, and compares the answer to
// Dockerfile.advisor's FROM line — the artifact that actually reaches
// production. It reuses POST /v1/_test/installed-versions, which ABS-532 added
// for the same reason: free, no key, no network, no database, no model call.
//
// Like the ABS-532 spec it deliberately goes through no UI. No user-facing
// surface renders an interpreter version, and inventing one to have something
// to click would test the invention rather than the risk.

import { readFileSync } from "node:fs";
import path from "node:path";

import { expect, test } from "@playwright/test";

const E2E_API_URL = process.env.E2E_API_URL ?? "http://127.0.0.1:8001";
const INSTALLED_VERSIONS = `${E2E_API_URL}/v1/_test/installed-versions`;

// web/e2e/functional/ -> repo root
const REPO_ROOT = path.resolve(__dirname, "..", "..", "..");
const ADVISOR_DOCKERFILE = path.join(REPO_ROOT, "Dockerfile.advisor");

type InstalledVersions = {
  python_version: string;
  python_implementation: string;
  platform: string;
  executable: string;
};

/**
 * The `X.Y.Z` the advisor image pins, read out of its base image.
 *
 * Read rather than written down on purpose: a constant here would be an eighth
 * independent declaration of the interpreter version, and this whole ticket is
 * about declarations that agree only until someone moves one of them.
 */
function deployTargetVersion(): { minor: string; patch: string } {
  const text = readFileSync(ADVISOR_DOCKERFILE, "utf8");
  const match = /^FROM python:((\d+\.\d+)\.\d+)-slim-bookworm/m.exec(text);

  expect(
    match,
    `Could not read a pinned python:X.Y.Z-slim-bookworm base image out of ` +
      `Dockerfile.advisor. Either the image is no longer exactly pinned — which ` +
      `is its own regression, an unpinned base drifts — or the FROM line was ` +
      `reshaped and this spec is now comparing against nothing.`,
  ).not.toBeNull();

  return { patch: match![1], minor: match![2] };
}

test.describe("ABS-533 Python runtime version", () => {
  test("the stack runs the minor release the advisor image ships", async ({ request }) => {
    const { minor, patch } = deployTargetVersion();

    const response = await request.post(INSTALLED_VERSIONS, { data: {} });
    expect(
      response.status(),
      `installed-versions probe failed: ${await response.text()}`,
    ).toBe(200);
    const installed = (await response.json()) as InstalledVersions;

    const runningMinor = installed.python_version.split(".").slice(0, 2).join(".");

    expect(
      runningMinor,
      `This stack is serving on Python ${installed.python_version} ` +
        `(${installed.executable}), but Dockerfile.advisor ships Python ${patch}. ` +
        `Every test in this suite is therefore green about an interpreter that ` +
        `does not reach production — the ABS-531 failure shape, one layer down. ` +
        `Rebuild the venv on the deploy target:\n` +
        `  rm -rf .venv && ./scripts/dev-setup.sh --skip-db --python python${minor}`,
    ).toBe(minor);
  });

  test("the interpreter is CPython, matching the image", async ({ request }) => {
    // Cheap, and not hypothetical: `python3` on a developer's PATH can be
    // PyPy or a conda build. The lock's wheel hashes are resolved for CPython
    // ABI tags, so a different implementation silently changes which artifacts
    // the suite exercises even when the version string matches.
    const response = await request.post(INSTALLED_VERSIONS, { data: {} });
    expect(response.status()).toBe(200);
    const installed = (await response.json()) as InstalledVersions;

    expect(
      installed.python_implementation,
      `The stack is running ${installed.python_implementation}; the advisor ` +
        `image runs CPython.`,
    ).toBe("CPython");
  });

  test("the deploy target is not a Python release in security-only maintenance", async () => {
    // The complaint that opened ABS-533 was not that the pin was wrong, it was
    // that the value was old: 3.11.10 shipped September 2024 and 3.11 receives
    // security fixes only. A pin is not evidence of currency — it is the exact
    // mechanism by which something stays two years behind while looking
    // deliberate. This is the check nothing had: it fails when the interpreter
    // this stack runs falls behind the floor below, which is the signal to open
    // the next runtime ticket rather than notice during an incident.
    const { minor } = deployTargetVersion();

    // Raise this when the stack moves forward again. 3.13 was chosen over 3.14
    // on wheel availability: paddleocr/paddlex constrain pyyaml to 6.0.2, which
    // publishes no cp314 manylinux wheel, so the unlocked [parsers] extra
    // compiles from source there.
    const MINIMUM_SUPPORTED_MINOR = 13;

    const [major, minorNumber] = minor.split(".").map(Number);

    expect(major).toBe(3);
    expect(
      minorNumber,
      `Dockerfile.advisor targets Python ${minor}, at or below the floor this ` +
        `project committed to in ABS-533. Moving forward is its own ticket with ` +
        `its own test run — see the ABS-533 commit for the wheel-availability ` +
        `procedure that picks the target.`,
    ).toBeGreaterThanOrEqual(MINIMUM_SUPPORTED_MINOR);
  });
});
