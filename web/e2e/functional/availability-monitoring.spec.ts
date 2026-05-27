// ABS-122: Availability monitoring with alerting.
//
// Verifies that the /v1/monitoring/status endpoint is present, returns
// a well-structured probe result + monitoring config, and that the SLO
// targets exposed through it match the documented values.
//
// The test does NOT configure real Slack/email credentials — it only
// confirms the endpoint exists and the data shape is correct.  The
// actual alert-dispatch path is covered by Python unit tests.

import { expect, test } from "../fixtures/test-env";
import { E2E_API_URL } from "../fixtures/test-env";

test.describe("availability monitoring", () => {
  test("/v1/monitoring/status returns probe result and config", async () => {
    const res = await fetch(`${E2E_API_URL}/v1/monitoring/status`);

    // The e2e stack is healthy, so the probe should succeed.
    expect(res.status).toBe(200);

    const body = await res.json();

    // Probe block.
    expect(body).toHaveProperty("probe");
    expect(body.probe).toHaveProperty("url");
    expect(body.probe).toHaveProperty("status");
    expect(["healthy", "unhealthy"]).toContain(body.probe.status);
    expect(typeof body.probe.response_time_ms).toBe("number");
    expect(body.probe).toHaveProperty("timestamp");
    expect(body.probe).toHaveProperty("http_status");

    // Config block.
    expect(body).toHaveProperty("config");
    expect(typeof body.config.probe_interval_seconds).toBe("number");
    expect(typeof body.config.failure_threshold).toBe("number");
    expect(body.config).toHaveProperty("alerting");
    expect(typeof body.config.alerting.slack_configured).toBe("boolean");
    expect(typeof body.config.alerting.email_configured).toBe("boolean");
  });

  test("/v1/monitoring/status probe reports healthy in e2e stack", async () => {
    const res = await fetch(`${E2E_API_URL}/v1/monitoring/status`);
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.probe.status).toBe("healthy");
    expect(body.probe.http_status).toBe(200);
  });

  test("/v1/monitoring/status probe includes sli data", async () => {
    const res = await fetch(`${E2E_API_URL}/v1/monitoring/status`);
    expect(res.status).toBe(200);
    const body = await res.json();

    // SLI data is forwarded from /healthz when the probe succeeds.
    expect(body.probe.sli).not.toBeNull();
    expect(body.probe.sli).toHaveProperty("availability");
    expect(body.probe.sli.availability).toHaveProperty("target", 0.995);
    expect(body.probe.sli).toHaveProperty("chat_latency_p95_seconds");
    expect(body.probe.sli.chat_latency_p95_seconds).toHaveProperty("target", 10.0);
  });

  test("monitoring failure threshold defaults to 2", async () => {
    const res = await fetch(`${E2E_API_URL}/v1/monitoring/status`);
    expect(res.status).toBe(200);
    const body = await res.json();
    // Default threshold is 2 consecutive failures before alerting.
    expect(body.config.failure_threshold).toBe(2);
  });

  test("monitoring probe interval defaults to 60 seconds", async () => {
    const res = await fetch(`${E2E_API_URL}/v1/monitoring/status`);
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.config.probe_interval_seconds).toBe(60);
  });
});
