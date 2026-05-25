"""Alert dispatch to Slack webhooks and SMTP email."""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from advisor.monitoring.config import MonitoringSettings

logger = logging.getLogger(__name__)


class AlertSender:
    """Fan-out alerts to all configured channels (Slack, email)."""

    def __init__(self, settings: MonitoringSettings) -> None:
        self._settings = settings

    @property
    def has_channels(self) -> bool:
        return bool(self._settings.slack_webhook_url) or bool(
            self._settings.smtp_host and self._settings.alert_email_to
        )

    def send(self, subject: str, body: str) -> None:
        if self._settings.slack_webhook_url:
            self._send_slack(subject, body)
        if self._settings.smtp_host and self._settings.alert_email_to:
            self._send_email(subject, body)

    def _send_slack(self, subject: str, body: str) -> None:
        try:
            httpx.post(
                self._settings.slack_webhook_url,  # type: ignore[arg-type]
                json={"text": f"*{subject}*\n{body}"},
                timeout=10,
            ).raise_for_status()
        except Exception:
            logger.exception("Failed to send Slack alert")

    def _send_email(self, subject: str, body: str) -> None:
        try:
            msg = EmailMessage()
            msg["Subject"] = subject
            msg["From"] = (
                self._settings.alert_email_from or self._settings.smtp_username
            )
            msg["To"] = self._settings.alert_email_to
            msg.set_content(body)

            with smtplib.SMTP(
                self._settings.smtp_host,  # type: ignore[arg-type]
                self._settings.smtp_port,
            ) as smtp:
                smtp.starttls()
                if self._settings.smtp_username and self._settings.smtp_password:
                    smtp.login(
                        self._settings.smtp_username, self._settings.smtp_password
                    )
                smtp.send_message(msg)
        except Exception:
            logger.exception("Failed to send email alert")
