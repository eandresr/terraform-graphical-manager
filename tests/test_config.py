"""
Tests for app/config.py — Config class.
"""
import configparser
import os

import pytest

from app.config import Config


class TestConfigDefaults:
    def test_repos_root_default(self, tmp_path):
        cfg = Config(config_path=str(tmp_path / "missing.conf"))
        assert cfg.repos_root == os.path.expanduser("~/terraform")

    def test_site_name_default(self, tmp_path):
        cfg = Config(config_path=str(tmp_path / "missing.conf"))
        assert cfg.site_name == "Terraform Graphical Manager"

    def test_repo_url_default(self, tmp_path):
        cfg = Config(config_path=str(tmp_path / "missing.conf"))
        assert "github.com" in cfg.repo_url

    def test_theme_default(self, tmp_path):
        cfg = Config(config_path=str(tmp_path / "missing.conf"))
        assert cfg.theme == "terraform-cloud"

    def test_max_concurrent_default(self, tmp_path):
        cfg = Config(config_path=str(tmp_path / "missing.conf"))
        assert cfg.max_concurrent_executions == 3

    def test_default_terraform_version(self, tmp_path):
        cfg = Config(config_path=str(tmp_path / "missing.conf"))
        assert cfg.default_terraform_version == "system"


class TestConfigFromFile:
    @pytest.fixture
    def conf_file(self, tmp_path):
        conf = tmp_path / "tfg.conf"
        conf.write_text(
            "[workspaces]\n"
            f"repos_root = {tmp_path}\n"
            "[ui]\n"
            "site_name = My TGM\n"
            "repo_url = https://example.com/my-repo\n"
            "theme = dark\n"
            "[execution]\n"
            "max_concurrent = 5\n"
            "[terraform]\n"
            "default_version = 1.6.0\n",
            encoding="utf-8",
        )
        return str(conf)

    def test_repos_root(self, conf_file, tmp_path):
        cfg = Config(config_path=conf_file)
        assert cfg.repos_root == str(tmp_path)

    def test_site_name(self, conf_file):
        cfg = Config(config_path=conf_file)
        assert cfg.site_name == "My TGM"

    def test_repo_url(self, conf_file):
        cfg = Config(config_path=conf_file)
        assert cfg.repo_url == "https://example.com/my-repo"

    def test_theme(self, conf_file):
        cfg = Config(config_path=conf_file)
        assert cfg.theme == "dark"

    def test_max_concurrent(self, conf_file):
        cfg = Config(config_path=conf_file)
        assert cfg.max_concurrent_executions == 5

    def test_default_version(self, conf_file):
        cfg = Config(config_path=conf_file)
        assert cfg.default_terraform_version == "1.6.0"


class TestConfigSave:
    def test_save_creates_section(self, tmp_path):
        conf = tmp_path / "tfg.conf"
        cfg = Config(config_path=str(conf))
        cfg.save({"ui.site_name": "Saved Name"})
        assert cfg.site_name == "Saved Name"

    def test_save_persists_to_disk(self, tmp_path):
        conf = tmp_path / "tfg.conf"
        cfg = Config(config_path=str(conf))
        cfg.save({"ui.theme": "light"})

        # Re-read from disk
        cfg2 = Config(config_path=str(conf))
        assert cfg2.theme == "light"

    def test_save_ignores_malformed_key(self, tmp_path):
        conf = tmp_path / "tfg.conf"
        cfg = Config(config_path=str(conf))
        # Should not raise
        cfg.save({"no_dot": "value"})
