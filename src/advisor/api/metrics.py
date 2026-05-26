"""SLI/SLO definitions and Prometheus metric instruments.

Service Level Indicators (SLIs) measured:
  * **Availability** — fraction of non-5xx responses on API endpoints.
  * **Request latency** — response duration histogram (seconds).
  * **Error rate** — fraction of 5xx responses on API endpoints.

Service Level Objectives (SLOs):
  * Availability ≥ 99.5 %
  * Chat latency p95 < 10 s
  * Error rate < 1 % of total requests

The ``/metrics`` endpoint exposes all counters/histograms in the
Prometheus exposition format for scraping.
"""
from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram, Info

# ---------------------------------------------------------------------------
# SLO target constants (referenced by /healthz SLI compliance report)
# ---------------------------------------------------------------------------
SLO_AVAILABILITY_TARGET = 0.995
SLO_CHAT_LATENCY_P95_SECONDS = 10.0
SLO_ERROR_RATE_TARGET = 0.01

# ---------------------------------------------------------------------------
# Prometheus instruments
# ---------------------------------------------------------------------------

REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests received",
    ["method", "endpoint", "status"],
)

REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "endpoint"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0),
)

REQUEST_ERRORS = Counter(
    "http_errors_total",
    "Total HTTP 5xx responses",
    ["method", "endpoint", "status"],
)

REQUEST_IN_PROGRESS = Gauge(
    "http_requests_in_progress",
    "Number of HTTP requests currently being processed",
    ["method", "endpoint"],
)

APP_INFO = Info(
    "advisor",
    "Application metadata",
)

# ---------------------------------------------------------------------------
# SLI compliance helpers
# ---------------------------------------------------------------------------

def compute_sli_compliance() -> dict:
    """Derive current SLI values from in-process Prometheus counters.

    Returns a dict suitable for embedding in the ``/healthz`` response
    so operators can eyeball compliance without a Grafana dashboard.
    """
    total = 0.0
    errors = 0.0
    for metric in REQUEST_COUNT.collect():
        for sample in metric.samples:
            if sample.name == "http_requests_total":
                total += sample.value
                status = sample.labels.get("status", "")
                if status.startswith("5"):
                    errors += sample.value

    availability = 1.0 - (errors / total) if total > 0 else 1.0
    error_rate = (errors / total) if total > 0 else 0.0

    chat_p95 = _estimate_histogram_quantile(0.95, "http_request_duration_seconds", "/v1/chat")

    return {
        "window": "since_startup",
        "total_requests": int(total),
        "availability": {
            "current": round(availability, 6),
            "target": SLO_AVAILABILITY_TARGET,
            "meeting_slo": availability >= SLO_AVAILABILITY_TARGET,
        },
        "chat_latency_p95_seconds": {
            "current": round(chat_p95, 3) if chat_p95 is not None else None,
            "target": SLO_CHAT_LATENCY_P95_SECONDS,
            "meeting_slo": (chat_p95 is not None and chat_p95 <= SLO_CHAT_LATENCY_P95_SECONDS),
        },
        "error_rate": {
            "current": round(error_rate, 6),
            "target": SLO_ERROR_RATE_TARGET,
            "meeting_slo": error_rate <= SLO_ERROR_RATE_TARGET,
        },
    }


def _estimate_histogram_quantile(
    quantile: float, metric_name: str, endpoint: str
) -> float | None:
    """Estimate a quantile from Prometheus histogram buckets.

    Uses linear interpolation within the bucket that contains the target
    quantile — the same approach Prometheus server uses for
    ``histogram_quantile()``. Returns ``None`` when no observations
    exist for the given endpoint.
    """
    buckets: list[tuple[float, float]] = []
    total_count = 0.0

    for metric in REQUEST_LATENCY.collect():
        for sample in metric.samples:
            if sample.name == f"{metric_name}_bucket":
                if sample.labels.get("endpoint") == endpoint:
                    le = float(sample.labels.get("le", "inf"))
                    buckets.append((le, sample.value))
            elif sample.name == f"{metric_name}_count":
                if sample.labels.get("endpoint") == endpoint:
                    total_count = sample.value

    if total_count == 0:
        return None

    buckets.sort(key=lambda b: b[0])
    target = quantile * total_count
    prev_count = 0.0
    prev_bound = 0.0

    for bound, count in buckets:
        if count >= target:
            if bound == float("inf"):
                return prev_bound
            if count == prev_count:
                return bound
            fraction = (target - prev_count) / (count - prev_count)
            return prev_bound + fraction * (bound - prev_bound)
        prev_count = count
        prev_bound = bound

    return prev_bound
