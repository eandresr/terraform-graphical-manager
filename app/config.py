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

    # ------------------------------------------------------------------
    # Sentinel
    # ------------------------------------------------------------------

    @property
    def sentinel_cli_path(self) -> str:
        """Absolute path to the Sentinel CLI binary, or empty to use PATH."""
        raw = self._parser.get("sentinel", "cli_path", fallback="")
        if not raw:
            return ""
        expanded = os.path.expanduser(raw)
        if not os.path.isabs(expanded):
            expanded = os.path.abspath(expanded)
        return expanded

    @property
    def sentinel_global_policies(self) -> str:
        """Directory containing global policy sets (applied to every workspace)."""
        raw = self._parser.get("sentinel", "global_policies", fallback="")
        if not raw:
            return ""
        expanded = os.path.expanduser(raw)
        if not os.path.isabs(expanded):
            expanded = os.path.abspath(expanded)
        return expanded

    @property
    def sentinel_enforce_on_plan(self) -> bool:
        """When True, every plan/apply automatically runs Sentinel checks."""
        return self._parser.getboolean("sentinel", "enforce_on_plan", fallback=False)

    @property
    def sentinel_enforce_on_apply(self) -> bool:
        """When True, block apply if Sentinel fails (requires enforce_on_plan)."""
        return self._parser.getboolean("sentinel", "enforce_on_apply", fallback=False)

    @property
    def sentinel_active_policy_sets(self) -> list:
        """Names of globally enabled policy sets; empty list = all enabled."""
        raw = self._parser.get("sentinel", "active_policy_sets", fallback="")
        return [n.strip() for n in raw.split(",") if n.strip()]

    # ------------------------------------------------------------------
    # Security
    # ------------------------------------------------------------------

    @property
    def lock_password_hash(self) -> str:
        """PBKDF2 hash of the portal lock password; empty string means unlocked."""
        return self._parser.get("security", "password_hash", fallback="")

    # ------------------------------------------------------------------
    # Metrics export
    # ------------------------------------------------------------------

    @property
    def metrics_backend(self) -> str:
        """One of: '', 'influxdb', 'prometheus', 'graphite'."""
        return self._parser.get("metrics", "backend", fallback="").strip().lower()

    @property
    def metrics_enabled(self) -> bool:
        return self._parser.getboolean("metrics", "enabled", fallback=False)

    @property
    def metrics_prefix(self) -> str:
        return self._parser.get("metrics", "prefix", fallback="tgm")

    # InfluxDB
    @property
    def metrics_influxdb_url(self) -> str:
        return self._parser.get("metrics", "influxdb_url", fallback="")

    @property
    def metrics_influxdb_token(self) -> str:
        return self._parser.get("metrics", "influxdb_token", fallback="")

    @property
    def metrics_influxdb_org(self) -> str:
        return self._parser.get("metrics", "influxdb_org", fallback="")

    @property
    def metrics_influxdb_bucket(self) -> str:
        return self._parser.get("metrics", "influxdb_bucket", fallback="tgm")

    @property
    def metrics_influxdb_verify_ssl(self) -> bool:
        return self._parser.getboolean("metrics", "influxdb_verify_ssl", fallback=True)

    # Prometheus Pushgateway
    @property
    def metrics_prometheus_url(self) -> str:
        return self._parser.get("metrics", "prometheus_url", fallback="")

    @property
    def metrics_prometheus_job(self) -> str:
        return self._parser.get("metrics", "prometheus_job", fallback="tgm")

    @property
    def metrics_prometheus_username(self) -> str:
        return self._parser.get("metrics", "prometheus_username", fallback="")

    @property
    def metrics_prometheus_password(self) -> str:
        return self._parser.get("metrics", "prometheus_password", fallback="")

    @property
    def metrics_prometheus_verify_ssl(self) -> bool:
        return self._parser.getboolean("metrics", "prometheus_verify_ssl", fallback=True)

    # ------------------------------------------------------------------
    # Run history retention
    # ------------------------------------------------------------------

    @property
    def history_retention_mode(self) -> str:
        """One of: 'none', 'count', 'days', 'size'."""
        return self._parser.get("history", "retention_mode", fallback="none").strip().lower()

    @property
    def history_retention_count(self) -> int:
        """Max number of runs to keep per workspace (retention_mode = count)."""
        return self._parser.getint("history", "retention_count", fallback=50)

    @property
    def history_retention_days(self) -> int:
        """Delete runs older than this many days (retention_mode = days)."""
        return self._parser.getint("history", "retention_days", fallback=90)

    @property
    def history_retention_size_mb(self) -> int:
        """Max total size in MB per workspace (retention_mode = size)."""
        return self._parser.getint("history", "retention_size_mb", fallback=500)

    # Graphite
    @property
    def metrics_graphite_host(self) -> str:
        return self._parser.get("metrics", "graphite_host", fallback="")

    @property
    def metrics_graphite_port(self) -> int:
        return self._parser.getint("metrics", "graphite_port", fallback=2003)

    @property
    def metrics_graphite_protocol(self) -> str:
        """'tcp' or 'udp'."""
        return self._parser.get("metrics", "graphite_protocol", fallback="tcp").strip().lower()

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
