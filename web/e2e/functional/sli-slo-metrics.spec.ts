// ABS-135: SLI/SLO metrics instrumentation.
//
// Verifies that the Prometheus /metrics endpoint emits the expected
// metric families and that /v1/slo returns the SLI compliance report.
// Also checks that /healthz now includes the sli field.

import { expect, test } from "../fixtures/test-env";
import { E2E_API_URL } from "../fixtures/test-env";

test("/metrics returns Prometheus exposition format", async () => {
  // Hit a measured endpoint first so counters are non-zero.
  await fetch(`${E2E_API_URL}/v1/slo`);

  const res = await fetch(`${E2E_API_URL}/metrics`);
  expect(res.status).toBe(200);

  const contentType = res.headers.get("content-type") ?? "";
  expect(contentType).toContain("text/plain");

  const body = await res.text();

  // Core SLI metric families must be present.
  expect(body).toContain("http_requests_total");
  expect(body).toContain("http_request_duration_seconds");
  expect(body).toContain("http_errors_total");
  expect(body).toContain("http_requests_in_progress");
  expect(body).toContain("advisor_info");
});

test("/v1/slo returns SLI compliance report", async () => {
  const res = await fetch(`${E2E_API_URL}/v1/slo`);
  expect(res.status).toBe(200);

  const body = await res.json();

  // Structure checks.
  expect(body).toHaveProperty("window", "since_startup");
  expect(typeof body.total_requests).toBe("number");

  // Availability SLO.
  expect(body.availability).toHaveProperty("current");
  expect(body.availability).toHaveProperty("target", 0.995);
  expect(typeof body.availability.meeting_slo).toBe("boolean");

  // Chat latency SLO.
  expect(body.chat_latency_p95_seconds).toHaveProperty("target", 10.0);
  expect(typeof body.chat_latency_p95_seconds.meeting_slo).toBe("boolean");

  // Error rate SLO.
  expect(body.error_rate).toHaveProperty("target", 0.01);
  expect(typeof body.error_rate.meeting_slo).toBe("boolean");
});

test("/healthz includes sli compliance data", async () => {
  const res = await fetch(`${E2E_API_URL}/healthz`);
  expect(res.status).toBe(200);

  const body = await res.json();
  expect(body).toHaveProperty("sli");
  expect(body.sli).toHaveProperty("availability");
  expect(body.sli).toHaveProperty("error_rate");
  expect(body.sli).toHaveProperty("chat_latency_p95_seconds");
});

test("metrics track requests after API calls", async () => {
  // Record baseline.
  const before = await fetch(`${E2E_API_URL}/v1/slo`);
  const beforeBody = await before.json();
  const baselineRequests = beforeBody.total_requests;

  // Make a few API calls that should be tracked.
  await fetch(`${E2E_API_URL}/v1/slo`);
  await fetch(`${E2E_API_URL}/v1/slo`);

  const after = await fetch(`${E2E_API_URL}/v1/slo`);
  const afterBody = await after.json();

  // Should have recorded at least 3 new requests (the 2 /v1/slo
  // calls above + the final /v1/slo call itself). The /metrics
  // endpoint is excluded from tracking.
  expect(afterBody.total_requests).toBeGreaterThanOrEqual(baselineRequests + 3);
});
