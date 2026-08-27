// ABS-420: the ops corpus-coherence endpoint must audit the real declaration
// set, and must never report a green it did not earn.
//
// Production ran for months on {"status":"ok","checked_roles":0}. The audit
// resolves its overlay declarations from the layer1 dataset configs; in the
// deployed wheel that path pointed at a directory no install creates, and
// Path.glob() on a missing directory returns nothing — so the tripwire built
// for a geo dataset falling out of retrieval scope (ABS-350) checked zero
// roles and passed, every time, for the entire life of the endpoint.
//
// The wheel half of that fix is pinned by tests/test_package_data.py. This
// spec pins the half a wheel test cannot see: that the running advisor
// process, over HTTP, on the endpoint an operator actually reads, reports an
// audit of every declared role — and that the number it reports is the number
// of configs on disk, not a constant either side could drift from.

import * as fs from "node:fs";
import * as path from "node:path";

import { E2E_API_URL, expect, test } from "../fixtures/test-env";

const DATASET_CONFIG_DIR = path.resolve(__dirname, "..", "..", "..", "src", "layer1", "datasets");

type CoherenceBody = {
  status: string;
  detail?: string;
  checked_roles: number;
  bylaws_checked: number;
  missing: { role: string; dataset_name: string; reason: string; detail: string }[];
};

/**
 * The overlay-role configs the audit loads: every dataset YAML that binds to a
 * bylaw fragment (`links_to`) and does not carry a `role` of its own — the
 * same filter as coherence_audit._overlay_configs. Read off disk rather than
 * hardcoded so adding a config updates the expectation instead of breaking it.
 */
function declaredOverlayConfigs(): string[] {
  const names: string[] = [];
  for (const file of fs.readdirSync(DATASET_CONFIG_DIR).filter((f) => f.endsWith(".yaml"))) {
    const body = fs.readFileSync(path.join(DATASET_CONFIG_DIR, file), "utf8");
    if (!/^links_to:/m.test(body) || /^role:/m.test(body)) continue;
    // The declaration is keyed by the config's `name:`, which is not always
    // the filename — halifax_zoning.yaml declares halifax_zoning_boundaries.
    const declared = /^name:\s*(\S+)/m.exec(body);
    expect(declared, `dataset config ${file} has no top-level name:`).not.toBeNull();
    names.push(declared![1]);
  }
  return names;
}

async function readCoherence(
  request: import("@playwright/test").APIRequestContext,
): Promise<{ status: number; body: CoherenceBody }> {
  const response = await request.get(`${E2E_API_URL}/v1/monitoring/corpus-coherence`);
  // 200 (coherent) or 503 (something is red) are both real verdicts. The e2e
  // database holds its own fixtures rather than the halifax corpus, so which
  // one comes back is not this spec's business — what it audited is.
  expect(
    [200, 503],
    `unexpected status from corpus-coherence: ${response.status()} ${await response.text()}`,
  ).toContain(response.status());
  return { status: response.status(), body: (await response.json()) as CoherenceBody };
}

test("the ops endpoint audits every overlay role declared on disk", async ({ request }) => {
  const configs = declaredOverlayConfigs();
  expect(
    configs.length,
    `no overlay configs found under ${DATASET_CONFIG_DIR}`,
  ).toBeGreaterThan(0);

  const { body } = await readCoherence(request);

  // The assertion production would have failed since ABS-356 shipped.
  expect(
    body.checked_roles,
    `endpoint checked ${body.checked_roles} role(s) but ${configs.length} are declared on disk — ` +
      "the process cannot read its layer1 dataset configs",
  ).toBe(configs.length);
});

test("a zero-declaration audit is never reported as ok", async ({ request }) => {
  const { status, body } = await readCoherence(request);

  // Whatever the verdict, it must not be the vacuous shape: green with
  // nothing checked. If checked_roles is ever 0, the endpoint owes a 503 and
  // a detail line saying so, not "ok".
  if (body.checked_roles === 0) {
    expect(status).toBe(503);
    expect(body.status).toBe("error");
    expect(body.detail).toContain("no overlay declarations loaded");
  } else {
    expect(body.status).not.toBe("error");
  }
});

test("every role it names belongs to a config on disk", async ({ request }) => {
  const configs = new Set(declaredOverlayConfigs());
  const { body } = await readCoherence(request);

  // Proves the declarations came from the real configs rather than from a
  // fixture or a stale in-process default: each missing role is reported
  // under the dataset_name of a config that exists in this checkout.
  for (const missing of body.missing) {
    expect(
      configs.has(missing.dataset_name),
      `endpoint reported role ${missing.role} for unknown dataset ${missing.dataset_name}`,
    ).toBe(true);
    expect(["unlinked", "orphaned", "evicted"]).toContain(missing.reason);
  }
});
