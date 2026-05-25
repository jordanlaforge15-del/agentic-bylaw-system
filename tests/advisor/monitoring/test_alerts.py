"""Unit tests for alert dispatch channels."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx

from advisor.monitoring.alerts import AlertSender
from advisor.monitoring.config import MonitoringSettings


def test_slack_alert_posts_to_webhook():
    mock_response = httpx.Response(200)
    settings = MonitoringSettings(
        MONITORING_SLACK_WEBHOOK_URL="https://hooks.slack.com/test",
    )
    sender = AlertSender(settings)

    with patch("httpx.post", return_value=mock_response) as mock_post:
        sender.send("Test Alert", "This is a test.")
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert call_kwargs[0][0] == "https://hooks.slack.com/test"
        assert "Test Alert" in str(call_kwargs[1]["json"])


def test_no_channels_configured_is_noop():
    settings = MonitoringSettings()
    sender = AlertSender(settings)
    assert not sender.has_channels
    sender.send("Test", "Body")


def test_has_channels_with_slack():
    settings = MonitoringSettings(
        MONITORING_SLACK_WEBHOOK_URL="https://hooks.slack.com/test",
    )
    sender = AlertSender(settings)
    assert sender.has_channels


def test_has_channels_with_email():
    settings = MonitoringSettings(
        MONITORING_SMTP_HOST="smtp.example.com",
        MONITORING_ALERT_EMAIL_TO="ops@example.com",
    )
    sender = AlertSender(settings)
    assert sender.has_channels


def test_slack_failure_is_logged_not_raised():
    settings = MonitoringSettings(
        MONITORING_SLACK_WEBHOOK_URL="https://hooks.slack.com/test",
    )
    sender = AlertSender(settings)

    with patch("httpx.post", side_effect=ConnectionError("fail")):
        sender.send("Alert", "body")
