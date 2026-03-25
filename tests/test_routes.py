"""
Tests for the Flask routes — uses the test client from conftest.py.
"""
import json

import pytest


class TestDashboardRoute:
    def test_dashboard_returns_200(self, client):
        resp = client.get("/")
        assert resp.status_code == 200

    def test_dashboard_contains_html(self, client):
        resp = client.get("/")
        assert b"<html" in resp.data.lower() or b"<!doctype" in resp.data.lower()


class TestAPIWorkspaces:
    def test_list_workspaces_returns_json(self, client):
        resp = client.get("/api/workspaces")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert isinstance(data, list)

    def test_unknown_workspace_returns_404(self, client):
        resp = client.get("/api/workspace/nonexistentworkspaceid")
        assert resp.status_code == 404
        data = json.loads(resp.data)
        assert "error" in data


class TestAPIVersions:
    def test_versions_endpoint_returns_json(self, client):
        resp = client.get("/api/versions")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "system_version" in data
        assert "available" in data
        assert isinstance(data["available"], list)


class TestAPIExecutions:
    def test_unknown_execution_returns_404(self, client):
        resp = client.get("/api/executions/doesnotexist")
        assert resp.status_code == 404

    def test_unknown_execution_logs_returns_404(self, client):
        resp = client.get("/api/executions/doesnotexist/logs")
        assert resp.status_code == 404

    def test_unknown_execution_plan_returns_404(self, client):
        resp = client.get("/api/executions/doesnotexist/plan")
        assert resp.status_code == 404

    def test_cancel_unknown_execution(self, client):
        resp = client.post("/api/executions/doesnotexist/cancel")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["ok"] is False


class TestAPIRunSubmission:
    def test_run_unknown_workspace_returns_404(self, client):
        resp = client.post(
            "/api/workspace/unknownworkspace/run",
            json={"command": "plan"},
        )
        assert resp.status_code == 404

    def test_run_invalid_command_returns_400(self, client):
        # We need a workspace — use the list to get one or just confirm 400 is returned
        # when command is invalid even when workspace is not found the check comes second,
        # so we exercise the 400 path by patching the workspace lookup.
        from unittest.mock import patch
        from app.workspace_scanner import WorkspaceScanner

        dummy_ws = {
            "id": "test_ws",
            "name": "test",
            "relative_path": "test",
            "abs_path": "/tmp/test",
            "providers": [],
            "backend": None,
        }
        with patch.object(WorkspaceScanner, "get_workspace_by_id", return_value=dummy_ws):
            resp = client.post(
                "/api/workspace/test_ws/run",
                json={"command": "destroy"},
            )
        assert resp.status_code == 400
        data = json.loads(resp.data)
        assert "error" in data


class TestSettingsRoute:
    def test_settings_page_returns_200(self, client):
        resp = client.get("/settings")
        assert resp.status_code == 200


class TestExecutionRoute:
    def test_execution_detail_unknown_id_redirects(self, client):
        # No index at /executions — detail route redirects unknown IDs to dashboard
        resp = client.get("/executions/unknownid", follow_redirects=False)
        assert resp.status_code in (302, 200)
