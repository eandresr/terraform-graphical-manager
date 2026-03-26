"""
Settings Routes — UI page for editing tfg.conf visually.
"""
import os
from flask import Blueprint, current_app, render_template, request, redirect, url_for, flash

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

    # --- Portal lock password ---
    if data.get("remove_lock_password") == "1":
        updates["security.password_hash"] = ""
    else:
        new_password = data.get("lock_password", "").strip()
        if new_password:
            from app.auth import hash_password
            updates["security.password_hash"] = hash_password(new_password)

    try:
        config.save(updates)
        flash("Settings saved successfully.", "success")
    except Exception as exc:
        flash(f"Error saving settings: {exc}", "error")

    return redirect(url_for("settings.settings_page"))
