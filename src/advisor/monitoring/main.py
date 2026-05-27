"""Standalone entrypoint for the monitoring service.

Usage (inside Docker / directly)::

    python -m advisor.monitoring.main

Reads all configuration from environment variables:
  MONITOR_TARGET_URL               — default http://localhost:8000/healthz
  MONITOR_PROBE_INTERVAL_SECONDS   — default 60
  MONITOR_FAILURE_THRESHOLD        — default 2
  MONITOR_SLACK_WEBHOOK_URL        — optional; enables Slack alerts
  MONITOR_ALERT_EMAIL_TO           — optional; enables email alerts (requires SMTP vars)
  MONITOR_SMTP_HOST                — SMTP server host
  MONITOR_SMTP_PORT                — SMTP server port (default 587)
  MONITOR_SMTP_USER                — SMTP login user
  MONITOR_SMTP_PASSWORD            — SMTP login password
  MONITOR_SMTP_FROM                — sender address (default monitor@agenticbylawsystems.com)
  LOG_LEVEL                        — default INFO
  LOG_FORMAT                       — "json" or "text" (default json)
"""
from __future__ import annotations

import asyncio

from advisor.logging import setup_logging
from advisor.monitoring.monitor import MonitorService


def main() -> None:
    setup_logging()
    service = MonitorService.from_env()
    asyncio.run(service.run_forever())


if __name__ == "__main__":
    main()
