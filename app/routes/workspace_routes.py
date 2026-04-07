"""
Workspace Routes — UI page routes for the dashboard and workspace detail views.
"""
from flask import Blueprint, current_app, render_template, redirect, url_for, request, flash
from app.workspace_scanner import WorkspaceScanner

workspace_bp = Blueprint("workspace", __name__)


@workspace_bp.route("/")
def dashboard():
    config = current_app.config["TFG_CONFIG"]
    scanner = WorkspaceScanner(config.repos_root)
    flat = scanner.get_flat_list()
    total = len(flat)

    # GitHub-token warnings from in-memory cache (populated on startup + workspace opens)
    from app.github_module_checker import get_all_warnings as _gh_warnings
    github_token_warnings = _gh_warnings()

    return render_template(
        "dashboard.html",
        total_workspaces=total,
        flat_workspaces=flat,
        repos_root=config.repos_root,
        github_token_warnings=github_token_warnings,
    )


@workspace_bp.route("/notifications")
def notifications_global():
    config = current_app.config["TFG_CONFIG"]
    return render_template("notifications_global.html", config=config)


@workspace_bp.route("/metrics")
def metrics_global():
    config = current_app.config["TFG_CONFIG"]
    return render_template("metrics_global.html", config=config)


@workspace_bp.route("/api-docs")
def api_docs():
    config = current_app.config["TFG_CONFIG"]
    has_portal = bool(config.lock_password_hash)
    return render_template("api_docs.html", config=config, has_portal=has_portal)


@workspace_bp.route("/variable-groups")
def variable_groups_global():
    config = current_app.config["TFG_CONFIG"]
    scanner = WorkspaceScanner(config.repos_root)
    flat = scanner.get_flat_list()
    return render_template("variable_groups.html", config=config, flat_workspaces=flat)


@workspace_bp.route("/workspace/<workspace_id>")
def workspace_detail(workspace_id: str):
    config = current_app.config["TFG_CONFIG"]
    scanner = WorkspaceScanner(config.repos_root)
    workspace = scanner.get_workspace_by_id(workspace_id)
    if workspace is None:
        flash("Workspace not found.", "error")
        return redirect(url_for("workspace.dashboard"))

    from app.env_validator import validate_credentials
    cred_status = validate_credentials(workspace["providers"])

    from app.storage import get_backend
    from app.sentinel_runner import discover_policy_sets
    try:
        ws_cfg = get_backend().get_workspace_config(workspace_id)
    except Exception:
        ws_cfg = {}

    config = current_app.config["TFG_CONFIG"]
    sentinel_extra_policies = ws_cfg.get("sentinel_extra_policies", "")
    sentinel_extra_sets = (
        discover_policy_sets(sentinel_extra_policies) if sentinel_extra_policies else []
    )
    global_policy_sets = discover_policy_sets(config.sentinel_global_policies)

    # Trigger a fresh GitHub-module / token check for this workspace and
    # refresh the cache entry so the dashboard reflects the current state.
    import threading as _threading
    from app.github_module_checker import check_workspace as _check_ws

    def _bg_check():
        try:
            _check_ws(
                workspace_id,
                workspace["abs_path"],
                workspace["name"],
                workspace["relative_path"],
            )
        except Exception:
            pass

    _threading.Thread(target=_bg_check, daemon=True, name=f"gh-check-{workspace_id[:8]}").start()

    return render_template(
        "workspace.html",
        workspace=workspace,
        cred_status=cred_status,
        active_tab=request.args.get("tab", "overview"),
        sentinel_extra_policies=sentinel_extra_policies,
        sentinel_extra_sets=sentinel_extra_sets,
        global_policy_sets=global_policy_sets,
        sentinel_enforce_on_plan=config.sentinel_enforce_on_plan,
        sentinel_enforce_on_apply=config.sentinel_enforce_on_apply,
    )


@workspace_bp.route("/workspace/<workspace_id>/git-pull", methods=["POST"])
def git_pull(workspace_id: str):
    import subprocess
    config = current_app.config["TFG_CONFIG"]
    scanner = WorkspaceScanner(config.repos_root)
    workspace = scanner.get_workspace_by_id(workspace_id)
    if workspace is None:
        return {"ok": False, "error": "Workspace not found"}, 404

    try:
        result = subprocess.run(
            ["git", "pull"],
            cwd=workspace["abs_path"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        output = result.stdout + result.stderr
        return {"ok": result.returncode == 0, "output": output}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}, 500
