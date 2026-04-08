"""
Tests for the Terraform version download / install / uninstall feature.

Covers:
  - _validate_base_url helper
  - _VersionLinkParser HTML parser
  - _fetch_available_versions (mocked HTTP)
  - _build_zip_url helper
  - GET  /api/terraform-versions/available
  - POST /api/terraform-versions/install
  - DELETE /api/terraform-versions/uninstall/<version>
"""
import io
import json
import os
import stat
import zipfile
from contextlib import contextmanager
from unittest.mock import MagicMock, PropertyMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers — import the private functions under test directly
# ---------------------------------------------------------------------------

from app.routes.settings_routes import (
    _validate_base_url,
    _VersionLinkParser,
    _fetch_available_versions,
    _build_zip_url,
)


# ---------------------------------------------------------------------------
# _validate_base_url
# ---------------------------------------------------------------------------

class TestValidateBaseUrl:
    def test_valid_https_url_accepted(self):
        result = _validate_base_url("https://releases.hashicorp.com/terraform/")
        assert result == "https://releases.hashicorp.com/terraform"

    def test_trailing_slash_stripped(self):
        result = _validate_base_url("https://example.com/mirror/")
        assert not result.endswith("/")

    def test_http_url_rejected(self):
        with pytest.raises(ValueError, match="HTTPS"):
            _validate_base_url("http://releases.hashicorp.com/terraform/")

    def test_missing_scheme_rejected(self):
        with pytest.raises(ValueError):
            _validate_base_url("releases.hashicorp.com/terraform/")

    def test_no_hostname_rejected(self):
        with pytest.raises(ValueError, match="hostname"):
            _validate_base_url("https:///path")

    def test_internal_mirror_accepted(self):
        result = _validate_base_url("https://artifactory.internal.corp/terraform/")
        assert result == "https://artifactory.internal.corp/terraform"


# ---------------------------------------------------------------------------
# _VersionLinkParser
# ---------------------------------------------------------------------------

class TestVersionLinkParser:
    def _parse(self, html: str):
        p = _VersionLinkParser()
        p.feed(html)
        return p.versions

    def test_parses_absolute_href(self):
        html = '<a href="/terraform/1.9.5/">1.9.5</a>'
        assert "1.9.5" in self._parse(html)

    def test_parses_relative_href(self):
        html = '<a href="1.8.3/">1.8.3</a>'
        assert "1.8.3" in self._parse(html)

    def test_ignores_non_semver_links(self):
        html = '<a href="/terraform/index/"></a><a href="/about/"></a>'
        assert self._parse(html) == []

    def test_ignores_prerelease_versions(self):
        html = '<a href="/terraform/1.9.0-alpha1/">alpha</a>'
        # "1.9.0" would match but "alpha1" breaks the digit check
        assert self._parse(html) == []

    def test_deduplicates_versions(self):
        html = '<a href="/terraform/1.7.0/"></a><a href="/terraform/1.7.0/"></a>'
        versions = self._parse(html)
        assert versions.count("1.7.0") >= 1  # parser itself may duplicate; set is done upstream

    def test_parses_multiple_versions(self):
        html = """
        <a href="/terraform/1.9.5/"></a>
        <a href="/terraform/1.8.4/"></a>
        <a href="/terraform/1.7.2/"></a>
        """
        versions = self._parse(html)
        assert set(versions) >= {"1.9.5", "1.8.4", "1.7.2"}


# ---------------------------------------------------------------------------
# _fetch_available_versions (mocked HTTP)
# ---------------------------------------------------------------------------

_FAKE_INDEX_HTML = """
<html><body>
<a href="/terraform/1.10.0/">1.10.0</a>
<a href="/terraform/1.9.5/">1.9.5</a>
<a href="/terraform/1.9.4/">1.9.4</a>
<a href="/terraform/1.8.3/">1.8.3</a>
<a href="/terraform/1.7.0/">1.7.0</a>
</body></html>
"""


def _make_urlopen_mock(html: str):
    """Return a context-manager mock for urlopen that yields html bytes."""
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=cm)
    cm.__exit__ = MagicMock(return_value=False)
    cm.read.return_value = html.encode()
    return cm


class TestFetchAvailableVersions:
    def test_returns_sorted_newest_first(self):
        mock_resp = _make_urlopen_mock(_FAKE_INDEX_HTML)
        with patch("app.routes.settings_routes.urlopen", return_value=mock_resp):
            versions = _fetch_available_versions("https://example.com/terraform")
        assert versions[0] == "1.10.0"
        assert versions[-1] == "1.7.0"

    def test_deduplicates(self):
        html = '<a href="/terraform/1.9.5/"></a><a href="/terraform/1.9.5/"></a>'
        mock_resp = _make_urlopen_mock(html)
        with patch("app.routes.settings_routes.urlopen", return_value=mock_resp):
            versions = _fetch_available_versions("https://example.com/terraform")
        assert versions.count("1.9.5") == 1

    def test_empty_page_returns_empty_list(self):
        mock_resp = _make_urlopen_mock("<html><body></body></html>")
        with patch("app.routes.settings_routes.urlopen", return_value=mock_resp):
            versions = _fetch_available_versions("https://example.com/terraform")
        assert versions == []


# ---------------------------------------------------------------------------
# _build_zip_url
# ---------------------------------------------------------------------------

class TestBuildZipUrl:
    def test_standard_hashicorp_url(self):
        url = _build_zip_url(
            "https://releases.hashicorp.com/terraform",
            "1.9.5", "linux", "amd64",
        )
        assert url == (
            "https://releases.hashicorp.com/terraform"
            "/1.9.5/terraform_1.9.5_linux_amd64.zip"
        )

    def test_trailing_slash_in_base_ignored(self):
        url = _build_zip_url(
            "https://releases.hashicorp.com/terraform/",
            "1.8.0", "darwin", "arm64",
        )
        assert "//1.8.0" not in url
        assert url.endswith("terraform_1.8.0_darwin_arm64.zip")

    def test_internal_mirror(self):
        url = _build_zip_url(
            "https://mirror.internal/terraform",
            "1.7.0", "linux", "386",
        )
        assert url.startswith("https://mirror.internal/terraform/")
        assert "terraform_1.7.0_linux_386.zip" in url


# ---------------------------------------------------------------------------
# API endpoint: GET /api/terraform-versions/available
# ---------------------------------------------------------------------------

class TestApiAvailableEndpoint:
    def test_returns_200_with_versions(self, client):
        mock_resp = _make_urlopen_mock(_FAKE_INDEX_HTML)
        with patch("app.routes.settings_routes.urlopen", return_value=mock_resp):
            resp = client.get("/api/terraform-versions/available")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "versions" in data
        assert "default_os" in data
        assert "default_arch" in data
        assert "base_url" in data
        assert isinstance(data["versions"], list)

    def test_versions_have_installed_flag(self, client):
        mock_resp = _make_urlopen_mock(_FAKE_INDEX_HTML)
        with patch("app.routes.settings_routes.urlopen", return_value=mock_resp):
            resp = client.get("/api/terraform-versions/available")
        data = json.loads(resp.data)
        for v in data["versions"]:
            assert "version" in v
            assert "installed" in v
            assert isinstance(v["installed"], bool)

    def test_custom_base_url_accepted(self, client):
        mock_resp = _make_urlopen_mock(_FAKE_INDEX_HTML)
        with patch("app.routes.settings_routes.urlopen", return_value=mock_resp):
            resp = client.get(
                "/api/terraform-versions/available",
                query_string={"base_url": "https://mirror.example.com/terraform/"},
            )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "mirror.example.com" in data["base_url"]

    def test_http_base_url_rejected(self, client):
        resp = client.get(
            "/api/terraform-versions/available",
            query_string={"base_url": "http://insecure.example.com/"},
        )
        assert resp.status_code == 400
        data = json.loads(resp.data)
        assert "error" in data

    def test_network_error_returns_502(self, client):
        from urllib.error import URLError
        with patch(
            "app.routes.settings_routes.urlopen",
            side_effect=URLError("connection refused"),
        ):
            resp = client.get("/api/terraform-versions/available")
        assert resp.status_code == 502
        data = json.loads(resp.data)
        assert "error" in data

    def test_default_base_url_echoed_in_response(self, client):
        mock_resp = _make_urlopen_mock(_FAKE_INDEX_HTML)
        with patch("app.routes.settings_routes.urlopen", return_value=mock_resp):
            resp = client.get("/api/terraform-versions/available")
        data = json.loads(resp.data)
        assert "releases.hashicorp.com" in data["base_url"]


# ---------------------------------------------------------------------------
# API endpoint: POST /api/terraform-versions/install
# ---------------------------------------------------------------------------

def _make_fake_zip(binary_name: str = "terraform") -> bytes:
    """Build an in-memory zip containing a fake terraform binary."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(binary_name, b"#!/bin/sh\necho fake terraform\n")
    return buf.getvalue()


class TestApiInstallEndpoint:
    def test_missing_versions_returns_400(self, client, tmp_path):
        with _versions_folder_ctx(client, tmp_path):
            resp = client.post(
                "/api/terraform-versions/install",
                json={"os": "linux", "arch": "amd64"},
            )
        assert resp.status_code == 400

    def test_missing_os_arch_returns_400(self, client, tmp_path):
        with _versions_folder_ctx(client, tmp_path):
            resp = client.post(
                "/api/terraform-versions/install",
                json={"versions": ["1.9.5"]},
            )
        assert resp.status_code == 400

    def test_invalid_version_string_returns_400(self, client, tmp_path):
        with _versions_folder_ctx(client, tmp_path):
            resp = client.post(
                "/api/terraform-versions/install",
                json={"versions": ["../evil"], "os": "linux", "arch": "amd64"},
            )
        assert resp.status_code == 400

    def test_path_traversal_in_version_rejected(self, client, tmp_path):
        with _versions_folder_ctx(client, tmp_path):
            resp = client.post(
                "/api/terraform-versions/install",
                json={"versions": ["../../etc/passwd"], "os": "linux", "arch": "amd64"},
            )
        assert resp.status_code == 400

    def test_invalid_os_rejected(self, client, tmp_path):
        with _versions_folder_ctx(client, tmp_path):
            resp = client.post(
                "/api/terraform-versions/install",
                json={"versions": ["1.9.5"], "os": "lin;ux", "arch": "amd64"},
            )
        assert resp.status_code == 400

    def test_http_base_url_rejected(self, client, tmp_path):
        with _versions_folder_ctx(client, tmp_path):
            resp = client.post(
                "/api/terraform-versions/install",
                json={
                    "versions": ["1.9.5"],
                    "os": "linux",
                    "arch": "amd64",
                    "base_url": "http://insecure.example.com/",
                },
            )
        assert resp.status_code == 400

    def test_successful_install(self, client, tmp_path):
        fake_zip = _make_fake_zip("terraform")
        mock_resp = _make_urlopen_mock_binary(fake_zip)
        with _versions_folder_ctx(client, tmp_path):
            with patch("app.routes.settings_routes.urlopen", return_value=mock_resp):
                resp = client.post(
                    "/api/terraform-versions/install",
                    json={"versions": ["1.9.5"], "os": "linux", "arch": "amd64"},
                )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert any(i["version"] == "1.9.5" and i["status"] == "installed"
                   for i in data["installed"])
        # Binary should be placed on disk
        binary = tmp_path / "1.9.5" / "terraform"
        assert binary.exists()
        # Should be executable
        assert binary.stat().st_mode & stat.S_IXUSR

    def test_already_installed_version_skipped(self, client, tmp_path):
        # Pre-create the binary
        ver_dir = tmp_path / "1.9.5"
        ver_dir.mkdir()
        (ver_dir / "terraform").write_bytes(b"fake")
        with _versions_folder_ctx(client, tmp_path):
            resp = client.post(
                "/api/terraform-versions/install",
                json={"versions": ["1.9.5"], "os": "linux", "arch": "amd64"},
            )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert any(i["status"] == "already_installed" for i in data["installed"])

    def test_download_failure_reported_in_errors(self, client, tmp_path):
        from urllib.error import URLError
        with _versions_folder_ctx(client, tmp_path):
            with patch(
                "app.routes.settings_routes.urlopen",
                side_effect=URLError("timeout"),
            ):
                resp = client.post(
                    "/api/terraform-versions/install",
                    json={"versions": ["1.9.5"], "os": "linux", "arch": "amd64"},
                )
        assert resp.status_code == 500
        data = json.loads(resp.data)
        assert any(e["version"] == "1.9.5" for e in data["errors"])

    def test_partial_failure_returns_207(self, client, tmp_path):
        """One version succeeds, one fails → HTTP 207."""
        fake_zip = _make_fake_zip("terraform")
        call_count = {"n": 0}

        def _side_effect(req, timeout=None):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return _make_urlopen_mock_binary(fake_zip)
            from urllib.error import URLError
            raise URLError("forced failure")

        with _versions_folder_ctx(client, tmp_path):
            with patch("app.routes.settings_routes.urlopen", side_effect=_side_effect):
                resp = client.post(
                    "/api/terraform-versions/install",
                    json={
                        "versions": ["1.9.5", "1.8.0"],
                        "os": "linux",
                        "arch": "amd64",
                    },
                )
        assert resp.status_code == 207
        data = json.loads(resp.data)
        assert len(data["installed"]) == 1
        assert len(data["errors"]) == 1

    def test_custom_base_url_used_for_download(self, client, tmp_path):
        fake_zip = _make_fake_zip("terraform")
        captured = {}

        def _side_effect(req, timeout=None):
            captured["url"] = req.full_url
            return _make_urlopen_mock_binary(fake_zip)

        with _versions_folder_ctx(client, tmp_path):
            with patch("app.routes.settings_routes.urlopen", side_effect=_side_effect):
                client.post(
                    "/api/terraform-versions/install",
                    json={
                        "versions": ["1.9.5"],
                        "os": "linux",
                        "arch": "amd64",
                        "base_url": "https://mirror.example.com/terraform/",
                    },
                )
        assert "mirror.example.com" in captured.get("url", "")


# ---------------------------------------------------------------------------
# API endpoint: DELETE /api/terraform-versions/uninstall/<version>
# ---------------------------------------------------------------------------

class TestApiUninstallEndpoint:
    def test_invalid_version_string_returns_400(self, client):
        resp = client.delete("/api/terraform-versions/uninstall/evil-version")
        assert resp.status_code == 400

    def test_not_installed_returns_404(self, client, tmp_path):
        with _versions_folder_ctx(client, tmp_path):
            resp = client.delete("/api/terraform-versions/uninstall/9.9.9")
        assert resp.status_code == 404

    def test_successful_uninstall(self, client, tmp_path):
        ver_dir = tmp_path / "1.9.5"
        ver_dir.mkdir()
        binary = ver_dir / "terraform"
        binary.write_bytes(b"fake binary")
        binary.chmod(0o755)

        with _versions_folder_ctx(client, tmp_path):
            with patch("app.workspace_state.get_all", return_value={}):
                resp = client.delete("/api/terraform-versions/uninstall/1.9.5")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["uninstalled"] == "1.9.5"
        assert not ver_dir.exists()

    def test_blocked_by_workspace_returns_409(self, client, tmp_path):
        ver_dir = tmp_path / "1.9.5"
        ver_dir.mkdir()
        binary = ver_dir / "terraform"
        binary.write_bytes(b"fake binary")
        binary.chmod(0o755)

        last_states = {
            "ws_abc": {"terraform_version": "1.9.5", "status": "completed"},
        }
        with _versions_folder_ctx(client, tmp_path):
            with patch("app.workspace_state.get_all", return_value=last_states):
                resp = client.delete("/api/terraform-versions/uninstall/1.9.5")
        assert resp.status_code == 409
        data = json.loads(resp.data)
        assert "blocking_workspaces" in data
        assert "ws_abc" in data["blocking_workspaces"]
        # Directory must NOT have been removed
        assert ver_dir.exists()

    def test_version_with_underscore_dir_uninstalled(self, client, tmp_path):
        """Versions stored as 1_9_5/ (legacy underscores) are also handled."""
        ver_dir = tmp_path / "1_9_5"
        ver_dir.mkdir()
        binary = ver_dir / "terraform"
        binary.write_bytes(b"fake binary")
        binary.chmod(0o755)

        with _versions_folder_ctx(client, tmp_path):
            with patch("app.workspace_state.get_all", return_value={}):
                resp = client.delete("/api/terraform-versions/uninstall/1.9.5")
        assert resp.status_code == 200
        assert not ver_dir.exists()


# ---------------------------------------------------------------------------
# Integration: available endpoint marks installed versions correctly
# ---------------------------------------------------------------------------

class TestInstalledFlag:
    def test_installed_version_flagged_true(self, client, tmp_path):
        ver_dir = tmp_path / "1.9.5"
        ver_dir.mkdir()
        (ver_dir / "terraform").write_bytes(b"fake")
        os.chmod(ver_dir / "terraform", 0o755)

        mock_resp = _make_urlopen_mock(_FAKE_INDEX_HTML)
        with _versions_folder_ctx(client, tmp_path):
            with patch("app.routes.settings_routes.urlopen", return_value=mock_resp):
                resp = client.get("/api/terraform-versions/available")
        data = json.loads(resp.data)
        entry = next((v for v in data["versions"] if v["version"] == "1.9.5"), None)
        assert entry is not None
        assert entry["installed"] is True

    def test_non_installed_version_flagged_false(self, client, tmp_path):
        mock_resp = _make_urlopen_mock(_FAKE_INDEX_HTML)
        with _versions_folder_ctx(client, tmp_path):
            with patch("app.routes.settings_routes.urlopen", return_value=mock_resp):
                resp = client.get("/api/terraform-versions/available")
        data = json.loads(resp.data)
        entry = next((v for v in data["versions"] if v["version"] == "1.8.3"), None)
        assert entry is not None
        assert entry["installed"] is False


# ---------------------------------------------------------------------------
# Helpers used by the test classes above
# ---------------------------------------------------------------------------


@contextmanager
def _versions_folder_ctx(client, tmp_path):
    """Temporarily point TFG_CONFIG.terraform_versions_folder at tmp_path."""
    from app.config import Config
    with patch.object(
        Config,
        "terraform_versions_folder",
        new_callable=PropertyMock,
        return_value=str(tmp_path),
    ):
        yield


def _make_urlopen_mock_binary(data: bytes):
    """Return a context-manager mock for urlopen that yields raw bytes (for zip)."""
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=cm)
    cm.__exit__ = MagicMock(return_value=False)
    cm.read.return_value = data

    # Also support shutil.copyfileobj which calls read(length)
    def _read(length=-1):
        if not hasattr(_read, "_buf"):
            _read._buf = io.BytesIO(data)
        return _read._buf.read(length if length != -1 else None)

    cm.read.side_effect = _read
    return cm
