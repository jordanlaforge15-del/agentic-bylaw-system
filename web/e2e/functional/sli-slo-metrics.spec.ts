// ABS-135: SLI/SLO metrics endpoint.
//
// Verifies the Prometheus /metrics endpoint exposes the defined SLIs
// (availability, latency, error rate) and SLO targets. The endpoint is
// unauthenticated so Prometheus can scrape it without credentials.

import { expect, test } from "../fixtures/test-env";
import { E2E_API_URL } from "../fixtures/test-env";

test("/metrics returns Prometheus exposition format", async () => {
  const res = await fetch(`${E2E_API_URL}/metrics`);
  expect(res.status).toBe(200);
  const ct = res.headers.get("content-type") ?? "";
  expect(ct).toContain("text/plain");
  const body = await res.text();
  expect(body).toContain("advisor_http_requests_total");
  expect(body).toContain("advisor_http_request_duration_seconds");
  expect(body).toContain("advisor_http_requests_in_progress");
  expect(body).toContain("advisor_http_5xx_total");
  expect(body).toContain("advisor_chat_request_duration_seconds");
});

test("/metrics exposes SLO target definitions", async () => {
  const res = await fetch(`${E2E_API_URL}/metrics`);
  expect(res.status).toBe(200);
  const body = await res.text();
  expect(body).toContain("advisor_slo_info");
  expect(body).toContain('availability_target="0.995"');
  expect(body).toContain('chat_latency_p95_seconds="10.0"');
  expect(body).toContain('error_rate_ceiling="0.01"');
});

test("metrics increment after hitting /healthz", async () => {
  // Hit /healthz to ensure at least one data point
  const healthRes = await fetch(`${E2E_API_URL}/healthz`);
  expect(healthRes.status).toBe(200);

  const metricsRes = await fetch(`${E2E_API_URL}/metrics`);
  expect(metricsRes.status).toBe(200);
  const body = await metricsRes.text();

  // The counter should have recorded a GET /healthz with status 200
  expect(body).toMatch(
    /advisor_http_requests_total\{[^}]*endpoint="\/healthz"[^}]*status_code="200"[^}]*\}/,
  );
  // The histogram should have recorded duration for GET /healthz
  expect(body).toMatch(
    /advisor_http_request_duration_seconds_bucket\{[^}]*endpoint="\/healthz"/,
  );
});
