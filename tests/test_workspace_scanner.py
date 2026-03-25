"""
Tests for app/workspace_scanner.py — WorkspaceScanner.
"""
import os

import pytest

from app.workspace_scanner import (
    WorkspaceScanner,
    encode_workspace_id,
    decode_workspace_id,
)


class TestIDEncoding:
    def test_roundtrip(self):
        path = "projects/myapp/dev"
        assert decode_workspace_id(encode_workspace_id(path)) == path

    def test_url_safe(self):
        encoded = encode_workspace_id("a/b/c")
        assert "+" not in encoded
        assert "/" not in encoded

    def test_different_paths_give_different_ids(self):
        assert encode_workspace_id("a/b") != encode_workspace_id("a/c")


class TestWorkspaceScannerEmptyRoot:
    def test_missing_root_returns_empty(self, tmp_path):
        scanner = WorkspaceScanner(str(tmp_path / "nonexistent"))
        assert scanner.get_flat_list() == []

    def test_empty_dir_returns_empty(self, tmp_path):
        scanner = WorkspaceScanner(str(tmp_path))
        assert scanner.get_flat_list() == []


class TestWorkspaceScannerDetection:
    @pytest.fixture
    def ws_root(self, tmp_path):
        """Creates:
            root/
              project_a/main.tf
              project_b/variables.tf
              not_tf/README.md
        """
        (tmp_path / "project_a").mkdir()
        (tmp_path / "project_a" / "main.tf").write_text('resource "null_resource" "a" {}')
        (tmp_path / "project_b").mkdir()
        (tmp_path / "project_b" / "variables.tf").write_text('variable "env" {}')
        (tmp_path / "not_tf").mkdir()
        (tmp_path / "not_tf" / "README.md").write_text("# not terraform")
        return tmp_path

    def test_finds_tf_workspaces(self, ws_root):
        scanner = WorkspaceScanner(str(ws_root))
        workspaces = scanner.get_flat_list()
        names = {ws["name"] for ws in workspaces}
        assert "project_a" in names
        assert "project_b" in names

    def test_skips_non_tf_dirs(self, ws_root):
        scanner = WorkspaceScanner(str(ws_root))
        workspaces = scanner.get_flat_list()
        names = {ws["name"] for ws in workspaces}
        assert "not_tf" not in names

    def test_workspace_has_expected_keys(self, ws_root):
        scanner = WorkspaceScanner(str(ws_root))
        ws = scanner.get_flat_list()[0]
        for key in ("id", "name", "relative_path", "abs_path", "providers"):
            assert key in ws

    def test_abs_path_exists(self, ws_root):
        scanner = WorkspaceScanner(str(ws_root))
        for ws in scanner.get_flat_list():
            assert os.path.isdir(ws["abs_path"])

    def test_get_workspace_by_id_found(self, ws_root):
        scanner = WorkspaceScanner(str(ws_root))
        workspaces = scanner.get_flat_list()
        ws = workspaces[0]
        found = scanner.get_workspace_by_id(ws["id"])
        assert found is not None
        assert found["id"] == ws["id"]

    def test_get_workspace_by_id_not_found(self, ws_root):
        scanner = WorkspaceScanner(str(ws_root))
        assert scanner.get_workspace_by_id("nonexistentid") is None

    def test_hidden_dirs_skipped(self, tmp_path):
        (tmp_path / ".hidden").mkdir()
        (tmp_path / ".hidden" / "main.tf").write_text("")
        scanner = WorkspaceScanner(str(tmp_path))
        names = {ws["name"] for ws in scanner.get_flat_list()}
        assert ".hidden" not in names

    def test_terraform_cache_skipped(self, tmp_path):
        (tmp_path / "project").mkdir()
        (tmp_path / "project" / "main.tf").write_text("")
        (tmp_path / "project" / ".terraform").mkdir()
        scanner = WorkspaceScanner(str(tmp_path))
        names = {ws["name"] for ws in scanner.get_flat_list()}
        assert ".terraform" not in names
