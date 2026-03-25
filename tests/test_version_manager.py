"""
Tests for app/version_manager.py — discover_versions(), get_terraform_binary().
"""
import os
import stat

import pytest

from app.version_manager import discover_versions, get_terraform_binary


def _make_fake_terraform(path: str) -> None:
    """Create a fake executable terraform binary at *path*."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("#!/bin/sh\necho 'Terraform v0.0.0'\n")
    os.chmod(path, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP)


class TestDiscoverVersions:
    def test_none_folder_returns_empty(self):
        assert discover_versions(None) == []

    def test_missing_folder_returns_empty(self, tmp_path):
        assert discover_versions(str(tmp_path / "nonexistent")) == []

    def test_finds_versions(self, tmp_path):
        _make_fake_terraform(str(tmp_path / "1_5_7" / "terraform"))
        _make_fake_terraform(str(tmp_path / "1_6_0" / "terraform"))
        versions = discover_versions(str(tmp_path))
        version_strings = [v["version"] for v in versions]
        assert "1.5.7" in version_strings
        assert "1.6.0" in version_strings

    def test_sorted_newest_first(self, tmp_path):
        _make_fake_terraform(str(tmp_path / "1_5_7" / "terraform"))
        _make_fake_terraform(str(tmp_path / "1_6_0" / "terraform"))
        _make_fake_terraform(str(tmp_path / "1_4_0" / "terraform"))
        versions = discover_versions(str(tmp_path))
        parsed = [tuple(int(x) for x in v["version"].split(".")) for v in versions]
        assert parsed == sorted(parsed, reverse=True)

    def test_ignores_non_matching_dirs(self, tmp_path):
        (tmp_path / "latest").mkdir()
        versions = discover_versions(str(tmp_path))
        assert versions == []

    def test_ignores_dir_without_binary(self, tmp_path):
        (tmp_path / "1_5_7").mkdir()
        # No terraform binary inside
        versions = discover_versions(str(tmp_path))
        assert versions == []

    def test_version_entry_has_expected_keys(self, tmp_path):
        _make_fake_terraform(str(tmp_path / "1_6_0" / "terraform"))
        versions = discover_versions(str(tmp_path))
        v = versions[0]
        for key in ("version", "label", "binary", "dir_name"):
            assert key in v

    def test_label_format(self, tmp_path):
        _make_fake_terraform(str(tmp_path / "1_6_0" / "terraform"))
        versions = discover_versions(str(tmp_path))
        assert versions[0]["label"] == "Terraform 1.6.0"


class TestGetTerraformBinary:
    def test_system_returns_system_binary(self, tmp_path):
        binary = get_terraform_binary("system", str(tmp_path))
        # Returns the resolved path or the bareword - must end with 'terraform'
        assert binary.endswith("terraform") or binary == "terraform"

    def test_empty_version_returns_system(self, tmp_path):
        binary = get_terraform_binary("", str(tmp_path))
        assert binary.endswith("terraform") or binary == "terraform"

    def test_none_version_returns_system(self, tmp_path):
        binary = get_terraform_binary(None, str(tmp_path))
        assert binary.endswith("terraform") or binary == "terraform"

    def test_returns_specific_version_binary(self, tmp_path):
        _make_fake_terraform(str(tmp_path / "1_6_0" / "terraform"))
        binary = get_terraform_binary("1.6.0", str(tmp_path))
        assert binary.endswith("terraform")
        assert "1_6_0" in binary

    def test_falls_back_to_system_when_version_not_found(self, tmp_path):
        binary = get_terraform_binary("9.9.9", str(tmp_path))
        assert binary.endswith("terraform") or binary == "terraform"
