"""Availability monitoring and alerting for the Halifax Bylaw Advisor.

External probe that polls /healthz every N seconds and dispatches Slack
(or email) alerts after M consecutive failures.  Designed to run as a
separate process / Docker container so it survives advisor crashes.

Public surface
--------------
``MonitorService``  – polls, tracks failures, dispatches alerts.
``SlackAlerter``    – sends Slack incoming-webhook notifications.
``FailureTracker``  – counts consecutive failures, fires on threshold.
``HealthProbe``     – one-shot HTTP health check.
``router``          – FastAPI router exposing ``/v1/monitoring/status``.
"""
from advisor.monitoring.probe import HealthProbe, ProbeResult
from advisor.monitoring.alerting import SlackAlerter
from advisor.monitoring.state import FailureTracker
from advisor.monitoring.monitor import MonitorService
from advisor.monitoring import router as monitoring_router

__all__ = [
    "HealthProbe",
    "ProbeResult",
    "SlackAlerter",
    "FailureTracker",
    "MonitorService",
    "monitoring_router",
]
