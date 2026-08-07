// ABS-401: Bump the advisor image's setuptools pin to >=83 to close
// PYSEC-2026-3447 and drop the pip-audit --ignore-vuln exception.
//
// setuptools 82+ removes the deprecated `pkg_resources` module. The pin was
// bumped only after auditing the [advisor] dependency tree for runtime
// `pkg_resources` importers (the sole hit, sentry-sdk, imports it inside a
// guarded Python <3.8 fallback branch that never runs on the 3.11 runtime).
// The failure mode this ticket must NOT introduce is a dependency that
// imports `pkg_resources` at advisor startup: under setuptools>=83 that
// import raises ModuleNotFoundError, `advisor.api.main` fails to load, and
// build_app() never completes — so the FastAPI server would never bind.
//
// Same reasoning as the ABS-85 import smoke (09-advisor-healthz.spec.ts):
// /healthz can only return 200 with status=ok if `advisor.api.main` was
// imported and initialized cleanly. This functional spec pins that runtime
// guarantee to the ABS-401 change so a regression that reintroduces a
// pkg_resources-importing dependency surfaces here.

import { E2E_API_URL } from "../fixtures/test-env";
import { expect, test } from "../fixtures/test-env";

test.describe("ABS-401 advisor imports cleanly under setuptools>=83", () => {
  test("/healthz ok — advisor.api.main imported without pkg_resources at startup", async () => {
    const res = await fetch(`${E2E_API_URL}/healthz`);
    expect(res.status).toBe(200);
    const body = await res.json();
    // A 200 with status=ok proves the full app-construction import path
    // (advisor.api.main → build_app) completed — the exact path that a
    // pkg_resources-importing dependency would break under setuptools>=83.
    expect(body.status).toBe("ok");
    expect(body.checks.database).toBe("ok");
  });
});
