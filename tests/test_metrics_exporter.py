"""
Tests for app/metrics_exporter.py:

  - _resource_counts()
  - _ts_ns()
  - _safe_tag()
  - _sanitize_graphite_path()
  - export_execution_metrics()
  - _send_influxdb()
  - _send_prometheus()
  - _send_graphite()
"""
import base64
import socket
from unittest.mock import MagicMock, patch

from app.metrics_exporter import (
    _resource_counts,
    _safe_tag,
    _sanitize_graphite_path,
    _send_graphite,
    _send_influxdb,
    _send_prometheus,
    _ts_ns,
    export_execution_metrics,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(**kwargs):
    """Build a lightweight mock Config with sensible defaults."""
    cfg = MagicMock()
    cfg.metrics_enabled = kwargs.get("metrics_enabled", True)
    cfg.metrics_backend = kwargs.get("metrics_backend", "influxdb")
    cfg.metrics_prefix = kwargs.get("metrics_prefix", "tgm")
    cfg.metrics_influxdb_url = kwargs.get("metrics_influxdb_url", "https://influx.example.com")
    cfg.metrics_influxdb_token = kwargs.get("metrics_influxdb_token", "tok123")
    cfg.metrics_influxdb_org = kwargs.get("metrics_influxdb_org", "myorg")
    cfg.metrics_influxdb_bucket = kwargs.get("metrics_influxdb_bucket", "tgm")
    cfg.metrics_influxdb_verify_ssl = kwargs.get("metrics_influxdb_verify_ssl", True)
    cfg.metrics_prometheus_url = kwargs.get("metrics_prometheus_url", "https://prom.example.com")
    cfg.metrics_prometheus_job = kwargs.get("metrics_prometheus_job", "tgm")
    cfg.metrics_prometheus_username = kwargs.get("metrics_prometheus_username", "")
    cfg.metrics_prometheus_password = kwargs.get("metrics_prometheus_password", "")
    cfg.metrics_prometheus_verify_ssl = kwargs.get("metrics_prometheus_verify_ssl", True)
    cfg.metrics_graphite_host = kwargs.get("metrics_graphite_host", "graphite.example.com")
    cfg.metrics_graphite_port = kwargs.get("metrics_graphite_port", 2003)
    cfg.metrics_graphite_protocol = kwargs.get("metrics_graphite_protocol", "tcp")
    return cfg


_BASE_META = {
    "workspace_id": "ws1",
    "command": "plan",
    "status": "completed",
    "duration_seconds": 30,
    "resource_counts": {
        "create": 2, "update": 1, "delete": 1, "replace": 1, "no-op": 5,
    },
    "timestamp": "2025-01-15T12:00:00",
}


def _fake_urlopen_factory():
    """
    Return a (captured_requests, fake_urlopen) pair.
    fake_urlopen mimics urllib.request.urlopen as a context manager.
    """
    captured = []

    def _fake(req, context=None, timeout=None):
        captured.append(req)
        m = MagicMock()
        m.__enter__ = lambda s: s
        m.__exit__ = MagicMock(return_value=False)
        m.read.return_value = b""
        return m

    return captured, _fake


def _mock_ws_backend(metrics_enabled=True):
    """Patch app.storage.get_backend to return workspace config with given flag."""
    b = MagicMock()
    b.get_workspace_config.return_value = {"metrics_enabled": metrics_enabled}
    return b


# ---------------------------------------------------------------------------
# TestResourceCounts
# ---------------------------------------------------------------------------

class TestResourceCounts:
    def test_zeros_when_no_resource_counts(self):
        result = _resource_counts({})
        assert result == {"add": 0, "change": 0, "destroy": 0, "no_op": 0}

    def test_correct_field_mapping(self):
        meta = {"resource_counts": {
            "create": 3, "delete": 2, "no-op": 4, "update": 0, "replace": 0,
        }}
        rc = _resource_counts(meta)
        assert rc["add"] == 3
        assert rc["destroy"] == 2
        assert rc["no_op"] == 4
        assert rc["change"] == 0

    def test_update_and_replace_are_summed_into_change(self):
        meta = {"resource_counts": {
            "create": 0, "delete": 0, "no-op": 0, "update": 2, "replace": 3,
        }}
        rc = _resource_counts(meta)
        assert rc["change"] == 5


# ---------------------------------------------------------------------------
# TestTsNs
# ---------------------------------------------------------------------------

class TestTsNs:
    def test_parses_valid_iso_timestamp(self):
        meta = {"timestamp": "2025-01-15T12:00:00"}
        ns = _ts_ns(meta)
        assert isinstance(ns, int)
        # Sanity range: after 2024, before 2033
        assert 1_700_000_000_000_000_000 < ns < 2_000_000_000_000_000_000

    def test_fallback_for_invalid_timestamp(self):
        ns = _ts_ns({"timestamp": "not-a-date"})
        assert isinstance(ns, int)
        assert ns > 0

    def test_fallback_for_missing_timestamp(self):
        ns = _ts_ns({})
        assert isinstance(ns, int)
        assert ns > 0


# ---------------------------------------------------------------------------
# TestSafeTag
# ---------------------------------------------------------------------------

class TestSafeTag:
    def test_escapes_spaces(self):
        assert _safe_tag("my workspace") == r"my\ workspace"

    def test_escapes_commas(self):
        result = _safe_tag("a,b")
        assert r"\," in result

    def test_escapes_equals(self):
        result = _safe_tag("k=v")
        assert r"\=" in result


# ---------------------------------------------------------------------------
# TestSanitizeGraphitePath
# ---------------------------------------------------------------------------

class TestSanitizeGraphitePath:
    def test_replaces_dots_with_underscores(self):
        assert _sanitize_graphite_path("a.b.c") == "a_b_c"

    def test_replaces_spaces_and_slashes(self):
        assert _sanitize_graphite_path("a b/c") == "a_b_c"

    def test_empty_string_returns_unknown(self):
        assert _sanitize_graphite_path("") == "unknown"


# ---------------------------------------------------------------------------
# TestExportExecutionMetrics
# ---------------------------------------------------------------------------

class TestExportExecutionMetrics:
    def test_skips_when_metrics_disabled(self):
        cfg = _make_config(metrics_enabled=False)
        with patch("app.metrics_exporter._send_influxdb") as mock_send:
            export_execution_metrics(_BASE_META, cfg)
        mock_send.assert_not_called()

    def test_skips_when_backend_is_empty(self):
        cfg = _make_config(metrics_enabled=True, metrics_backend="")
        with patch("app.metrics_exporter._send_influxdb") as mock_send:
            export_execution_metrics(_BASE_META, cfg)
        mock_send.assert_not_called()

    def test_skips_when_per_workspace_opt_out(self):
        cfg = _make_config(metrics_backend="influxdb")
        b = _mock_ws_backend(metrics_enabled=False)
        with patch("app.storage.get_backend", return_value=b), \
             patch("app.metrics_exporter._send_influxdb") as mock_send:
            export_execution_metrics(_BASE_META, cfg)
        mock_send.assert_not_called()

    def test_routes_to_influxdb(self):
        cfg = _make_config(metrics_backend="influxdb")
        b = _mock_ws_backend()
        with patch("app.storage.get_backend", return_value=b), \
             patch("app.metrics_exporter._send_influxdb") as mock_influx:
            export_execution_metrics(_BASE_META, cfg, "my-ws")
        mock_influx.assert_called_once_with(_BASE_META, cfg, "my-ws")

    def test_routes_to_prometheus(self):
        cfg = _make_config(metrics_backend="prometheus")
        b = _mock_ws_backend()
        with patch("app.storage.get_backend", return_value=b), \
             patch("app.metrics_exporter._send_prometheus") as mock_prom:
            export_execution_metrics(_BASE_META, cfg, "ws")
        mock_prom.assert_called_once_with(_BASE_META, cfg, "ws")

    def test_routes_to_graphite(self):
        cfg = _make_config(metrics_backend="graphite")
        b = _mock_ws_backend()
        with patch("app.storage.get_backend", return_value=b), \
             patch("app.metrics_exporter._send_graphite") as mock_graph:
            export_execution_metrics(_BASE_META, cfg)
        mock_graph.assert_called_once()

    def test_swallows_exceptions_from_backend(self):
        cfg = _make_config(metrics_backend="influxdb")
        b = _mock_ws_backend()
        with patch("app.storage.get_backend", return_value=b), \
             patch("app.metrics_exporter._send_influxdb", side_effect=RuntimeError("boom")):
            # Should not raise
            export_execution_metrics(_BASE_META, cfg)


# ---------------------------------------------------------------------------
# TestSendInfluxdb
# ---------------------------------------------------------------------------

class TestSendInfluxdb:
    def test_skips_when_url_is_empty(self):
        cfg = _make_config(metrics_influxdb_url="", metrics_influxdb_token="tok")
        with patch("app.metrics_exporter.urlopen") as mock_u:
            _send_influxdb(_BASE_META, cfg, "ws")
        mock_u.assert_not_called()

    def test_skips_when_token_is_empty(self):
        cfg = _make_config(
            metrics_influxdb_url="https://influx.example.com", metrics_influxdb_token="",
        )
        with patch("app.metrics_exporter.urlopen") as mock_u:
            _send_influxdb(_BASE_META, cfg, "ws")
        mock_u.assert_not_called()

    def test_correct_write_url(self):
        cfg = _make_config()
        captured, fake = _fake_urlopen_factory()
        with patch("app.metrics_exporter.urlopen", side_effect=fake):
            _send_influxdb(_BASE_META, cfg, "ws")
        assert len(captured) == 1
        url = captured[0].full_url
        assert "/api/v2/write" in url
        assert "org=myorg" in url
        assert "bucket=tgm" in url

    def test_line_protocol_contains_measurement_and_fields(self):
        cfg = _make_config()
        captured, fake = _fake_urlopen_factory()
        with patch("app.metrics_exporter.urlopen", side_effect=fake):
            _send_influxdb(_BASE_META, cfg, "ws")
        body = captured[0].data.decode()
        assert "tgm_execution" in body
        assert "duration_seconds=" in body
        assert "resources_add=" in body
        assert "resources_destroy=" in body

    def test_authorization_header_uses_token(self):
        cfg = _make_config()
        captured, fake = _fake_urlopen_factory()
        with patch("app.metrics_exporter.urlopen", side_effect=fake):
            _send_influxdb(_BASE_META, cfg, "ws")
        auth = captured[0].get_header("Authorization")
        assert auth == "Token tok123"


# ---------------------------------------------------------------------------
# TestSendPrometheus
# ---------------------------------------------------------------------------

class TestSendPrometheus:
    def test_skips_when_url_is_empty(self):
        cfg = _make_config(metrics_prometheus_url="")
        with patch("app.metrics_exporter.urlopen") as mock_u:
            _send_prometheus(_BASE_META, cfg, "ws")
        mock_u.assert_not_called()

    def test_correct_push_url(self):
        cfg = _make_config(
            metrics_prometheus_url="https://prom.example.com",
            metrics_prometheus_job="myjob",
        )
        captured, fake = _fake_urlopen_factory()
        with patch("app.metrics_exporter.urlopen", side_effect=fake):
            _send_prometheus(_BASE_META, cfg, "ws")
        url = captured[0].full_url
        assert "/metrics/job/myjob/instance/" in url

    def test_body_contains_prometheus_exposition(self):
        cfg = _make_config()
        captured, fake = _fake_urlopen_factory()
        with patch("app.metrics_exporter.urlopen", side_effect=fake):
            _send_prometheus(_BASE_META, cfg, "ws")
        body = captured[0].data.decode()
        assert "# HELP" in body
        assert "# TYPE" in body
        assert "tgm_execution_duration_seconds" in body
        assert "tgm_execution_resources_add" in body

    def test_basic_auth_when_username_set(self):
        cfg = _make_config(
            metrics_prometheus_username="user1",
            metrics_prometheus_password="pass1",
        )
        captured, fake = _fake_urlopen_factory()
        with patch("app.metrics_exporter.urlopen", side_effect=fake):
            _send_prometheus(_BASE_META, cfg, "ws")
        auth_header = captured[0].get_header("Authorization")
        expected_b64 = base64.b64encode(b"user1:pass1").decode()
        assert auth_header == f"Basic {expected_b64}"

    def test_no_auth_header_when_username_empty(self):
        cfg = _make_config(metrics_prometheus_username="", metrics_prometheus_password="")
        captured, fake = _fake_urlopen_factory()
        with patch("app.metrics_exporter.urlopen", side_effect=fake):
            _send_prometheus(_BASE_META, cfg, "ws")
        auth_header = captured[0].get_header("Authorization")
        assert auth_header is None


# ---------------------------------------------------------------------------
# TestSendGraphite
# ---------------------------------------------------------------------------

class TestSendGraphite:
    def test_skips_when_host_is_empty(self):
        cfg = _make_config(metrics_graphite_host="")
        with patch("socket.create_connection") as mock_conn:
            _send_graphite(_BASE_META, cfg, "ws")
        mock_conn.assert_not_called()

    def test_tcp_connection_used_by_default(self):
        cfg = _make_config(
            metrics_graphite_host="graphite.lan",
            metrics_graphite_port=2003,
            metrics_graphite_protocol="tcp",
        )
        mock_sock = MagicMock()
        mock_sock.__enter__ = lambda s: s
        mock_sock.__exit__ = MagicMock(return_value=False)
        with patch("socket.create_connection", return_value=mock_sock) as mock_conn:
            _send_graphite(_BASE_META, cfg, "ws")
        mock_conn.assert_called_once_with(("graphite.lan", 2003), timeout=5)
        mock_sock.sendall.assert_called_once()

    def test_graphite_metric_lines_format(self):
        cfg = _make_config(
            metrics_graphite_host="graphite.lan",
            metrics_graphite_protocol="tcp",
        )
        mock_sock = MagicMock()
        mock_sock.__enter__ = lambda s: s
        mock_sock.__exit__ = MagicMock(return_value=False)
        with patch("socket.create_connection", return_value=mock_sock):
            _send_graphite(_BASE_META, cfg, "ws")
        payload = mock_sock.sendall.call_args[0][0].decode()
        assert "tgm.workspaces.ws1.plan.duration_seconds" in payload
        assert "tgm.workspaces.ws1.plan.resources_add" in payload
        assert "tgm.workspaces.ws1.plan.status.completed" in payload

    def test_udp_socket_used_when_protocol_is_udp(self):
        cfg = _make_config(
            metrics_graphite_host="graphite.lan",
            metrics_graphite_port=2003,
            metrics_graphite_protocol="udp",
        )
        mock_sock = MagicMock()
        mock_sock.__enter__ = lambda s: s
        mock_sock.__exit__ = MagicMock(return_value=False)
        with patch("socket.socket", return_value=mock_sock) as mock_socket_cls:
            _send_graphite(_BASE_META, cfg, "ws")
        mock_socket_cls.assert_called_once_with(socket.AF_INET, socket.SOCK_DGRAM)
        mock_sock.sendto.assert_called_once()

    def test_workspace_id_sanitized_in_path(self):
        cfg = _make_config(
            metrics_graphite_host="graphite.lan",
            metrics_graphite_protocol="tcp",
        )
        meta = dict(_BASE_META, workspace_id="group1/my ws")
        mock_sock = MagicMock()
        mock_sock.__enter__ = lambda s: s
        mock_sock.__exit__ = MagicMock(return_value=False)
        with patch("socket.create_connection", return_value=mock_sock):
            _send_graphite(meta, cfg, "ws")
        payload = mock_sock.sendall.call_args[0][0].decode()
        # Slashes and spaces must be replaced; dots must not appear in ws_id segment
        assert "group1/my ws" not in payload
        assert "group1_my_ws" in payload
