"""
Tests for the metrics-related API endpoints in api_routes.py:

  GET  /api/workspace/<id>/stats
  GET  /api/metrics-config
  POST /api/metrics-config
  GET  /api/workspace/<id>/metrics-config
  POST /api/workspace/<id>/metrics-config
"""
import json
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DUMMY_WS = {
    "id": "ws1",
    "name": "demo",
    "relative_path": "demo",
    "abs_path": "/tmp/demo",
    "providers": [],
    "backend": None,
}

_COMPLETED_META = {
    "timestamp": "2025-03-01T10:00:00",
    "command": "plan",
    "status": "completed",
    "duration_seconds": 12,
    "resource_counts": {"create": 2, "update": 0, "delete": 0, "replace": 0, "no-op": 3},
    "state_resource_count": 5,
}

_FAILED_META = {
    "timestamp": "2025-03-02T11:00:00",
    "command": "apply",
    "status": "failed",
    "duration_seconds": 5,
    "resource_counts": None,
    "state_resource_count": None,
}

_RUNNING_META = {
    "timestamp": "2025-03-03T12:00:00",
    "command": "plan",
    "status": "running",
    "duration_seconds": None,
    "resource_counts": None,
    "state_resource_count": None,
}


def _mock_backend(runs=None, ws_cfg=None):
    """Return a configured mock storage backend."""
    b = MagicMock()
    b.list_executions.return_value = runs if runs is not None else []
    b.get_workspace_config.return_value = ws_cfg if ws_cfg is not None else {}
    b.set_workspace_config.return_value = None
    return b


# ---------------------------------------------------------------------------
# TestWorkspaceStats — GET /api/workspace/<id>/stats
# ---------------------------------------------------------------------------

class TestWorkspaceStats:
    def test_unknown_workspace_returns_404(self, client):
        from app.workspace_scanner import WorkspaceScanner
        with patch.object(WorkspaceScanner, "get_workspace_by_id", return_value=None):
            resp = client.get("/api/workspace/badid/stats")
        assert resp.status_code == 404
        assert "error" in json.loads(resp.data)

    def test_empty_runs_returns_empty_series(self, client):
        from app.workspace_scanner import WorkspaceScanner
        backend = _mock_backend(runs=[])
        with patch.object(WorkspaceScanner, "get_workspace_by_id", return_value=_DUMMY_WS), \
             patch("app.storage.get_backend", return_value=backend):
            resp = client.get("/api/workspace/ws1/stats")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["series"] == []

    def test_filters_non_terminal_statuses(self, client):
        from app.workspace_scanner import WorkspaceScanner
        runs = [_COMPLETED_META, _FAILED_META, _RUNNING_META]
        backend = _mock_backend(runs=runs)
        with patch.object(WorkspaceScanner, "get_workspace_by_id", return_value=_DUMMY_WS), \
             patch("app.storage.get_backend", return_value=backend):
            resp = client.get("/api/workspace/ws1/stats")
        data = json.loads(resp.data)
        statuses = {s["status"] for s in data["series"]}
        assert statuses <= {"completed", "failed"}
        assert len(data["series"]) == 2

    def test_series_sorted_by_timestamp(self, client):
        from app.workspace_scanner import WorkspaceScanner
        unsorted = [_FAILED_META, _COMPLETED_META]  # reversed order
        backend = _mock_backend(runs=unsorted)
        with patch.object(WorkspaceScanner, "get_workspace_by_id", return_value=_DUMMY_WS), \
             patch("app.storage.get_backend", return_value=backend):
            resp = client.get("/api/workspace/ws1/stats")
        data = json.loads(resp.data)
        timestamps = [s["timestamp"] for s in data["series"]]
        assert timestamps == sorted(timestamps)

    def test_truncates_to_last_50_runs(self, client):
        from app.workspace_scanner import WorkspaceScanner
        runs = [
            {
                "timestamp": f"2025-01-{i:02d}T00:00:00",
                "command": "plan",
                "status": "completed",
                "duration_seconds": i,
                "resource_counts": None,
                "state_resource_count": None,
            }
            for i in range(1, 61)  # 60 completed runs
        ]
        backend = _mock_backend(runs=runs)
        with patch.object(WorkspaceScanner, "get_workspace_by_id", return_value=_DUMMY_WS), \
             patch("app.storage.get_backend", return_value=backend):
            resp = client.get("/api/workspace/ws1/stats")
        data = json.loads(resp.data)
        assert len(data["series"]) == 50
        # Should be the LAST 50 (newest)
        assert data["series"][0]["duration_seconds"] == 11

    def test_falls_back_to_local_backend(self, client):
        from app.workspace_scanner import WorkspaceScanner
        local_runs = [_COMPLETED_META]
        mock_local = _mock_backend(runs=local_runs)
        with patch.object(WorkspaceScanner, "get_workspace_by_id", return_value=_DUMMY_WS), \
             patch("app.storage.get_backend", return_value=_mock_backend(runs=[])), \
             patch("app.storage.local_backend.LocalBackend", return_value=mock_local):
            resp = client.get("/api/workspace/ws1/stats")
        data = json.loads(resp.data)
        assert len(data["series"]) == 1

    def test_series_entry_has_expected_fields(self, client):
        from app.workspace_scanner import WorkspaceScanner
        backend = _mock_backend(runs=[_COMPLETED_META])
        with patch.object(WorkspaceScanner, "get_workspace_by_id", return_value=_DUMMY_WS), \
             patch("app.storage.get_backend", return_value=backend):
            resp = client.get("/api/workspace/ws1/stats")
        entry = json.loads(resp.data)["series"][0]
        for field in ("timestamp", "command", "status", "duration_seconds",
                      "resource_counts", "state_resource_count"):
            assert field in entry


# ---------------------------------------------------------------------------
# TestGlobalMetricsConfigGet — GET /api/metrics-config
# ---------------------------------------------------------------------------

class TestGlobalMetricsConfigGet:
    def test_returns_200_with_all_expected_keys(self, client):
        resp = client.get("/api/metrics-config")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        for key in (
            "enabled", "backend", "prefix",
            "influxdb_url", "influxdb_token", "influxdb_org",
            "influxdb_bucket", "influxdb_verify_ssl",
            "prometheus_url", "prometheus_job", "prometheus_username", "prometheus_password",
            "prometheus_verify_ssl",
            "graphite_host", "graphite_port", "graphite_protocol",
        ):
            assert key in data, f"Missing key: {key}"

    def test_default_values(self, client):
        resp = client.get("/api/metrics-config")
        data = json.loads(resp.data)
        assert data["enabled"] is False
        assert data["backend"] == ""
        assert data["prefix"] == "tgm"
        assert data["influxdb_bucket"] == "tgm"
        assert data["prometheus_job"] == "tgm"
        assert data["graphite_port"] == 2003
        assert data["graphite_protocol"] == "tcp"


# ---------------------------------------------------------------------------
# TestGlobalMetricsConfigPost — POST /api/metrics-config
# ---------------------------------------------------------------------------

class TestGlobalMetricsConfigPost:
    def test_returns_ok_true(self, client, flask_app):
        config_obj = flask_app.config["TFG_CONFIG"]
        with patch.object(config_obj, "save") as mock_save:
            resp = client.post(
                "/api/metrics-config",
                json={"enabled": True, "backend": "influxdb", "prefix": "myapp"},
            )
        assert resp.status_code == 200
        assert json.loads(resp.data)["ok"] is True
        mock_save.assert_called_once()

    def test_save_contains_correct_keys(self, client, flask_app):
        config_obj = flask_app.config["TFG_CONFIG"]
        captured = {}

        def _capture(updates):
            captured.update(updates)
        with patch.object(config_obj, "save", side_effect=_capture):
            client.post(
                "/api/metrics-config",
                json={"enabled": True, "backend": "prometheus"},
            )
        assert captured.get("metrics.enabled") == "true"
        assert captured.get("metrics.backend") == "prometheus"

    def test_returns_500_when_save_raises(self, client, flask_app):
        config_obj = flask_app.config["TFG_CONFIG"]
        with patch.object(config_obj, "save", side_effect=OSError("disk full")):
            resp = client.post("/api/metrics-config", json={"enabled": False})
        assert resp.status_code == 500
        assert json.loads(resp.data)["ok"] is False


# ---------------------------------------------------------------------------
# TestWorkspaceMetricsConfigGet — GET /api/workspace/<id>/metrics-config
# ---------------------------------------------------------------------------

class TestWorkspaceMetricsConfigGet:
    def test_returns_true_by_default(self, client):
        backend = _mock_backend(ws_cfg={})
        with patch("app.storage.get_backend", return_value=backend):
            resp = client.get("/api/workspace/ws1/metrics-config")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["metrics_enabled"] is True

    def test_returns_stored_false_value(self, client):
        backend = _mock_backend(ws_cfg={"metrics_enabled": False})
        with patch("app.storage.get_backend", return_value=backend):
            resp = client.get("/api/workspace/ws1/metrics-config")
        data = json.loads(resp.data)
        assert data["metrics_enabled"] is False

    def test_returns_true_when_backend_raises(self, client):
        with patch("app.storage.get_backend", side_effect=Exception("unavailable")):
            resp = client.get("/api/workspace/ws1/metrics-config")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["metrics_enabled"] is True


# ---------------------------------------------------------------------------
# TestWorkspaceMetricsConfigPost — POST /api/workspace/<id>/metrics-config
# ---------------------------------------------------------------------------

class TestWorkspaceMetricsConfigPost:
    def test_disables_metrics(self, client):
        backend = _mock_backend(ws_cfg={"metrics_enabled": True})
        with patch("app.storage.get_backend", return_value=backend):
            resp = client.post(
                "/api/workspace/ws1/metrics-config",
                json={"metrics_enabled": False},
            )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["ok"] is True
        assert data["metrics_enabled"] is False

    def test_enables_metrics(self, client):
        backend = _mock_backend(ws_cfg={"metrics_enabled": False})
        with patch("app.storage.get_backend", return_value=backend):
            resp = client.post(
                "/api/workspace/ws1/metrics-config",
                json={"metrics_enabled": True},
            )
        data = json.loads(resp.data)
        assert data["metrics_enabled"] is True

    def test_returns_500_when_backend_raises(self, client):
        mock_b = MagicMock()
        mock_b.get_workspace_config.side_effect = Exception("storage error")
        with patch("app.storage.get_backend", return_value=mock_b):
            resp = client.post(
                "/api/workspace/ws1/metrics-config",
                json={"metrics_enabled": False},
            )
        assert resp.status_code == 500
        assert json.loads(resp.data)["ok"] is False
