"""Alert dispatchers — Slack webhook and SMTP email."""
from __future__ import annotations

import json
import logging
import smtplib
import urllib.request
import urllib.error
from dataclasses import dataclass
from email.message import EmailMessage

from advisor.monitoring.probe import ProbeResult

logger = logging.getLogger(__name__)


@dataclass
class SlackAlerter:
    """Posts a message to a Slack incoming-webhook URL."""

    webhook_url: str

    def send(self, result: ProbeResult, consecutive_failures: int) -> None:
        body = _build_slack_payload(result, consecutive_failures)
        data = json.dumps(body).encode()
        req = urllib.request.Request(
            self.webhook_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                logger.info(
                    "slack alert sent",
                    extra={"status": resp.status, "consecutive_failures": consecutive_failures},
                )
        except urllib.error.HTTPError as exc:
            logger.error(
                "slack alert failed",
                extra={"http_status": exc.code, "error": str(exc)},
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("slack alert failed", extra={"error": str(exc)})


def _build_slack_payload(result: ProbeResult, consecutive_failures: int) -> dict:
    ts = result.timestamp.strftime("%Y-%m-%d %H:%M:%S UTC")
    error_detail = f"\n• Error: `{result.error}`" if result.error else ""
    http_detail = f"\n• HTTP status: `{result.http_status}`" if result.http_status else ""
    return {
        "text": (
            f":red_circle: *Halifax Bylaw Advisor is DOWN*\n"
            f"• {consecutive_failures} consecutive probe failures\n"
            f"• Target: `{result.url}`"
            f"{http_detail}"
            f"{error_detail}\n"
            f"• Last checked: {ts}"
        )
    }


@dataclass
class EmailAlerter:
    """Sends an alert email via SMTP."""

    smtp_host: str
    smtp_port: int
    username: str
    password: str
    sender: str
    recipient: str

    def send(self, result: ProbeResult, consecutive_failures: int) -> None:
        msg = EmailMessage()
        msg["Subject"] = f"[ALERT] Halifax Bylaw Advisor DOWN — {consecutive_failures} consecutive failures"
        msg["From"] = self.sender
        msg["To"] = self.recipient
        ts = result.timestamp.strftime("%Y-%m-%d %H:%M:%S UTC")
        error_line = f"Error: {result.error}\n" if result.error else ""
        http_line = f"HTTP status: {result.http_status}\n" if result.http_status else ""
        msg.set_content(
            f"The Halifax Bylaw Advisor health probe has failed {consecutive_failures} times in a row.\n\n"
            f"Target: {result.url}\n"
            f"{http_line}"
            f"{error_line}"
            f"Last checked: {ts}\n\n"
            f"Check https://api.agenticbylawsystems.com/healthz for current status."
        )
        try:
            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                server.starttls()
                server.login(self.username, self.password)
                server.send_message(msg)
            logger.info(
                "email alert sent",
                extra={"recipient": self.recipient, "consecutive_failures": consecutive_failures},
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("email alert failed", extra={"error": str(exc)})


def build_alerters_from_env() -> list:
    """Read env vars and return configured alerter instances (may be empty)."""
    import os
    alerters: list = []

    slack_url = os.environ.get("MONITOR_SLACK_WEBHOOK_URL")
    if slack_url:
        alerters.append(SlackAlerter(webhook_url=slack_url))

    smtp_host = os.environ.get("MONITOR_SMTP_HOST")
    recipient = os.environ.get("MONITOR_ALERT_EMAIL_TO")
    if smtp_host and recipient:
        alerters.append(
            EmailAlerter(
                smtp_host=smtp_host,
                smtp_port=int(os.environ.get("MONITOR_SMTP_PORT", "587")),
                username=os.environ.get("MONITOR_SMTP_USER", ""),
                password=os.environ.get("MONITOR_SMTP_PASSWORD", ""),
                sender=os.environ.get("MONITOR_SMTP_FROM", "monitor@agenticbylawsystems.com"),
                recipient=recipient,
            )
        )
    return alerters
