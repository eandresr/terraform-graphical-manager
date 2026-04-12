"""
Settings Routes — UI page for editing tfg.conf visually.
"""
import os
import platform
import shutil
import stat
import tempfile
import zipfile
from html.parser import HTMLParser
from urllib.request import urlopen, Request
from urllib.error import URLError

from flask import (
    Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for,
)

from app.version_manager import discover_versions, get_system_version
from app.sentinel_runner import sentinel_available, discover_policy_sets, get_sentinel_binary

settings_bp = Blueprint("settings", __name__)


def _get_backend_status() -> dict:
    """
    Inspect the current backend configuration and return a status dict
    describing what is set and where data is stored.
    """
    backend_type = os.environ.get("TERRAFORM_GRAPHICAL_BACKEND", "local").lower().strip()

    def _check(var_name: str, sensitive: bool = False) -> dict:
        val = os.environ.get(var_name)
        return {
            "name": var_name,
            "set": val is not None and val != "",
            "hint": ("*" * 8) if (sensitive and val) else (val or ""),
        }

    if backend_type == "aws":
        return {
            "type": "aws",
            "label": "AWS S3",
            "is_cloud": True,
            "var_backend": _check("TERRAFORM_GRAPHICAL_BACKEND"),
            "vars": [
                _check("TERRAFORM_GRAPHICAL_BACKEND_BUCKET"),
                _check("TERRAFORM_GRAPHICAL_BACKEND_AWS_ACCESS_KEY_ID", sensitive=True),
                _check("TERRAFORM_GRAPHICAL_BACKEND_AWS_SECRET_ACCESS_KEY", sensitive=True),
                _check("TERRAFORM_GRAPHICAL_BACKEND_AWS_REGION"),
            ],
        }

    if backend_type == "gcp":
        return {
            "type": "gcp",
            "label": "GCP Cloud Storage",
            "is_cloud": True,
            "var_backend": _check("TERRAFORM_GRAPHICAL_BACKEND"),
            "vars": [
                _check("TERRAFORM_GRAPHICAL_BACKEND_BUCKET"),
                _check("TERRAFORM_GRAPHICAL_BACKEND_GOOGLE_CREDENTIALS", sensitive=True),
            ],
        }

    if backend_type == "azure":
        return {
            "type": "azure",
            "label": "Azure Blob Storage",
            "is_cloud": True,
            "var_backend": _check("TERRAFORM_GRAPHICAL_BACKEND"),
            "vars": [
                _check("TERRAFORM_GRAPHICAL_BACKEND_CONTAINER"),
                _check("TERRAFORM_GRAPHICAL_BACKEND_CONNECTION_STRING", sensitive=True),
            ],
        }

    # local
    env_var_set = "TERRAFORM_GRAPHICAL_BACKEND" in os.environ
    local_path = os.environ.get(
        "TERRAFORM_GRAPHICAL_BACKEND_LOCAL_PATH",
        os.path.join(os.getcwd(), "TERRAFORM_GRAPHICAL_BACKEND"),
    )
    return {
        "type": "local",
        "label": "Local Filesystem",
        "is_cloud": False,
        "env_var_set": env_var_set,
        "env_var_value": os.environ.get("TERRAFORM_GRAPHICAL_BACKEND", ""),
        "path": os.path.abspath(local_path),
        "path_var_set": "TERRAFORM_GRAPHICAL_BACKEND_LOCAL_PATH" in os.environ,
    }


@settings_bp.route("/settings", methods=["GET"])
def settings_page():
    config = current_app.config["TFG_CONFIG"]

    system_version = get_system_version()
    available_versions = discover_versions(config.terraform_versions_folder)
    backend_status = _get_backend_status()

    sentinel_bin = get_sentinel_binary(config.sentinel_cli_path)
    sentinel_ok = sentinel_available(config.sentinel_cli_path)
    global_policy_sets = discover_policy_sets(config.sentinel_global_policies)

    return render_template(
        "settings.html",
        config=config,
        system_version=system_version,
        available_versions=available_versions,
        backend_status=backend_status,
        sentinel_available=sentinel_ok,
        sentinel_binary=sentinel_bin,
        global_policy_sets=global_policy_sets,
    )


@settings_bp.route("/settings", methods=["POST"])
def settings_save():
    config = current_app.config["TFG_CONFIG"]
    data = request.form

    updates = {}

    site_name = data.get("site_name", "").strip()
    if site_name:
        updates["ui.site_name"] = site_name

    repo_url = data.get("repo_url", "").strip()
    updates["ui.repo_url"] = repo_url

    repos_root = data.get("repos_root", "").strip()
    if repos_root:
        updates["workspaces.repos_root"] = repos_root

    max_concurrent = data.get("max_concurrent", "").strip()
    if max_concurrent.isdigit():
        updates["execution.max_concurrent"] = max_concurrent

    versions_folder = data.get("versions_folder", "").strip()
    updates["terraform.versions_folder"] = versions_folder

    default_version = data.get("default_version", "system").strip()
    updates["terraform.default_version"] = default_version

    # --- Sentinel ---
    sentinel_cli_path = data.get("sentinel_cli_path", "").strip()
    updates["sentinel.cli_path"] = sentinel_cli_path

    sentinel_global_policies = data.get("sentinel_global_policies", "").strip()
    updates["sentinel.global_policies"] = sentinel_global_policies

    updates["sentinel.enforce_on_plan"] = (
        "true" if data.get("sentinel_enforce_on_plan") == "1" else "false"
    )
    updates["sentinel.enforce_on_apply"] = (
        "true" if data.get("sentinel_enforce_on_apply") == "1" else "false"
    )
    updates["sentinel.active_policy_sets"] = data.get("sentinel_active_policy_sets", "").strip()

    # --- Metrics export ---
    updates["metrics.enabled"] = (
        "true" if data.get("metrics_enabled") == "1" else "false"
    )
    updates["metrics.backend"] = data.get("metrics_backend", "").strip().lower()
    updates["metrics.prefix"] = data.get("metrics_prefix", "tgm").strip()
    # InfluxDB
    updates["metrics.influxdb_url"] = data.get("metrics_influxdb_url", "").strip()
    updates["metrics.influxdb_org"] = data.get("metrics_influxdb_org", "").strip()
    updates["metrics.influxdb_bucket"] = data.get("metrics_influxdb_bucket", "tgm").strip()
    updates["metrics.influxdb_verify_ssl"] = (
        "true" if data.get("metrics_influxdb_verify_ssl") == "1" else "false"
    )
    # Sensitive metrics fields — route to Vault if enabled.
    from flask import session as _ms
    _menc = _ms.get("tgm_enc_key", "")
    for _msec, _mkey, _mform in [
        ("metrics", "influxdb_token",      "metrics_influxdb_token"),
        ("metrics", "prometheus_password", "metrics_prometheus_password"),
    ]:
        _mval = data.get(_mform, "").strip()
        if not _mval or _mval == "\u2022" * 8:
            # Keep existing stored value (Vault ref or plaintext)
            pass
        elif config.vault_enabled and _menc:
            from app import vault_manager as _mvm
            _mpath = _mvm.backend_credential_path(
                config.vault_path_prefix, _msec, _mkey
            )
            updates[f"{_msec}.{_mkey}"] = _mvm.store_secret(config, _menc, _mpath, _mval)
        else:
            updates[f"{_msec}.{_mkey}"] = _mval
    # Prometheus
    updates["metrics.prometheus_url"] = data.get("metrics_prometheus_url", "").strip()
    updates["metrics.prometheus_job"] = data.get("metrics_prometheus_job", "tgm").strip()
    updates["metrics.prometheus_username"] = data.get("metrics_prometheus_username", "").strip()
    # prometheus_password handled above (vault-aware)
    updates["metrics.prometheus_verify_ssl"] = (
        "true" if data.get("metrics_prometheus_verify_ssl") == "1" else "false"
    )
    # Graphite
    updates["metrics.graphite_host"] = data.get("metrics_graphite_host", "").strip()
    port_val = data.get("metrics_graphite_port", "2003").strip()
    updates["metrics.graphite_port"] = port_val if port_val.isdigit() else "2003"
    proto = data.get("metrics_graphite_protocol", "tcp").strip().lower()
    updates["metrics.graphite_protocol"] = proto if proto in ("tcp", "udp") else "tcp"

    # --- Run history retention ---
    retention_mode = data.get("history_retention_mode", "none").strip().lower()
    if retention_mode not in ("none", "count", "days", "size"):
        retention_mode = "none"
    updates["history.retention_mode"] = retention_mode

    retention_count = data.get("history_retention_count", "50").strip()
    updates["history.retention_count"] = retention_count if retention_count.isdigit() else "50"

    retention_days = data.get("history_retention_days", "90").strip()
    updates["history.retention_days"] = retention_days if retention_days.isdigit() else "90"

    retention_size_mb = data.get("history_retention_size_mb", "500").strip()
    updates["history.retention_size_mb"] = (
        retention_size_mb if retention_size_mb.isdigit() else "500"
    )

    # --- Portal lock password ---
    if data.get("remove_lock_password") == "1":
        updates["security.password_hash"] = ""
    else:
        new_password = data.get("lock_password", "").strip()
        if new_password:
            from app.auth import hash_password
            from flask import session as _session
            from app.variable_groups import reencrypt_all_sensitive
            from app.notification_manager import (
                reencrypt_all_sensitive as reencrypt_all_notif_sensitive,
            )
            old_enc_key = _session.get("tgm_enc_key", "")
            updates["security.password_hash"] = hash_password(new_password)
            # Re-encrypt all sensitive variables with the new password
            if old_enc_key and old_enc_key != new_password:
                try:
                    reencrypt_all_sensitive(old_enc_key, new_password)
                except Exception:
                    pass
                try:
                    reencrypt_all_notif_sensitive(old_enc_key, new_password)
                except Exception:
                    pass
                # Re-encrypt backend credentials (skip if Vault is the secrets backend)
                try:
                    from app.backend_config import (
                        get_backend_config, save_backend_config,
                        decrypt_fields, SENSITIVE_FIELDS,
                    )
                    bc = get_backend_config(config)
                    bt = (bc.get("type") or "").lower().strip()
                    if bt and SENSITIVE_FIELDS.get(bt):
                        # If all sensitive fields are Vault refs, nothing to re-encrypt.
                        sensitive_vals = [
                            bc.get(f, "") for f in SENSITIVE_FIELDS[bt] if bc.get(f)
                        ]
                        all_vault = sensitive_vals and all(
                            str(v).startswith("vault:") for v in sensitive_vals
                        )
                        if not all_vault:
                            bc_plain = decrypt_fields(bc, bt, old_enc_key)
                            # Re-encrypt only fields that are NOT Vault refs.
                            bc_reenc = {}
                            for f in SENSITIVE_FIELDS[bt]:
                                val = bc_plain.get(f)
                                if val and not str(val).startswith("vault:"):
                                    from app.crypto import encrypt as _enc
                                    bc_reenc[f] = _enc(val, new_password)
                                elif val:
                                    bc_reenc[f] = val  # vault ref, leave unchanged
                            bc_reenc["type"] = bt
                            # Preserve non-sensitive fields
                            for k, v in bc.items():
                                if k not in bc_reenc:
                                    bc_reenc[k] = v
                            save_backend_config(config, bc_reenc)
                except Exception:
                    pass
            # Keep the enc_key in session in sync with the new password
            _session["tgm_enc_key"] = new_password

    try:
        config.save(updates)
        flash("Settings saved successfully.", "success")
    except Exception as exc:
        flash(f"Error saving settings: {exc}", "error")

    return redirect(url_for("settings.settings_page"))


# ---------------------------------------------------------------------------
# Terraform version download / install / uninstall helpers
# ---------------------------------------------------------------------------

_HASHICORP_DEFAULT_BASE = "https://releases.hashicorp.com/terraform/"


def _validate_base_url(url: str) -> str:
    """Ensure the URL is HTTPS and strip trailing slash."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError("Base URL must use HTTPS.")
    if not parsed.netloc:
        raise ValueError("Base URL must include a hostname.")
    return url.rstrip("/")


def _platform_for_download():
    """Return (os_str, arch_str) matching HashiCorp naming."""
    system = platform.system().lower()
    if system == "darwin":
        os_str = "darwin"
    elif system == "windows":
        os_str = "windows"
    else:
        os_str = "linux"

    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        arch_str = "amd64"
    elif machine in ("aarch64", "arm64"):
        arch_str = "arm64"
    elif machine in ("i386", "i686", "x86"):
        arch_str = "386"
    else:
        arch_str = machine  # best effort

    return os_str, arch_str


class _VersionLinkParser(HTMLParser):
    """Extracts version strings from any releases index page."""
    def __init__(self):
        super().__init__()
        self.versions = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            for name, val in attrs:
                if name == "href" and val:
                    # Accept both absolute and relative hrefs; extract the last
                    # path segment that looks like a stable semver (X.Y.Z).
                    segments = val.strip("/").split("/")
                    for seg in reversed(segments):
                        parts = seg.split(".")
                        if (len(parts) == 3
                                and all(p.isdigit() for p in parts)):
                            self.versions.append(seg)
                            break


def _fetch_available_versions(base_url: str):
    """Scrape a releases index page; return sorted list newest-first."""
    url = base_url.rstrip("/") + "/"
    req = Request(url, headers={"User-Agent": "terraform-graphical-manager/1"})
    with urlopen(req, timeout=10) as resp:
        html = resp.read().decode("utf-8", errors="replace")
    parser = _VersionLinkParser()
    parser.feed(html)
    versions = sorted(set(parser.versions),
                      key=lambda v: tuple(int(x) for x in v.split(".")),
                      reverse=True)
    return versions


def _build_zip_url(base_url: str, ver: str, os_str: str, arch: str) -> str:
    """Construct the zip download URL relative to the configured base URL."""
    root = base_url.rstrip("/")
    return f"{root}/{ver}/terraform_{ver}_{os_str}_{arch}.zip"


# ---------------------------------------------------------------------------
# API: list remote versions with installed flag
# ---------------------------------------------------------------------------

@settings_bp.route("/api/terraform-versions/available", methods=["GET"])
def api_tf_versions_available():
    config = current_app.config["TFG_CONFIG"]
    raw_base = request.args.get("base_url", "").strip() or _HASHICORP_DEFAULT_BASE
    try:
        base_url = _validate_base_url(raw_base)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    try:
        remote = _fetch_available_versions(base_url)
    except URLError as exc:
        return jsonify({"error": f"Could not reach {base_url}: {exc}"}), 502
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    installed_set = {v["version"] for v in discover_versions(config.terraform_versions_folder)}

    default_os, default_arch = _platform_for_download()

    data = [
        {"version": v, "installed": v in installed_set}
        for v in remote
    ]
    return jsonify({
        "versions": data,
        "default_os": default_os,
        "default_arch": default_arch,
        "base_url": base_url,
    })


# ---------------------------------------------------------------------------
# API: download + install selected versions
# ---------------------------------------------------------------------------

@settings_bp.route("/api/terraform-versions/install", methods=["POST"])
def api_tf_versions_install():
    config = current_app.config["TFG_CONFIG"]
    body = request.get_json(force=True, silent=True) or {}
    versions_to_install = body.get("versions", [])
    os_str = body.get("os", "")
    arch = body.get("arch", "")
    raw_base = (body.get("base_url", "") or "").strip() or _HASHICORP_DEFAULT_BASE

    if not versions_to_install:
        return jsonify({"error": "No versions specified"}), 400
    if not os_str or not arch:
        return jsonify({"error": "os and arch are required"}), 400

    try:
        base_url = _validate_base_url(raw_base)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    # Validate inputs — only allow safe characters to prevent path traversal
    import re as _re
    safe_pat = _re.compile(r"^[a-z0-9_.]+$")
    for ver in versions_to_install:
        if not safe_pat.match(ver):
            return jsonify({"error": f"Invalid version: {ver}"}), 400
    if not safe_pat.match(os_str) or not safe_pat.match(arch):
        return jsonify({"error": "Invalid os or arch"}), 400

    versions_folder = config.terraform_versions_folder
    if not versions_folder:
        return jsonify({"error": "versions_folder is not configured"}), 400

    os.makedirs(versions_folder, exist_ok=True)

    installed = []
    errors = []

    for ver in versions_to_install:
        zip_url = _build_zip_url(base_url, ver, os_str, arch)
        dest_dir = os.path.join(versions_folder, ver)
        binary_name = "terraform.exe" if os_str == "windows" else "terraform"
        dest_binary = os.path.join(dest_dir, binary_name)

        if os.path.isfile(dest_binary):
            installed.append({"version": ver, "status": "already_installed"})
            continue

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                zip_path = os.path.join(tmpdir, f"terraform_{ver}.zip")
                req = Request(zip_url, headers={"User-Agent": "terraform-graphical-manager/1"})
                with urlopen(req, timeout=120) as resp, open(zip_path, "wb") as fh:
                    shutil.copyfileobj(resp, fh)

                with zipfile.ZipFile(zip_path) as zf:
                    members = [m for m in zf.namelist()
                               if os.path.basename(m) == binary_name and not m.endswith("/")]
                    if not members:
                        raise ValueError(f"Binary '{binary_name}' not found in zip")
                    os.makedirs(dest_dir, exist_ok=True)
                    zf.extract(members[0], tmpdir)
                    extracted = os.path.join(tmpdir, members[0])
                    shutil.move(extracted, dest_binary)

            # chmod +x
            st = os.stat(dest_binary)
            os.chmod(dest_binary, st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            installed.append({"version": ver, "status": "installed"})
        except Exception as exc:
            # Clean up partial install
            if os.path.isdir(dest_dir) and not os.listdir(dest_dir):
                shutil.rmtree(dest_dir, ignore_errors=True)
            errors.append({"version": ver, "error": str(exc)})

    status_code = 200 if not errors else (207 if installed else 500)
    return jsonify({"installed": installed, "errors": errors}), status_code


# ---------------------------------------------------------------------------
# API: uninstall a version
# ---------------------------------------------------------------------------

@settings_bp.route("/api/terraform-versions/uninstall/<version>", methods=["DELETE"])
def api_tf_versions_uninstall(version):
    import re as _re
    if not _re.match(r"^[0-9]+\.[0-9]+\.[0-9]+$", version):
        return jsonify({"error": "Invalid version string"}), 400

    config = current_app.config["TFG_CONFIG"]
    versions_folder = config.terraform_versions_folder
    if not versions_folder:
        return jsonify({"error": "versions_folder is not configured"}), 400

    # Find the on-disk directory for this version (may use dots or underscores)
    from app.version_manager import discover_versions as _disc
    match_dir = None
    for v in _disc(versions_folder):
        if v["version"] == version:
            match_dir = v["dir_name"]
            break
    if match_dir is None:
        return jsonify({"error": f"Version {version} is not installed"}), 404

    # Validate: no workspace's last run used this version
    from app.workspace_state import get_all as _ws_get_all
    last_states = _ws_get_all()
    blocking = [
        ws_id for ws_id, state in last_states.items()
        if state.get("terraform_version") == version
    ]
    if blocking:
        return jsonify({
            "error": (
                f"Version {version} is still the last-used version"
                f" for {len(blocking)} workspace(s)."
            ),
            "blocking_workspaces": blocking,
        }), 409

    target = os.path.join(versions_folder, match_dir)
    try:
        shutil.rmtree(target)
    except Exception as exc:
        return jsonify({"error": f"Could not remove directory: {exc}"}), 500

    return jsonify({"uninstalled": version}), 200


# ---------------------------------------------------------------------------
# Vault settings API
# ---------------------------------------------------------------------------

@settings_bp.route("/api/vault/config", methods=["GET"])
def api_vault_get_config():
    """Return the current Vault configuration (sensitive fields masked)."""
    config = current_app.config["TFG_CONFIG"]
    return jsonify({
        "enabled":       config.vault_enabled,
        "url":           config.vault_url,
        "auth_method":   config.vault_auth_method,
        "token":         "••••••••" if config.vault_token else "",
        "role_id":       config.vault_role_id,
        "secret_id":     "••••••••" if config.vault_secret_id else "",
        "mount":         config.vault_mount or "secret",
        "path_prefix":   config.vault_path_prefix or "tgm",
        "namespace":     config.vault_namespace,
        "verify_ssl":    config.vault_verify_ssl,
    })


@settings_bp.route("/api/vault/config", methods=["POST"])
def api_vault_save_config():
    """Save Vault configuration. Sensitive fields (token, secret_id) are Fernet-encrypted."""
    from flask import session as _session
    config = current_app.config["TFG_CONFIG"]
    body = request.get_json(silent=True) or {}
    enc_key = _session.get("tgm_enc_key", "")

    # Vault requires the portal to be password-protected so the auth credentials
    # can be encrypted at rest and decrypted at connection time.
    if body.get("enabled") and not enc_key:
        return jsonify({
            "ok": False,
            "error": "The portal must be password-protected to enable Vault.",
        }), 400
    if body.get("enabled") and not config.lock_password_hash:
        return jsonify({
            "ok": False,
            "error": "Set a portal password before enabling Vault.",
        }), 400

    updates = {}
    updates["vault.enabled"] = "true" if body.get("enabled") else "false"
    updates["vault.url"] = (body.get("url") or "").strip().rstrip("/")
    auth_method = (body.get("auth_method") or "token").strip().lower()
    if auth_method not in ("token", "approle"):
        auth_method = "token"
    updates["vault.auth_method"] = auth_method

    updates["vault.mount"] = (body.get("mount") or "secret").strip()
    updates["vault.path_prefix"] = (body.get("path_prefix") or "tgm").strip()
    updates["vault.namespace"] = (body.get("namespace") or "").strip()
    updates["vault.verify_ssl"] = "true" if body.get("verify_ssl", True) else "false"

    if auth_method == "token":
        token_val = (body.get("token") or "").strip()
        if token_val and token_val != "••••••••":
            if enc_key:
                from app.crypto import encrypt as _encrypt
                updates["vault.token"] = _encrypt(token_val, enc_key)
            else:
                updates["vault.token"] = token_val
        # keep role_id/secret_id clear
        updates["vault.role_id"] = ""
        updates["vault.secret_id"] = ""
    else:  # approle
        updates["vault.role_id"] = (body.get("role_id") or "").strip()
        secret_id_val = (body.get("secret_id") or "").strip()
        if secret_id_val and secret_id_val != "••••••••":
            if enc_key:
                from app.crypto import encrypt as _encrypt
                updates["vault.secret_id"] = _encrypt(secret_id_val, enc_key)
            else:
                updates["vault.secret_id"] = secret_id_val
        updates["vault.token"] = ""

    try:
        config.save(updates)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True})


@settings_bp.route("/api/vault/test", methods=["POST"])
def api_vault_test():
    """Test connectivity and authentication with the supplied Vault credentials."""
    from flask import session as _session
    from app.vault_manager import test_vault_connection
    config = current_app.config["TFG_CONFIG"]
    body = request.get_json(silent=True) or {}
    enc_key = _session.get("tgm_enc_key", "")

    url = (body.get("url") or config.vault_url or "").strip().rstrip("/")
    auth_method = (body.get("auth_method") or config.vault_auth_method or "token").lower()
    mount = (body.get("mount") or config.vault_mount or "secret").strip()
    namespace = (body.get("namespace") or config.vault_namespace or "").strip()
    verify_ssl = body.get("verify_ssl", config.vault_verify_ssl)

    token = ""
    role_id = ""
    secret_id = ""

    if auth_method == "token":
        token_raw = (body.get("token") or "").strip()
        if token_raw and token_raw != "••••••••":
            token = token_raw
        elif config.vault_token and enc_key:
            from app.crypto import decrypt as _decrypt
            try:
                token = _decrypt(config.vault_token, enc_key)
            except Exception:
                token = config.vault_token
    else:
        role_id = (body.get("role_id") or config.vault_role_id or "").strip()
        secret_id_raw = (body.get("secret_id") or "").strip()
        if secret_id_raw and secret_id_raw != "••••••••":
            secret_id = secret_id_raw
        elif config.vault_secret_id and enc_key:
            from app.crypto import decrypt as _decrypt
            try:
                secret_id = _decrypt(config.vault_secret_id, enc_key)
            except Exception:
                secret_id = config.vault_secret_id

    result = test_vault_connection(
        url=url,
        auth_method=auth_method,
        token=token,
        role_id=role_id,
        secret_id=secret_id,
        mount=mount,
        namespace=namespace,
        verify_ssl=verify_ssl,
    )
    status = 200 if result.get("ok") else 502
    return jsonify(result), status


@settings_bp.route("/api/vault/migrate-to-vault", methods=["POST"])
def api_vault_migrate_to_vault():
    """
    Migrate all Fernet-encrypted sensitive variables to Vault.
    Vault must be enabled and reachable before calling this endpoint.
    """
    from flask import session as _session
    from app.vault_manager import migrate_to_vault
    config = current_app.config["TFG_CONFIG"]
    enc_key = _session.get("tgm_enc_key", "")
    if not config.vault_enabled:
        return jsonify({"ok": False, "error": "Vault is not enabled."}), 400
    try:
        summary = migrate_to_vault(config, enc_key)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True, **summary})


@settings_bp.route("/api/vault/migrate-from-vault", methods=["POST"])
def api_vault_migrate_from_vault():
    """
    Pull all Vault-referenced sensitive variables back to local Fernet encryption
    and remove the corresponding Vault paths.
    """
    from flask import session as _session
    from app.vault_manager import migrate_from_vault
    config = current_app.config["TFG_CONFIG"]
    enc_key = _session.get("tgm_enc_key", "")
    try:
        summary = migrate_from_vault(config, enc_key, enc_key)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    # After migrating back, disable Vault in config automatically
    try:
        config.save({"vault.enabled": "false"})
    except Exception:
        pass
    return jsonify({"ok": True, **summary})
