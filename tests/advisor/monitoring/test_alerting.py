"""Unit tests for advisor.monitoring.alerting."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

import pytest

from advisor.monitoring.alerting import SlackAlerter, _build_slack_payload, build_alerters_from_env
from advisor.monitoring.probe import ProbeResult


def _result(healthy: bool = False) -> ProbeResult:
    return ProbeResult(
        timestamp=datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc),
        url="http://advisor:8000/healthz",
        healthy=healthy,
        http_status=None if not healthy else 200,
        response_time_ms=5.0,
        error="Connection refused" if not healthy else None,
    )


class _SlackStubHandler(BaseHTTPRequestHandler):
    received: list[dict] = []

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        self.__class__.received.append(body)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *_):
        pass


def _make_slack_server() -> HTTPServer:
    _SlackStubHandler.received = []
    server = HTTPServer(("127.0.0.1", 0), _SlackStubHandler)
    Thread(target=server.serve_forever, daemon=True).start()
    return server


class TestBuildSlackPayload:
    def test_contains_failure_count(self):
        payload = _build_slack_payload(_result(), consecutive_failures=3)
        assert "3" in payload["text"]

    def test_contains_url(self):
        payload = _build_slack_payload(_result(), consecutive_failures=2)
        assert "http://advisor:8000/healthz" in payload["text"]

    def test_contains_error_detail(self):
        payload = _build_slack_payload(_result(), consecutive_failures=2)
        assert "Connection refused" in payload["text"]

    def test_has_text_key(self):
        payload = _build_slack_payload(_result(), consecutive_failures=1)
        assert "text" in payload


class TestSlackAlerter:
    def test_posts_to_webhook(self):
        server = _make_slack_server()
        try:
            port = server.server_address[1]
            alerter = SlackAlerter(webhook_url=f"http://127.0.0.1:{port}/")
            alerter.send(_result(), consecutive_failures=2)
            assert len(_SlackStubHandler.received) == 1
            assert "text" in _SlackStubHandler.received[0]
        finally:
            server.shutdown()

    def test_does_not_raise_on_failed_webhook(self):
        alerter = SlackAlerter(webhook_url="http://127.0.0.1:1/nope")
        alerter.send(_result(), consecutive_failures=2)  # should not raise


class TestBuildAlertersFromEnv:
    def test_no_env_returns_empty(self, monkeypatch):
        monkeypatch.delenv("MONITOR_SLACK_WEBHOOK_URL", raising=False)
        monkeypatch.delenv("MONITOR_SMTP_HOST", raising=False)
        monkeypatch.delenv("MONITOR_ALERT_EMAIL_TO", raising=False)
        alerters = build_alerters_from_env()
        assert alerters == []

    def test_slack_env_returns_slack_alerter(self, monkeypatch):
        monkeypatch.setenv("MONITOR_SLACK_WEBHOOK_URL", "https://hooks.slack.com/test")
        monkeypatch.delenv("MONITOR_SMTP_HOST", raising=False)
        alerters = build_alerters_from_env()
        assert len(alerters) == 1
        assert isinstance(alerters[0], SlackAlerter)

    def test_smtp_env_requires_both_host_and_recipient(self, monkeypatch):
        monkeypatch.delenv("MONITOR_SLACK_WEBHOOK_URL", raising=False)
        monkeypatch.setenv("MONITOR_SMTP_HOST", "smtp.example.com")
        monkeypatch.delenv("MONITOR_ALERT_EMAIL_TO", raising=False)
        alerters = build_alerters_from_env()
        assert alerters == []
