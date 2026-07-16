"""Unit tests for advisor.monitoring.probe."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from unittest.mock import patch

import pytest

from advisor.monitoring.probe import HealthProbe, ProbeResult


# ---------------------------------------------------------------------------
# Minimal in-process HTTP stub
# ---------------------------------------------------------------------------

class _StubHandler(BaseHTTPRequestHandler):
    _status: int = 200
    _body: bytes = b'{"status":"ok","sli":null}'

    def do_GET(self):  # noqa: N802
        self.send_response(self._status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(self._body)

    def log_message(self, *_):
        pass  # suppress test output


def _make_server(status: int, body: bytes = b'{"status":"ok"}') -> HTTPServer:
    _StubHandler._status = status
    _StubHandler._body = body
    server = HTTPServer(("127.0.0.1", 0), _StubHandler)
    t = Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestHealthProbe:
    def test_healthy_on_200(self):
        server = _make_server(200)
        try:
            port = server.server_address[1]
            probe = HealthProbe(url=f"http://127.0.0.1:{port}/healthz")
            result = probe.check()
            assert result.healthy is True
            assert result.http_status == 200
            assert result.error is None
        finally:
            server.shutdown()

    def test_unhealthy_on_503(self):
        server = _make_server(503, b'{"status":"degraded"}')
        try:
            port = server.server_address[1]
            probe = HealthProbe(url=f"http://127.0.0.1:{port}/healthz")
            result = probe.check()
            assert result.healthy is False
            assert result.http_status == 503
        finally:
            server.shutdown()

    def test_unhealthy_on_connection_refused(self):
        probe = HealthProbe(url="http://127.0.0.1:1/healthz", timeout_seconds=1.0)
        result = probe.check()
        assert result.healthy is False
        assert result.http_status is None
        assert result.error is not None

    def test_status_label_healthy(self):
        server = _make_server(200)
        try:
            port = server.server_address[1]
            result = HealthProbe(url=f"http://127.0.0.1:{port}/healthz").check()
            assert result.status_label == "healthy"
        finally:
            server.shutdown()

    def test_status_label_unhealthy(self):
        probe = HealthProbe(url="http://127.0.0.1:1/healthz", timeout_seconds=1.0)
        result = probe.check()
        assert result.status_label == "unhealthy"

    def test_response_time_recorded(self):
        server = _make_server(200)
        try:
            port = server.server_address[1]
            result = HealthProbe(url=f"http://127.0.0.1:{port}/healthz").check()
            assert result.response_time_ms >= 0.0
        finally:
            server.shutdown()

    def test_sli_parsed_from_body(self):
        body = b'{"status":"ok","sli":{"availability":{"current":0.999,"target":0.995,"meeting_slo":true}}}'
        server = _make_server(200, body)
        try:
            port = server.server_address[1]
            result = HealthProbe(url=f"http://127.0.0.1:{port}/healthz").check()
            assert result.sli is not None
            assert result.sli["availability"]["target"] == 0.995
        finally:
            server.shutdown()
