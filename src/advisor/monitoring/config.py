"""Environment-backed configuration for availability monitoring.

All env vars are optional. The monitoring probe and alerting channels
are disabled by default — ``MONITORING_ENABLED=true`` gates the probe
daemon, and the Slack / SMTP sections only activate when their
respective connection parameters are populated.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class MonitoringSettings(BaseSettings):
    enabled: bool = Field(default=False, alias="MONITORING_ENABLED")
    healthz_url: str = Field(
        default="http://localhost:8000/healthz",
        alias="MONITORING_HEALTHZ_URL",
    )
    probe_interval_seconds: int = Field(default=60, alias="MONITORING_PROBE_INTERVAL")
    consecutive_failures_threshold: int = Field(
        default=2, alias="MONITORING_FAILURE_THRESHOLD"
    )

    slack_webhook_url: str | None = Field(
        default=None, alias="MONITORING_SLACK_WEBHOOK_URL"
    )

    smtp_host: str | None = Field(default=None, alias="MONITORING_SMTP_HOST")
    smtp_port: int = Field(default=587, alias="MONITORING_SMTP_PORT")
    smtp_username: str | None = Field(default=None, alias="MONITORING_SMTP_USERNAME")
    smtp_password: str | None = Field(default=None, alias="MONITORING_SMTP_PASSWORD")
    alert_email_to: str | None = Field(
        default=None, alias="MONITORING_ALERT_EMAIL_TO"
    )
    alert_email_from: str | None = Field(
        default=None, alias="MONITORING_ALERT_EMAIL_FROM"
    )

    slo_availability_target: float = Field(
        default=99.5, alias="MONITORING_SLO_AVAILABILITY"
    )
    slo_chat_latency_p95_seconds: float = Field(
        default=10.0, alias="MONITORING_SLO_CHAT_LATENCY_P95"
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> MonitoringSettings:
    return MonitoringSettings()
