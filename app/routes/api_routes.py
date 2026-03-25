"""
API Routes — JSON REST endpoints consumed by the frontend JS layer.
"""
import subprocess
from typing import Any, Dict

from flask import Blueprint, current_app, jsonify, request

from app.workspace_scanner import WorkspaceScanner
from app.env_validator import validate_credentials, build_execution_env
from app.execution_queue import Execution
from app.plan_parser import parse_plan
from app.state_parser import parse_state

api_bp = Blueprint("api", __name__)


# -------------------------------------------------------------------------
# Workspace listing
# -------------------------------------------------------------------------

@api_bp.route("/workspaces")
def list_workspaces():
    config = current_app.config["TFG_CONFIG"]
    scanner = WorkspaceScanner(config.repos_root)
    return jsonify(scanner.get_flat_list())


@api_bp.route("/workspace/<workspace_id>")
def get_workspace(workspace_id: str):
    workspace = _get_workspace_or_404(workspace_id)
    if workspace is None:
        return jsonify({"error": "Workspace not found"}), 404
    return jsonify(workspace)


# -------------------------------------------------------------------------
# Credential validation
# -------------------------------------------------------------------------

@api_bp.route("/workspace/<workspace_id>/credentials")
def workspace_credentials(workspace_id: str):
    workspace = _get_workspace_or_404(workspace_id)
    if workspace is None:
        return jsonify({"error": "Workspace not found"}), 404
    cred_status = validate_credentials(workspace["providers"])
    return jsonify({"providers": workspace["providers"], "credentials": cred_status})


# -------------------------------------------------------------------------
# Run submission
# -------------------------------------------------------------------------

@api_bp.route("/workspace/<workspace_id>/run", methods=["POST"])
def submit_run(workspace_id: str):
    workspace = _get_workspace_or_404(workspace_id)
    if workspace is None:
        return jsonify({"error": "Workspace not found"}), 404

    body: Dict[str, Any] = request.get_json(silent=True) or {}
    command = body.get("command", "plan")
    if command not in ("plan", "apply"):
        return jsonify({"error": "command must be 'plan' or 'apply'"}), 400

    user_env: Dict[str, str] = body.get("env_vars") or {}
    plan_execution_id: str = body.get("plan_execution_id")

    # Build isolated environment
    isolated_env = build_execution_env(workspace["providers"], user_env)

    # Resolve terraform binary — modal override takes precedence over workspace pin
    config = current_app.config["TFG_CONFIG"]
    from app.version_manager import get_terraform_binary
    from app.storage import get_backend
    version_override = body.get("terraform_version_override", "").strip()
    if version_override and version_override != "system":
        pinned_version = version_override
    else:
        try:
            ws_cfg = get_backend().get_workspace_config(workspace_id)
            pinned_version = ws_cfg.get("terraform_version") or config.default_terraform_version
        except Exception:
            pinned_version = config.default_terraform_version
    tf_binary = get_terraform_binary(pinned_version, config.terraform_versions_folder)

    execution = Execution(
        workspace_id=workspace_id,
        workspace_path=workspace["abs_path"],
        command=command,
        env_vars=isolated_env,
        providers=workspace["providers"],
        backend=workspace.get("backend"),
        plan_execution_id=plan_execution_id,
    )
    execution.terraform_binary = tf_binary

    eq = current_app.config["EXECUTION_QUEUE"]
    eq.submit(execution)

    return jsonify({"execution_id": execution.id, "status": execution.status.value})


# -------------------------------------------------------------------------
# Execution status / detail
# -------------------------------------------------------------------------

@api_bp.route("/executions/<execution_id>")
def get_execution(execution_id: str):
    eq = current_app.config["EXECUTION_QUEUE"]
    execution = eq.get(execution_id)
    if execution is None:
        return jsonify({"error": "Execution not found"}), 404
    return jsonify(execution.to_dict())


@api_bp.route("/executions/<execution_id>/logs")
def get_execution_logs(execution_id: str):
    eq = current_app.config["EXECUTION_QUEUE"]
    execution = eq.get(execution_id)
    if execution is None:
        return jsonify({"error": "Execution not found"}), 404

    # Historical execution: logs live on disk, not in memory
    if getattr(execution, "_from_storage", False) and not execution.logs:
        try:
            from app.storage import get_backend
            backend = get_backend()
            raw = backend.get_logs_by_id(execution_id)
            if raw:
                execution.logs = raw.splitlines()
        except Exception:
            pass

    offset = int(request.args.get("offset", 0))
    lines = execution.logs[offset:]
    return jsonify({"lines": lines, "total": len(execution.logs)})


@api_bp.route("/executions/<execution_id>/plan")
def get_execution_plan(execution_id: str):
    eq = current_app.config["EXECUTION_QUEUE"]
    execution = eq.get(execution_id)
    if execution is None:
        return jsonify({"error": "Execution not found"}), 404

    plan_json = execution.plan_json
    # Historical execution: plan.json lives on disk
    if not plan_json and getattr(execution, "_from_storage", False):
        try:
            from app.storage import get_backend
            backend = get_backend()
            plan_json = backend.get_plan_json_by_id(execution_id)
        except Exception:
            pass

    if not plan_json:
        return jsonify({"error": "No plan available"}), 404
    summary = parse_plan(plan_json)
    return jsonify(summary)


@api_bp.route("/executions/<execution_id>/cancel", methods=["POST"])
def cancel_execution(execution_id: str):
    eq = current_app.config["EXECUTION_QUEUE"]
    ok = eq.cancel(execution_id)
    return jsonify({"ok": ok})


@api_bp.route("/workspace/<workspace_id>/executions")
def workspace_executions(workspace_id: str):
    eq = current_app.config["EXECUTION_QUEUE"]
    runs = eq.list_for_workspace(workspace_id)
    runs_sorted = sorted(runs, key=lambda r: r.timestamp, reverse=True)
    return jsonify([r.to_dict() for r in runs_sorted])


# -------------------------------------------------------------------------
# Terraform state
# -------------------------------------------------------------------------

@api_bp.route("/workspace/<workspace_id>/state")
def workspace_state(workspace_id: str):
    workspace = _get_workspace_or_404(workspace_id)
    if workspace is None:
        return jsonify({"error": "Workspace not found"}), 404

    from app.terraform_runner import TerraformRunner
    from app.env_validator import build_execution_env

    runner = TerraformRunner(
        workspace["abs_path"], build_execution_env(workspace["providers"], {})
    )
    raw = runner.state_pull()
    if raw is None:
        return jsonify({"error": "Could not retrieve state"}), 500

    parsed = parse_state(raw)
    return jsonify(parsed)


# -------------------------------------------------------------------------
# Terraform graph
# -------------------------------------------------------------------------

@api_bp.route("/workspace/<workspace_id>/graph")
def workspace_graph(workspace_id: str):
    workspace = _get_workspace_or_404(workspace_id)
    if workspace is None:
        return jsonify({"error": "Workspace not found"}), 404

    from app.terraform_runner import TerraformRunner
    from app.env_validator import build_execution_env

    runner = TerraformRunner(
        workspace["abs_path"], build_execution_env(workspace["providers"], {})
    )
    dot = runner.graph()
    if dot is None:
        return jsonify({"error": "Could not generate graph"}), 500

    graph_data = _parse_dot(dot)
    return jsonify(graph_data)


# -------------------------------------------------------------------------
# Drift detection
# -------------------------------------------------------------------------

@api_bp.route("/workspace/<workspace_id>/drift")
def workspace_drift(workspace_id: str):
    workspace = _get_workspace_or_404(workspace_id)
    if workspace is None:
        return jsonify({"error": "Workspace not found"}), 404

    from app.terraform_runner import TerraformRunner
    from app.env_validator import build_execution_env

    runner = TerraformRunner(
        workspace["abs_path"], build_execution_env(workspace["providers"], {})
    )
    changes = runner.plan_refresh_only()

    has_drift = False
    if changes:
        for obj in changes:
            if obj.get("type") == "resource_drift":
                has_drift = True
                break

    return jsonify({"has_drift": has_drift, "changes": changes or []})


# -------------------------------------------------------------------------
# State lock detection
# -------------------------------------------------------------------------

@api_bp.route("/workspace/<workspace_id>/lock")
def workspace_lock(workspace_id: str):
    workspace = _get_workspace_or_404(workspace_id)
    if workspace is None:
        return jsonify({"error": "Workspace not found"}), 404

    from app.terraform_runner import TerraformRunner
    from app.env_validator import build_execution_env

    runner = TerraformRunner(
        workspace["abs_path"], build_execution_env(workspace["providers"], {})
    )
    locked = runner.check_lock()
    return jsonify({"locked": locked})


# -------------------------------------------------------------------------
# Terraform outputs
# -------------------------------------------------------------------------

@api_bp.route("/workspace/<workspace_id>/output")
def workspace_output(workspace_id: str):
    workspace = _get_workspace_or_404(workspace_id)
    if workspace is None:
        return jsonify({"error": "Workspace not found"}), 404

    from app.terraform_runner import TerraformRunner
    from app.env_validator import build_execution_env

    runner = TerraformRunner(
        workspace["abs_path"], build_execution_env(workspace["providers"], {})
    )
    raw = runner.output_json()
    if raw is None:
        return jsonify({
            "error": "Could not retrieve outputs (workspace may not be initialized)"
        }), 500

    # Sanitize sensitive outputs
    sanitized = {}
    for key, val in raw.items():
        if val.get("sensitive"):
            sanitized[key] = {
                "type": val.get("type"), "value": "***sensitive***", "sensitive": True
            }
        else:
            sanitized[key] = {
                "type": val.get("type"), "value": val.get("value"), "sensitive": False
            }
    return jsonify(sanitized)


# -------------------------------------------------------------------------
# Git pull
# -------------------------------------------------------------------------

@api_bp.route("/workspace/<workspace_id>/git-pull", methods=["POST"])
def git_pull(workspace_id: str):
    workspace = _get_workspace_or_404(workspace_id)
    if workspace is None:
        return jsonify({"error": "Workspace not found"}), 404
    try:
        result = subprocess.run(
            ["git", "pull"],
            cwd=workspace["abs_path"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        return jsonify({
            "ok": result.returncode == 0,
            "output": result.stdout + result.stderr,
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


# -------------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------------


# -------------------------------------------------------------------------
# Terraform versions
# -------------------------------------------------------------------------

@api_bp.route("/versions")
def list_versions():
    """Return available terraform versions from the configured versions folder
    plus the system version."""
    config = current_app.config["TFG_CONFIG"]
    from app.version_manager import discover_versions, get_system_version

    available = discover_versions(config.terraform_versions_folder)
    system_ver = get_system_version()

    return jsonify({
        "system_version": system_ver,
        "versions_folder": config.terraform_versions_folder,
        "default_version": config.default_terraform_version,
        "available": available,
    })


@api_bp.route("/workspace/<workspace_id>/version", methods=["GET"])
def get_workspace_version(workspace_id: str):
    workspace = _get_workspace_or_404(workspace_id)
    if workspace is None:
        return jsonify({"error": "Workspace not found"}), 404

    from app.storage import get_backend
    config = current_app.config["TFG_CONFIG"]
    try:
        ws_cfg = get_backend().get_workspace_config(workspace_id)
    except Exception:
        ws_cfg = {}

    pinned = ws_cfg.get("terraform_version")
    return jsonify({
        "pinned_version": pinned,
        "effective_version": pinned or config.default_terraform_version,
        "default_version": config.default_terraform_version,
    })


@api_bp.route("/workspace/<workspace_id>/version", methods=["POST"])
def set_workspace_version(workspace_id: str):
    workspace = _get_workspace_or_404(workspace_id)
    if workspace is None:
        return jsonify({"error": "Workspace not found"}), 404

    body = request.get_json(silent=True) or {}
    version = body.get("version", "").strip()

    from app.storage import get_backend
    try:
        backend = get_backend()
        ws_cfg = backend.get_workspace_config(workspace_id)
        if version and version != "system":
            ws_cfg["terraform_version"] = version
        else:
            ws_cfg.pop("terraform_version", None)
        backend.set_workspace_config(workspace_id, ws_cfg)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500

    return jsonify({"ok": True, "version": version or None})


def _get_workspace_or_404(workspace_id: str):
    config = current_app.config["TFG_CONFIG"]
    scanner = WorkspaceScanner(config.repos_root)
    return scanner.get_workspace_by_id(workspace_id)


@api_bp.route("/settings/api-token")
def get_api_token():
    """Return the current API Bearer token — requires an authenticated session."""
    config = current_app.config["TFG_CONFIG"]
    pwd_hash = config.lock_password_hash
    if not pwd_hash:
        return jsonify({"error": "Portal lock is not enabled."}), 404
    from app.auth import make_api_token
    token = make_api_token(pwd_hash, current_app.secret_key)
    return jsonify({"token": token})


def _parse_dot(dot_output: str) -> Dict:
    """
    Minimal DOT parser — extracts nodes and directed edges for the D3 graph.
    Returns {"nodes": [...], "links": [...]}.
    """
    import re

    nodes: Dict[str, Dict] = {}
    links = []

    edge_re = re.compile(r'"([^"]+)"\s*->\s*"([^"]+)"')
    node_re = re.compile(r'"([^"]+)"\s*\[label\s*=\s*"([^"]*)"')

    for line in dot_output.splitlines():
        m = node_re.search(line)
        if m:
            nid, label = m.group(1), m.group(2)
            nodes[nid] = {"id": nid, "label": label or nid}

        m = edge_re.search(line)
        if m:
            src, tgt = m.group(1), m.group(2)
            nodes.setdefault(src, {"id": src, "label": src})
            nodes.setdefault(tgt, {"id": tgt, "label": tgt})
            links.append({"source": src, "target": tgt})

    return {"nodes": list(nodes.values()), "links": links}
