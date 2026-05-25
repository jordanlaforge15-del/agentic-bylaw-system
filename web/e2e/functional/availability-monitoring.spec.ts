// ABS-122: Availability monitoring — /metrics endpoint and SLO tracking.
//
// Verifies the /metrics endpoint returns SLO targets and compliance
// data, and that the latency middleware records requests into the
// sliding window.

import { expect, test } from "../fixtures/test-env";
import { E2E_API_URL } from "../fixtures/test-env";

test("/metrics returns SLO targets and compliance data", async () => {
  const res = await fetch(`${E2E_API_URL}/metrics`);
  expect(res.status).toBe(200);
  const body = await res.json();

  expect(body).toHaveProperty("window_seconds");
  expect(body).toHaveProperty("total_requests");
  expect(body).toHaveProperty("availability_pct");
  expect(body).toHaveProperty("chat_latency_p95_seconds");
  expect(body).toHaveProperty("slo_targets");
  expect(body).toHaveProperty("slo_met");

  expect(body.slo_targets.availability_pct).toBe(99.5);
  expect(body.slo_targets.chat_latency_p95_seconds).toBe(10.0);
  expect(typeof body.slo_met.availability).toBe("boolean");
  expect(typeof body.slo_met.chat_latency_p95).toBe("boolean");
});

test("/metrics tracks requests in sliding window", async () => {
  // Hit healthz a few times to generate traffic
  for (let i = 0; i < 3; i++) {
    await fetch(`${E2E_API_URL}/healthz`);
  }

  const res = await fetch(`${E2E_API_URL}/metrics`);
  expect(res.status).toBe(200);
  const body = await res.json();

  // The sliding window should have recorded at least our requests
  // (plus any from other tests running in this suite).
  expect(body.total_requests).toBeGreaterThanOrEqual(3);
  expect(body.availability_pct).toBeGreaterThan(0);
});

test("/metrics reports healthy SLOs when service is up", async () => {
  const res = await fetch(`${E2E_API_URL}/metrics`);
  const body = await res.json();

  // With a healthy test stack, both SLOs should be met.
  expect(body.slo_met.availability).toBe(true);
  expect(body.slo_met.chat_latency_p95).toBe(true);
});
