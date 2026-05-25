#!/usr/bin/env python3
"""Standalone availability-monitoring daemon.

Polls the advisor's ``/healthz`` endpoint at a configurable interval
and fires Slack / email alerts after consecutive failures. Intended to
run alongside the application as a systemd service or cron job.

Configuration is entirely via environment variables — see
``advisor.monitoring.config.MonitoringSettings`` for the full list.

Quick start::

    export MONITORING_ENABLED=true
    export MONITORING_HEALTHZ_URL=https://bylawadvisor.ca/healthz
    export MONITORING_SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
    python scripts/availability-monitor.py
"""
from __future__ import annotations

import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("availability-monitor")


def main() -> None:
    from advisor.monitoring.alerts import AlertSender
    from advisor.monitoring.config import get_settings
    from advisor.monitoring.probe import HealthProbe

    settings = get_settings()
    if not settings.enabled:
        logger.error(
            "MONITORING_ENABLED is not set to true. "
            "Set MONITORING_ENABLED=true to start the probe."
        )
        sys.exit(1)

    sender = AlertSender(settings)
    if not sender.has_channels:
        logger.warning(
            "No alert channels configured. The probe will run but "
            "alerts will only appear in logs."
        )

    probe = HealthProbe(settings, sender)
    try:
        probe.run_loop()
    except KeyboardInterrupt:
        logger.info("Shutting down.")


if __name__ == "__main__":
    main()
