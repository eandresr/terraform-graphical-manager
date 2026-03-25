"""
Configuration loader for tfg.conf
"""
import configparser
import os


class Config:
    def __init__(self, config_path: str = "tfg.conf"):
        self._parser = configparser.ConfigParser()
        self.config_path = config_path
        if os.path.exists(config_path):
            self._parser.read(config_path)

    @property
    def repos_root(self) -> str:
        raw = self._parser.get(
            "workspaces", "repos_root", fallback=os.path.expanduser("~/terraform")
        )
        expanded = os.path.expanduser(raw)
        # Resolve relative paths against the working directory
        if not os.path.isabs(expanded):
            expanded = os.path.abspath(expanded)
        return expanded

    @property
    def site_name(self) -> str:
        return self._parser.get("ui", "site_name", fallback="Terraform Graphical Manager")

    @property
    def repo_url(self) -> str:
        return self._parser.get(
            "ui", "repo_url",
            fallback="https://github.com/eandresr/terraform-graphical-manager",
        )

    @property
    def theme(self) -> str:
        return self._parser.get("ui", "theme", fallback="terraform-cloud")

    @property
    def max_concurrent_executions(self) -> int:
        return self._parser.getint("execution", "max_concurrent", fallback=3)

    @property
    def terraform_versions_folder(self) -> str:
        raw = self._parser.get("terraform", "versions_folder", fallback="")
        if not raw:
            return ""
        expanded = os.path.expanduser(raw)
        if not os.path.isabs(expanded):
            expanded = os.path.abspath(expanded)
        return expanded

    @property
    def default_terraform_version(self) -> str:
        return self._parser.get("terraform", "default_version", fallback="system")

    @property
    def lock_password_hash(self) -> str:
        """PBKDF2 hash of the portal lock password; empty string means unlocked."""
        return self._parser.get("security", "password_hash", fallback="")

    def save(self, updates: dict) -> None:
        """
        Persist *updates* back to tfg.conf.
        *updates* maps ``"section.key"`` to a string value.
        Call with e.g. {"execution.max_concurrent": "5"}.
        """
        for dotkey, value in updates.items():
            section, _, key = dotkey.partition(".")
            if not section or not key:
                continue
            if not self._parser.has_section(section):
                self._parser.add_section(section)
            self._parser.set(section, key, str(value))
        with open(self.config_path, "w", encoding="utf-8") as fh:
            self._parser.write(fh)
        # Reload so in-process properties reflect the new values
        self._parser.read(self.config_path)
