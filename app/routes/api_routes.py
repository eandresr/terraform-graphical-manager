"""
API Routes — JSON REST endpoints consumed by the frontend JS layer.
"""
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
    if command not in ("plan", "apply", "destroy"):
        return jsonify({"error": "command must be 'plan', 'apply' or 'destroy'"}), 400

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

    # Sentinel enforcement gate — block apply if enforcement is on and last check failed
    if command == "apply":
        try:
            s_ws_cfg = get_backend().get_workspace_config(workspace_id)
            enforce = (
                s_ws_cfg.get("sentinel_enforce_on_apply", False)
                or config.sentinel_enforce_on_apply
            )
        except Exception:
            enforce = config.sentinel_enforce_on_apply
        if enforce:
            try:
                s_backend = get_backend()
                if hasattr(s_backend, "get_sentinel_last_result"):
                    last_sentinel = s_backend.get_sentinel_last_result(workspace_id)
                    if not last_sentinel or not last_sentinel.get("passed", False):
                        return jsonify({
                            "ok": False,
                            "error": (
                                "Sentinel policy check must pass before applying. "
                                "Use the Apply Preview to run a Sentinel check first."
                            ),
                        }), 403
                else:
                    return jsonify({
                        "ok": False,
                        "error": (
                            "Sentinel enforcement is enabled but the backend "
                            "does not support result persistence."
                        ),
                    }), 400
            except Exception as s_exc:
                return jsonify({
                    "ok": False,
                    "error": f"Sentinel verification failed: {s_exc}",
                }), 500

    # Inject variable-group vars (TF_VAR_* and plain env)
    from flask import session as _session
    from app.variable_groups import get_vars_for_workspace
    from app.crypto import decrypt as _decrypt
    _enc_key = _session.get("tgm_enc_key", "")
    _group_env, _sensitive_values, _var_entries = get_vars_for_workspace(workspace_id, _enc_key)
    isolated_env.update(_group_env)

    # Inject workspace-level individual variables (stored via Variables sub-tab)
    try:
        _ws_cfg = get_backend().get_workspace_config(workspace_id)
        for _var in _ws_cfg.get("variables", []):
            _key = (_var.get("key") or "").strip()
            if not _key:
                continue
            _raw = _var.get("value") or ""
            _is_sensitive = _var.get("sensitive", False)
            if _is_sensitive:
                if not _enc_key or not _raw:
                    continue
                try:
                    _val = _decrypt(_raw, _enc_key)
                    _sensitive_values.append(_val)
                    _display = "***"
                except ValueError:
                    continue
            else:
                _val = _raw
                _display = _raw
            _var_type = _var.get("type", "terraform")
            _env_key = f"TF_VAR_{_key}" if _var_type == "terraform" else _key
            isolated_env[_env_key] = _val
            _var_entries.append({
                "env_key": _env_key,
                "display_value": _display,
                "source": "workspace",
                "sensitive": _is_sensitive,
            })
    except Exception:
        pass

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
    execution.sensitive_values = _sensitive_values
    execution.git_pull = bool(body.get("git_pull", False))
    execution.enc_key = _enc_key
    execution.repos_root = config.repos_root
    execution.run_params = _collect_run_params(
        workspace_path=workspace["abs_path"],
        var_entries=_var_entries,
        user_env=user_env,
    )

    # Check for an existing execution lock before queuing.
    try:
        lock = get_backend().get_execution_lock(workspace_id)
    except Exception:
        lock = None
    if lock:
        _eid = (lock.get("execution_id") or "")
        _cmd = lock.get("command", "run")
        _reason = lock.get("reason", "")
        _reason_part = f" Reason: {_reason}" if _reason else ""
        _id_part = f"(execution {_eid[:8]})" if _eid else "(manual lock)"
        return jsonify({
            "ok": False,
            "locked": True,
            "lock": lock,
            "error": (
                f"Workspace is locked — {_cmd} {_id_part}.{_reason_part} "
                "Unlock the workspace or wait for the run to finish."
            ),
        }), 423

    eq = current_app.config["EXECUTION_QUEUE"]
    eq.submit(execution)

    return jsonify({"execution_id": execution.id, "status": execution.status.value})


# -------------------------------------------------------------------------
# Workspace execution lock status
# -------------------------------------------------------------------------

@api_bp.route("/workspace/<workspace_id>/execution-lock")
def get_workspace_execution_lock(workspace_id: str):
    """Return the active execution lock for the workspace, or an empty lock."""
    workspace = _get_workspace_or_404(workspace_id)
    if workspace is None:
        return jsonify({"error": "Workspace not found"}), 404
    try:
        from app.storage import get_backend as _get_backend
        lock = _get_backend().get_execution_lock(workspace_id)
    except Exception:
        lock = None
    if lock:
        return jsonify({"locked": True, "lock": lock})
    return jsonify({"locked": False, "lock": None})


@api_bp.route("/workspace/<workspace_id>/execution-lock", methods=["POST"])
def set_workspace_execution_lock(workspace_id: str):
    """Manually lock a workspace (requires a reason)."""
    workspace = _get_workspace_or_404(workspace_id)
    if workspace is None:
        return jsonify({"error": "Workspace not found"}), 404
    body: Dict[str, Any] = request.get_json(silent=True) or {}
    reason = (body.get("reason") or "").strip()
    if not reason:
        return jsonify({"error": "reason is required"}), 400
    import datetime as _dt
    lock_data = {
        "execution_id": None,
        "command": "manual",
        "started_at": _dt.datetime.utcnow().isoformat(),
        "workspace_id": workspace_id,
        "reason": reason,
        "manual": True,
    }
    try:
        from app.storage import get_backend as _get_backend
        _get_backend().set_execution_lock(workspace_id, lock_data)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True, "lock": lock_data})


@api_bp.route("/workspace/<workspace_id>/execution-lock", methods=["DELETE"])
def delete_workspace_execution_lock(workspace_id: str):
    """Force-clear the execution lock for a workspace."""
    workspace = _get_workspace_or_404(workspace_id)
    if workspace is None:
        return jsonify({"error": "Workspace not found"}), 404
    try:
        from app.storage import get_backend as _get_backend
        _get_backend().clear_execution_lock(workspace_id)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True})


# -------------------------------------------------------------------------
# Workspace execution statistics (time-series for overview charts)
# -------------------------------------------------------------------------

@api_bp.route("/workspace/<workspace_id>/stats")
def workspace_stats(workspace_id: str):
    """Return time-series execution stats (duration + resource counts) for charts."""
    from flask import session as _session
    workspace = _get_workspace_or_404(workspace_id)
    if workspace is None:
        return jsonify({"error": "Workspace not found"}), 404
    enc_key = _session.get("tgm_enc_key", "")
    try:
        from app.storage import get_backend as _gb
        all_meta = _gb(enc_key).list_executions(workspace_id)
    except Exception:
        all_meta = []

    # If the configured (cloud) backend returned nothing — either because it is
    # unreachable or because runs predate the cloud migration — fall back to the
    # local filesystem so charts still show historical data.
    if not all_meta:
        try:
            from app.storage.local_backend import LocalBackend as _LocalBackend
            local_meta = _LocalBackend().list_executions(workspace_id)
            if local_meta:
                all_meta = local_meta
        except Exception:
            pass

    terminal_statuses = {"completed", "failed"}
    series = []
    for meta in sorted(all_meta, key=lambda m: m.get("timestamp", "")):
        if meta.get("status") not in terminal_statuses:
            continue
        resource_counts = meta.get("resource_counts")
        # Backfill resource_counts for old plan runs that pre-date this field.
        if resource_counts is None and meta.get("command") == "plan":
            try:
                from app.storage import get_backend as _gb2
                plan_json = _gb2(enc_key).get_plan_json_by_id(meta.get("id", ""))
                if plan_json:
                    resource_counts = parse_plan(plan_json).get("counts")
            except Exception:
                pass
        series.append({
            "timestamp": meta.get("timestamp", ""),
            "command": meta.get("command", ""),
            "status": meta.get("status", ""),
            "duration_seconds": meta.get("duration_seconds"),
            "resource_counts": resource_counts,
            "state_resource_count": meta.get("state_resource_count"),
        })

    return jsonify({"series": series[-50:]})


@api_bp.route("/metrics-config", methods=["GET"])
def get_global_metrics_config():
    """Return the global metrics export configuration from tfg.conf."""
    config = current_app.config["TFG_CONFIG"]
    return jsonify({
        "enabled":                  config.metrics_enabled,
        "backend":                  config.metrics_backend,
        "prefix":                   config.metrics_prefix,
        "influxdb_url":             config.metrics_influxdb_url,
        "influxdb_token":           config.metrics_influxdb_token,
        "influxdb_org":             config.metrics_influxdb_org,
        "influxdb_bucket":          config.metrics_influxdb_bucket,
        "influxdb_verify_ssl":      config.metrics_influxdb_verify_ssl,
        "prometheus_url":           config.metrics_prometheus_url,
        "prometheus_job":           config.metrics_prometheus_job,
        "prometheus_username":      config.metrics_prometheus_username,
        "prometheus_password":      config.metrics_prometheus_password,
        "prometheus_verify_ssl":    config.metrics_prometheus_verify_ssl,
        "graphite_host":            config.metrics_graphite_host,
        "graphite_port":            config.metrics_graphite_port,
        "graphite_protocol":        config.metrics_graphite_protocol,
    })


@api_bp.route("/metrics-config", methods=["POST"])
def save_global_metrics_config():
    """Save the global metrics export configuration to tfg.conf."""
    config = current_app.config["TFG_CONFIG"]
    body: Dict[str, Any] = request.get_json(silent=True) or {}

    def _str(key, default=""):
        return str(body.get(key, default)).strip()

    def _bool(key, default=True):
        v = body.get(key, default)
        return v if isinstance(v, bool) else str(v).lower() in ("true", "1", "yes")

    updates = {
        "metrics.enabled":                  "true" if _bool("enabled") else "false",
        "metrics.backend":                  _str("backend").lower(),
        "metrics.prefix":                   _str("prefix", "tgm"),
        "metrics.influxdb_url":             _str("influxdb_url"),
        "metrics.influxdb_token":           _str("influxdb_token"),
        "metrics.influxdb_org":             _str("influxdb_org"),
        "metrics.influxdb_bucket":          _str("influxdb_bucket", "tgm"),
        "metrics.influxdb_verify_ssl":      "true" if _bool("influxdb_verify_ssl") else "false",
        "metrics.prometheus_url":           _str("prometheus_url"),
        "metrics.prometheus_job":           _str("prometheus_job", "tgm"),
        "metrics.prometheus_username":      _str("prometheus_username"),
        "metrics.prometheus_password":      _str("prometheus_password"),
        "metrics.prometheus_verify_ssl":    "true" if _bool("prometheus_verify_ssl") else "false",
        "metrics.graphite_host":            _str("graphite_host"),
        "metrics.graphite_port":            _str("graphite_port", "2003"),
        "metrics.graphite_protocol":        _str("graphite_protocol", "tcp"),
    }
    try:
        config.save(updates)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True})


@api_bp.route("/workspace/<workspace_id>/metrics-config", methods=["GET"])
def get_workspace_metrics_config(workspace_id: str):
    """Return per-workspace metrics opt-out flag."""
    try:
        from app.storage import get_backend as _gb
        ws_cfg = _gb().get_workspace_config(workspace_id)
    except Exception:
        ws_cfg = {}
    return jsonify({"metrics_enabled": ws_cfg.get("metrics_enabled", True)})


@api_bp.route("/workspace/<workspace_id>/metrics-config", methods=["POST"])
def set_workspace_metrics_config(workspace_id: str):
    """Toggle per-workspace metrics sending. Body: {"metrics_enabled": true|false}."""
    body: Dict[str, Any] = request.get_json(silent=True) or {}
    enabled = bool(body.get("metrics_enabled", True))
    try:
        from app.storage import get_backend as _gb
        backend = _gb()
        ws_cfg = backend.get_workspace_config(workspace_id)
        ws_cfg["metrics_enabled"] = enabled
        backend.set_workspace_config(workspace_id, ws_cfg)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True, "metrics_enabled": enabled})


# -------------------------------------------------------------------------
# Execution status / detail
# -------------------------------------------------------------------------

@api_bp.route("/executions/<execution_id>")
def get_execution(execution_id: str):
    from flask import session as _session
    eq = current_app.config["EXECUTION_QUEUE"]
    enc_key = _session.get("tgm_enc_key", "")
    execution = eq.get(execution_id, enc_key)
    if execution is None:
        return jsonify({"error": "Execution not found"}), 404
    return jsonify(execution.to_dict())


@api_bp.route("/executions/<execution_id>/logs")
def get_execution_logs(execution_id: str):
    from flask import session as _session
    eq = current_app.config["EXECUTION_QUEUE"]
    enc_key = _session.get("tgm_enc_key", "")
    execution = eq.get(execution_id, enc_key)
    if execution is None:
        return jsonify({"error": "Execution not found"}), 404

    # Historical execution: logs live on disk, not in memory
    if getattr(execution, "_from_storage", False) and not execution.logs:
        try:
            from app.storage import get_backend
            backend = get_backend(enc_key)
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
    from flask import session as _session
    enc_key = _session.get("tgm_enc_key", "")
    eq = current_app.config["EXECUTION_QUEUE"]
    execution = eq.get(execution_id, enc_key)
    if execution is None:
        return jsonify({"error": "Execution not found"}), 404

    plan_json = execution.plan_json
    # Historical execution: plan.json lives on disk
    if not plan_json and getattr(execution, "_from_storage", False):
        try:
            from app.storage import get_backend
            backend = get_backend(enc_key)
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
    from flask import session as _session
    eq = current_app.config["EXECUTION_QUEUE"]
    enc_key = _session.get("tgm_enc_key", "")
    runs = eq.list_for_workspace(workspace_id, enc_key)
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
# Resource history (run tracking per resource)
# -------------------------------------------------------------------------

@api_bp.route("/workspace/<workspace_id>/resource-history")
def workspace_resource_history(workspace_id: str):
    workspace = _get_workspace_or_404(workspace_id)
    if workspace is None:
        return jsonify({"error": "Workspace not found"}), 404

    try:
        from app.storage import get_backend
        backend = get_backend()
        getter = getattr(backend, "get_resource_history", None)
        history = getter(workspace_id) if getter else {}
    except Exception:
        history = {}

    return jsonify(history)


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
    from flask import session as _fls
    from app.git_manager import pull as gm_pull, get_token_for_workspace
    enc_key = _fls.get("tgm_enc_key", "")
    token = get_token_for_workspace(workspace_id, enc_key)
    result = gm_pull(workspace["abs_path"], token)
    return jsonify({"ok": result["ok"], "output": result["output"]})


# -------------------------------------------------------------------------
# Git refs listing & checkout
# -------------------------------------------------------------------------

@api_bp.route("/workspace/<workspace_id>/git/refs")
def workspace_git_refs(workspace_id: str):
    workspace = _get_workspace_or_404(workspace_id)
    if workspace is None:
        return jsonify({"error": "Workspace not found"}), 404
    if not workspace.get("has_git"):
        return jsonify({"branches": [], "tags": [], "current": {}})
    from app.git_manager import list_refs
    return jsonify(list_refs(workspace["abs_path"]))


@api_bp.route("/workspace/<workspace_id>/git/checkout", methods=["POST"])
def workspace_git_checkout(workspace_id: str):
    workspace = _get_workspace_or_404(workspace_id)
    if workspace is None:
        return jsonify({"error": "Workspace not found"}), 404
    if not workspace.get("has_git"):
        return jsonify({"ok": False, "output": "Not a git repository"}), 400
    body = request.get_json(silent=True) or {}
    ref = (body.get("ref") or "").strip()
    remote_only = bool(body.get("remote_only", False))
    if not ref:
        return jsonify({"ok": False, "output": "ref is required"}), 400
    from app.git_manager import checkout_ref
    result = checkout_ref(workspace["abs_path"], ref, remote_only=remote_only)
    return jsonify(result)


@api_bp.route("/workspace/<workspace_id>/git/fetch", methods=["POST"])
def workspace_git_fetch(workspace_id: str):
    workspace = _get_workspace_or_404(workspace_id)
    if workspace is None:
        return jsonify({"error": "Workspace not found"}), 404
    if not workspace.get("has_git"):
        return jsonify({"ok": False, "output": "Not a git repository"}), 400
    from flask import session as _fls
    from app.git_manager import fetch_all, get_token_for_workspace
    enc_key = _fls.get("tgm_enc_key", "")
    token = get_token_for_workspace(workspace_id, enc_key)
    result = fetch_all(workspace["abs_path"], token)
    return jsonify(result)


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


# ---------------------------------------------------------------------------
# Run params collector
# ---------------------------------------------------------------------------

def _scan_tfvars_values(workspace_path: str) -> list:
    """
    Scan .tfvars / .tfvars.json files in *workspace_path* and return a list
    of dicts ``{key, value, file}`` for each assignment found.
    Complex types (lists/maps) are summarised as ``"[...]"`` / ``"{...}"``.
    """
    import json
    import os
    import re

    entries: list = []
    try:
        fnames = sorted(
            f for f in os.listdir(workspace_path)
            if f.endswith(".tfvars") or f.endswith(".tfvars.json")
        )
    except OSError:
        return entries

    for fname in fnames:
        fpath = os.path.join(workspace_path, fname)
        if fname.endswith(".json"):
            try:
                with open(fpath, encoding="utf-8") as fh:
                    data = json.load(fh)
                for k, v in data.items():
                    entries.append({"key": str(k), "value": str(v), "file": fname})
            except Exception:
                pass
        else:
            try:
                with open(fpath, encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        m = re.match(r"^\s*([A-Za-z_]\w*)\s*=\s*(.+)", line.rstrip())
                        if not m:
                            continue
                        raw = m.group(2).strip().rstrip(",")
                        if raw.startswith('"') and raw.endswith('"'):
                            val = raw[1:-1]
                        elif raw.startswith("["):
                            val = "[...]"
                        elif raw.startswith("{"):
                            val = "{...}"
                        else:
                            val = raw
                        entries.append({"key": m.group(1), "value": val, "file": fname})
            except OSError:
                pass
    return entries


def _scan_variable_defaults(workspace_path: str) -> Dict[str, str]:
    """
    Scan *.tf files in *workspace_path* for variable blocks and return a dict
    mapping variable name → default value string.
    Only captures single-line scalar defaults (string, number, bool).
    """
    import os
    import re

    defaults: Dict[str, str] = {}
    try:
        fnames = [f for f in os.listdir(workspace_path) if f.endswith(".tf")]
    except OSError:
        return defaults

    for fname in fnames:
        fpath = os.path.join(workspace_path, fname)
        try:
            with open(fpath, encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
        except OSError:
            continue
        i = 0
        while i < len(lines):
            vm = re.match(r'\s*variable\s+"([^"]+)"\s*\{', lines[i])
            if vm:
                varname = vm.group(1)
                depth = lines[i].count("{") - lines[i].count("}")
                j = i + 1
                while j < len(lines) and depth > 0:
                    depth += lines[j].count("{") - lines[j].count("}")
                    if depth > 0:
                        dm = re.match(r"\s*default\s*=\s*(.+)", lines[j].rstrip())
                        if dm:
                            dval = dm.group(1).strip().rstrip(",")
                            if dval.startswith('"') and dval.endswith('"'):
                                dval = dval[1:-1]
                            if varname not in defaults:
                                defaults[varname] = dval
                    j += 1
                i = j
            else:
                i += 1
    return defaults


def _collect_run_params(
    workspace_path: str,
    var_entries: list,
    user_env: Dict[str, str],
) -> list:
    """
    Build a structured list of variable entries for the run detail "Values"
    panel.  Each entry: {env_key, key, value, sensitive, source, file}.

    Sources: workspace | carpeta | env | tfvars | default
    """
    params: list = []
    seen_keys: set = set()

    # ── 1. Variable groups (workspace / carpeta) ───────────────────────
    for entry in var_entries:
        env_key = entry["env_key"]
        params.append({
            "env_key": env_key,
            "key": env_key[7:] if env_key.startswith("TF_VAR_") else env_key,
            "value": entry["display_value"],
            "sensitive": entry["sensitive"],
            "source": entry["source"],
            "file": None,
        })
        seen_keys.add(env_key)

    # ── 2. User-supplied TF_VAR_* from the run modal ───────────────────
    for k in sorted(user_env):
        if not k.startswith("TF_VAR_"):
            continue
        params.append({
            "env_key": k,
            "key": k[7:],
            "value": user_env[k],
            "sensitive": False,
            "source": "env",
            "file": None,
        })
        seen_keys.add(k)

    # ── 3. .tfvars files in workspace ─────────────────────────────────
    for entry in _scan_tfvars_values(workspace_path):
        env_key = f"TF_VAR_{entry['key']}"
        params.append({
            "env_key": env_key,
            "key": entry["key"],
            "value": entry["value"],
            "sensitive": False,
            "source": "tfvars",
            "file": entry["file"],
        })
        seen_keys.add(env_key)

    # ── 4. Default values from .tf variable declarations ──────────────
    for varname, default_val in sorted(_scan_variable_defaults(workspace_path).items()):
        env_key = f"TF_VAR_{varname}"
        if env_key in seen_keys:
            continue
        params.append({
            "env_key": env_key,
            "key": varname,
            "value": default_val,
            "sensitive": False,
            "source": "default",
            "file": "variables.tf",
        })
    return params


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


# -------------------------------------------------------------------------
# Sentinel
# -------------------------------------------------------------------------

@api_bp.route("/sentinel/config")
def sentinel_config():
    """Return the Sentinel configuration + detected policy sets."""
    config = current_app.config["TFG_CONFIG"]
    from app.sentinel_runner import sentinel_available, discover_policy_sets, get_sentinel_binary

    cli_bin = get_sentinel_binary(config.sentinel_cli_path)
    available = sentinel_available(config.sentinel_cli_path)

    global_sets = discover_policy_sets(config.sentinel_global_policies)
    return jsonify({
        "available": available,
        "cli_path": config.sentinel_cli_path or "",
        "cli_binary": cli_bin,
        "global_policies": config.sentinel_global_policies or "",
        "enforce_on_plan": config.sentinel_enforce_on_plan,
        "enforce_on_apply": config.sentinel_enforce_on_apply,
        "global_policy_sets": global_sets,
        "active_policy_sets": config.sentinel_active_policy_sets,
    })


@api_bp.route("/workspace/<workspace_id>/sentinel/config", methods=["GET"])
def get_workspace_sentinel_config(workspace_id: str):
    """Return the workspace-level Sentinel override (extra policies path)."""
    workspace = _get_workspace_or_404(workspace_id)
    if workspace is None:
        return jsonify({"error": "Workspace not found"}), 404

    from app.storage import get_backend
    from app.sentinel_runner import discover_policy_sets
    try:
        ws_cfg = get_backend().get_workspace_config(workspace_id)
    except Exception:
        ws_cfg = {}

    extra_path = ws_cfg.get("sentinel_extra_policies", "")
    extra_sets = discover_policy_sets(extra_path) if extra_path else []
    return jsonify({
        "extra_policies": extra_path,
        "extra_policy_sets": extra_sets,
        "active_global_sets": ws_cfg.get("sentinel_active_global_sets"),
        "active_extra_sets": ws_cfg.get("sentinel_active_extra_sets"),
        "sentinel_enforce_on_apply": ws_cfg.get("sentinel_enforce_on_apply", False),
    })


@api_bp.route("/workspace/<workspace_id>/sentinel/config", methods=["POST"])
def set_workspace_sentinel_config(workspace_id: str):
    """Save the workspace-level extra Sentinel policies path."""
    workspace = _get_workspace_or_404(workspace_id)
    if workspace is None:
        return jsonify({"error": "Workspace not found"}), 404

    body = request.get_json(silent=True) or {}
    extra_path = body.get("extra_policies", "").strip()
    active_global_sets = body.get("active_global_sets")  # None or list of names
    active_extra_sets = body.get("active_extra_sets")    # None or list of names

    from app.storage import get_backend
    try:
        backend = get_backend()
        ws_cfg = backend.get_workspace_config(workspace_id)
        if extra_path:
            ws_cfg["sentinel_extra_policies"] = extra_path
        else:
            ws_cfg.pop("sentinel_extra_policies", None)
        if isinstance(active_global_sets, list):
            ws_cfg["sentinel_active_global_sets"] = active_global_sets
        else:
            ws_cfg.pop("sentinel_active_global_sets", None)
        if isinstance(active_extra_sets, list):
            ws_cfg["sentinel_active_extra_sets"] = active_extra_sets
        else:
            ws_cfg.pop("sentinel_active_extra_sets", None)
        enforce_on_apply = body.get("sentinel_enforce_on_apply")
        if isinstance(enforce_on_apply, bool):
            ws_cfg["sentinel_enforce_on_apply"] = enforce_on_apply
        backend.set_workspace_config(workspace_id, ws_cfg)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500

    return jsonify({"ok": True, "extra_policies": extra_path or None})


@api_bp.route("/workspace/<workspace_id>/sentinel/run", methods=["POST"])
def run_sentinel(workspace_id: str):
    """
    Manually trigger a Sentinel check on the latest plan JSON for this workspace.
    If no cached plan is available, run terraform plan first to obtain one.
    """
    workspace = _get_workspace_or_404(workspace_id)
    if workspace is None:
        return jsonify({"error": "Workspace not found"}), 404

    config = current_app.config["TFG_CONFIG"]
    from app.sentinel_runner import SentinelRunner, get_sentinel_binary, sentinel_available
    from app.storage import get_backend
    from app.env_validator import build_execution_env
    from app.version_manager import get_terraform_binary

    # Check Sentinel binary
    if not sentinel_available(config.sentinel_cli_path):
        return jsonify({
            "ok": False,
            "error": (
                "Sentinel CLI not found. Install it from "
                "https://developer.hashicorp.com/sentinel/downloads "
                "or set sentinel.cli_path in tfg.conf."
            ),
        }), 400

    # Load workspace config (for extra policies + pinned tf version)
    try:
        ws_cfg = get_backend().get_workspace_config(workspace_id)
    except Exception:
        ws_cfg = {}

    extra_path = ws_cfg.get("sentinel_extra_policies") or None
    global_policies = config.sentinel_global_policies or None
    # Active sets: workspace override takes precedence over global setting
    active_global = (
        ws_cfg.get("sentinel_active_global_sets") or config.sentinel_active_policy_sets or None
    )
    active_extra = ws_cfg.get("sentinel_active_extra_sets") or None

    if not global_policies and not extra_path:
        return jsonify({
            "ok": False,
            "error": "No policy sets configured (global or workspace-level).",
        }), 400

    # We need a plan JSON — try to get the most recent stored one
    plan_json = None
    try:
        backend = get_backend()
        executions = backend.list_executions(workspace_id)
        for meta in executions:
            if meta.get("command") == "plan" and meta.get("status") == "completed":
                plan_json = backend.get_plan_json_by_id(meta["id"])
                if plan_json:
                    break
    except Exception:
        pass

    # If no stored plan, run terraform plan now (blocking, short-timeout)
    if plan_json is None:
        pinned_version = ws_cfg.get("terraform_version") or config.default_terraform_version
        tf_binary = get_terraform_binary(pinned_version, config.terraform_versions_folder)
        isolated_env = build_execution_env(workspace["providers"], {})

        from app.terraform_runner import TerraformRunner
        import tempfile
        import os
        tmpdir = tempfile.mkdtemp(prefix="tgm-sentinel-plan-")
        try:
            runner = TerraformRunner(workspace["abs_path"], isolated_env, tf_binary)
            runner.init(lambda _: None)
            plan_binary = os.path.join(tmpdir, "tfplan.binary")
            ok = runner.plan(lambda _: None, plan_binary_path=plan_binary)
            if ok:
                plan_json = runner.show_json(plan_binary, lambda _: None)
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    if plan_json is None:
        return jsonify({
            "ok": False,
            "error": "Could not obtain a plan JSON to evaluate policies against.",
        }), 500

    binary = get_sentinel_binary(config.sentinel_cli_path)
    sentinel = SentinelRunner(
        sentinel_binary=binary,
        global_policies_path=global_policies,
        workspace_extra_policies=extra_path,
    )

    log_lines: list = []
    result = sentinel.check_plan(
        plan_json,
        log_cb=lambda line: log_lines.append(line),
        active_global_sets=active_global if active_global else None,
        active_extra_sets=active_extra if active_extra else None,
    )
    result["log"] = log_lines

    # Persist the result so it survives page reloads
    import datetime
    result["ran_at"] = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        backend = get_backend()
        if hasattr(backend, "set_sentinel_last_result"):
            backend.set_sentinel_last_result(workspace_id, result)
    except Exception:
        pass

    return jsonify({"ok": True, "result": result})


@api_bp.route("/workspace/<workspace_id>/sentinel/last-result", methods=["GET"])
def get_sentinel_last_result(workspace_id: str):
    """Return the persisted result of the last Sentinel run for this workspace."""
    workspace = _get_workspace_or_404(workspace_id)
    if workspace is None:
        return jsonify({"error": "Workspace not found"}), 404
    from app.storage import get_backend
    try:
        backend = get_backend()
        if hasattr(backend, "get_sentinel_last_result"):
            data = backend.get_sentinel_last_result(workspace_id)
            if data:
                return jsonify({"ok": True, "result": data})
    except Exception:
        pass
    return jsonify({"ok": False, "result": None})


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


# -------------------------------------------------------------------------
# Workspace-level individual variables (stored in workspace_config)
# -------------------------------------------------------------------------

@api_bp.route("/workspace/<workspace_id>/tfvars-files", methods=["GET"])
def get_workspace_tfvars_files(workspace_id: str):
    """
    Return all variable sources for the workspace priority view:
      - auto:    terraform.tfvars, *.auto.tfvars (always applied)
      - manual:  other .tfvars files
      - defaults: variable block defaults from .tf files
      - groups:  Variable Groups applied to this workspace (TF_VAR_* priority)
      - ws_vars: per-workspace individual variables (TF_VAR_* priority)
    """
    import os as _os
    from app.variable_groups import list_all_groups
    from app.storage import get_backend as _get_backend

    workspace = _get_workspace_or_404(workspace_id)
    if workspace is None:
        return jsonify({"error": "Workspace not found"}), 404

    ws_path = workspace["abs_path"]

    def _is_auto(fname: str) -> bool:
        return (
            fname in ("terraform.tfvars", "terraform.tfvars.json")
            or fname.endswith(".auto.tfvars")
            or fname.endswith(".auto.tfvars.json")
        )

    try:
        fnames = sorted(
            f for f in _os.listdir(ws_path)
            if f.endswith(".tfvars") or f.endswith(".tfvars.json")
        )
    except OSError:
        fnames = []

    all_entries = _scan_tfvars_values(ws_path)
    entries_by_file: Dict[str, list] = {}
    for e in all_entries:
        entries_by_file.setdefault(e["file"], []).append(
            {"key": e["key"], "value": e["value"]}
        )

    auto_files = []
    manual_files = []
    for fname in fnames:
        item = {"name": fname, "vars": entries_by_file.get(fname, [])}
        if _is_auto(fname):
            auto_files.append(item)
        else:
            manual_files.append(item)

    defaults = _scan_variable_defaults(ws_path)
    default_entries = [
        {"key": k, "value": v} for k, v in sorted(defaults.items())
    ]

    # Variable Groups applied to this workspace (injected as TF_VAR_* — top priority)
    groups_out = []
    try:
        all_groups = list_all_groups()
        applicable = [
            g for g in all_groups
            if g.get("workspace_ids") == ["*"]
            or workspace_id in (g.get("workspace_ids") or [])
        ]
        for g in applicable:
            vars_out = []
            for v in g.get("variables", []):
                key = (v.get("key") or "").strip()
                if not key:
                    continue
                var_type = v.get("type", "terraform")
                if var_type != "terraform":
                    continue   # only TF_VAR_* vars affect variable values
                is_sensitive = v.get("sensitive", False)
                if is_sensitive:
                    display = "***"
                else:
                    display = v.get("value") or ""
                vars_out.append({"key": key, "value": display, "sensitive": is_sensitive})
            if vars_out:
                scope = "global" if g.get("workspace_ids") == ["*"] else "workspace"
                groups_out.append({
                    "name": g.get("name", ""),
                    "scope": scope,
                    "vars": vars_out,
                })
    except Exception:
        pass

    # Per-workspace individual variables (also TF_VAR_* — top priority)
    ws_vars_out = []
    try:
        cfg = _get_backend().get_workspace_config(workspace_id)
        for v in cfg.get("variables", []):
            key = (v.get("key") or "").strip()
            if not key or v.get("type", "terraform") != "terraform":
                continue
            ws_vars_out.append({
                "key": key,
                "value": "***" if v.get("sensitive") else (v.get("value") or ""),
                "sensitive": v.get("sensitive", False),
            })
    except Exception:
        pass

    return jsonify({
        "auto": auto_files,
        "manual": manual_files,
        "defaults": default_entries,
        "groups": groups_out,
        "ws_vars": ws_vars_out,
    })


@api_bp.route("/workspace/<workspace_id>/vars", methods=["GET"])
def get_workspace_vars(workspace_id: str):
    """Return individual variables stored for this workspace (sensitive values masked)."""
    from app.storage import get_backend
    backend = get_backend()
    cfg = backend.get_workspace_config(workspace_id)
    variables = [
        {**v, "value": ""} if v.get("sensitive") else dict(v)
        for v in cfg.get("variables", [])
    ]
    return jsonify({"variables": variables})


@api_bp.route("/workspace/<workspace_id>/vars", methods=["PUT"])
def save_workspace_vars(workspace_id: str):
    """Persist individual variables for this workspace."""
    from flask import session
    from app.storage import get_backend
    from app.crypto import encrypt
    backend = get_backend()
    body = request.get_json(silent=True) or {}
    incoming = body.get("variables", [])
    password = session.get("tgm_enc_key", "")
    cfg = backend.get_workspace_config(workspace_id)
    existing_map = {v["key"]: v for v in cfg.get("variables", [])}
    saved = []
    for v in incoming:
        v = dict(v)
        key = v.get("key", "").strip()
        if not key:
            continue
        v["key"] = key
        if v.get("sensitive"):
            if not password:
                return jsonify(
                    {"ok": False, "error": "Portal password required for sensitive variables."}
                ), 400
            if v.get("value"):
                v["value"] = encrypt(v["value"], password)
            elif key in existing_map and existing_map[key].get("sensitive"):
                v["value"] = existing_map[key]["value"]
            else:
                v["value"] = ""
        saved.append(v)
    cfg["variables"] = saved
    backend.set_workspace_config(workspace_id, cfg)
    return jsonify({"ok": True})


# -------------------------------------------------------------------------
# Variable Groups
# -------------------------------------------------------------------------

@api_bp.route("/variable-groups", methods=["GET"])
def list_variable_groups():
    """List all groups, optionally filtered to those applied to a workspace."""
    from app.variable_groups import list_all_groups, sanitize_for_frontend
    ws_filter = request.args.get("workspace_id")
    groups = list_all_groups()
    if ws_filter:
        groups = [
            g for g in groups
            if g.get("workspace_ids") == ["*"] or ws_filter in (g.get("workspace_ids") or [])
        ]
    return jsonify({"groups": [sanitize_for_frontend(g) for g in groups]})


@api_bp.route("/variable-groups/all", methods=["GET"])
def list_variable_groups_all():
    """List every group (for assign dialog)."""
    from app.variable_groups import list_all_groups, sanitize_for_frontend
    groups = list_all_groups()
    result = {"groups": [sanitize_for_frontend(g) for g in groups]}

    # Warn when a cloud backend is active but has no groups while local does.
    if not groups:
        from app.storage import _resolve_type
        backend_type = _resolve_type()
        if backend_type != "local":
            try:
                from app.storage.local_backend import LocalBackend
                local_count = len(LocalBackend().list_variable_groups())
                if local_count:
                    result["local_only_count"] = local_count
                    result["backend_type"] = backend_type
            except Exception:
                pass

    return jsonify(result)


@api_bp.route("/variable-groups", methods=["POST"])
def create_variable_group():
    from flask import session as _session
    from app.variable_groups import save_group, sanitize_for_frontend
    body = request.get_json(silent=True) or {}
    enc_key = _session.get("tgm_enc_key", "")
    # Validate: sensitive vars require a password
    for var in body.get("variables", []):
        if var.get("sensitive") and not enc_key:
            return jsonify({
                "ok": False,
                "error": "A portal password must be set to use sensitive variables.",
            }), 400
    try:
        group = save_group(body, enc_key)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "group": sanitize_for_frontend(group)}), 201


@api_bp.route("/variable-groups/<group_id>", methods=["GET"])
def get_variable_group(group_id: str):
    from app.variable_groups import get_group, sanitize_for_frontend
    group = get_group(group_id)
    if group is None:
        return jsonify({"error": "Variable group not found"}), 404
    return jsonify(sanitize_for_frontend(group))


@api_bp.route("/variable-groups/<group_id>", methods=["PUT"])
def update_variable_group(group_id: str):
    from flask import session as _session
    from app.variable_groups import get_group, save_group, sanitize_for_frontend
    existing = get_group(group_id)
    if existing is None:
        return jsonify({"error": "Variable group not found"}), 404
    body = request.get_json(silent=True) or {}
    body["id"] = group_id
    enc_key = _session.get("tgm_enc_key", "")
    for var in body.get("variables", []):
        if var.get("sensitive") and not enc_key:
            return jsonify({
                "ok": False,
                "error": "A portal password must be set to use sensitive variables.",
            }), 400
    try:
        group = save_group(body, enc_key, existing_group=existing)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "group": sanitize_for_frontend(group)})


@api_bp.route("/variable-groups/<group_id>", methods=["DELETE"])
def delete_variable_group(group_id: str):
    from app.variable_groups import delete_group
    delete_group(group_id)
    return jsonify({"ok": True})


@api_bp.route("/variable-groups/unsensitize-all", methods=["POST"])
def unsensitize_all_vars():
    """
    Decrypt every sensitive variable using the current session enc_key and
    store the plaintext with sensitive=False.  Must be called while the
    session still holds a valid enc_key (i.e. before removing the password).
    """
    from flask import session as _session
    from app.variable_groups import unsensitize_all_sensitive
    enc_key = _session.get("tgm_enc_key", "")
    if not enc_key:
        return jsonify({"ok": False, "error": "No encryption key in session."}), 400
    converted = unsensitize_all_sensitive(enc_key)
    return jsonify({"ok": True, "converted": converted})


@api_bp.route("/sensitive-vars-summary")
def sensitive_vars_summary():
    """
    Return a flat list of sensitive variable locations for the 'Remove lock'
    confirmation modal.  Each entry: {folder, workspace, group, variable}.
    Groups applied to all workspaces use workspace='(all workspaces)'.
    """
    from app.variable_groups import list_all_groups
    from app.workspace_scanner import WorkspaceScanner

    config = current_app.config["TFG_CONFIG"]
    scanner = WorkspaceScanner(config.repos_root)
    all_workspaces = scanner.get_flat_list()

    # Build a lookup: workspace_id → {folder, name}
    ws_lookup: Dict[str, Dict[str, str]] = {}
    for ws in all_workspaces:
        parts = ws["relative_path"].replace("\\", "/").split("/")
        folder = "/".join(parts[:-1]) if len(parts) > 1 else "."
        ws_lookup[ws["id"]] = {"folder": folder, "name": ws["name"]}

    entries = []
    for group in list_all_groups():
        sensitive_vars = [
            v["key"] for v in group.get("variables", [])
            if v.get("sensitive") and v.get("value")
        ]
        if not sensitive_vars:
            continue
        ws_ids = group.get("workspace_ids") or []
        if ws_ids == ["*"]:
            targets = [{"folder": "(global)", "workspace": "(all workspaces)"}]
        else:
            targets = [
                {
                    "folder": ws_lookup[wid]["folder"] if wid in ws_lookup else wid,
                    "workspace": ws_lookup[wid]["name"] if wid in ws_lookup else wid,
                }
                for wid in ws_ids
            ]
        for target in targets:
            for var_key in sensitive_vars:
                entries.append({
                    "folder": target["folder"],
                    "workspace": target["workspace"],
                    "group": group.get("name", group["id"]),
                    "variable": var_key,
                })
    return jsonify(entries)


# -------------------------------------------------------------------------
# Backend credentials (configure cloud backend via UI)
# -------------------------------------------------------------------------

@api_bp.route("/backend-config", methods=["GET"])
def get_backend_config_api():
    """Return current backend config with sensitive fields masked."""
    from app.backend_config import get_backend_config, mask_sensitive

    config = current_app.config["TFG_CONFIG"]
    bc = get_backend_config(config)
    backend_type = (bc.get("type") or "local").lower().strip()
    # Never expose raw ciphertext — mask sensitive fields for display
    return jsonify(mask_sensitive(bc, backend_type))


@api_bp.route("/backend-config", methods=["POST"])
def save_backend_config_api():
    """
    Save backend credentials (sensitive fields sent as plaintext — we encrypt
    them here before persisting).  Requires portal lock to be active (enc_key
    must be in session) whenever sensitive fields are provided.

    Body:
      { "type": "aws"|"gcp"|"azure"|"local",
        "bucket": "...",
        ... <all fields for that backend type> }
    """
    from flask import session as _session
    from app.backend_config import (
        get_backend_config, save_backend_config, BACKEND_FIELDS, SENSITIVE_FIELDS,
        save_migration_source_config, delete_migration_source_config,
    )

    config = current_app.config["TFG_CONFIG"]
    enc_key = _session.get("tgm_enc_key", "")
    body = request.get_json(silent=True) or {}
    backend_type = (body.get("type") or "local").lower().strip()

    # Validate: sensitive fields require enc_key
    has_sensitive = any(
        (body.get(f) or "").strip()
        for f in SENSITIVE_FIELDS.get(backend_type, [])
    )
    if has_sensitive and not enc_key:
        return jsonify({
            "ok": False,
            "error": "A site password (lock) is required to store encrypted credentials.",
        }), 400

    # Filter to known fields + type
    valid_fields = BACKEND_FIELDS.get(backend_type, [])
    data: Dict[str, Any] = {"type": backend_type}
    for field in valid_fields:
        val = body.get(field)
        if val is not None:
            data[field] = str(val).strip()

    # Preserve existing encrypted values for sensitive fields when no new value provided.
    # Empty string OR the mask placeholder '••••••••' both mean "keep existing".
    existing = get_backend_config(config)
    for field in SENSITIVE_FIELDS.get(backend_type, []):
        new_val = data.get(field, "").strip()
        if not new_val or new_val == "\u2022" * 8:
            # Keep existing encrypted value if present
            if existing.get(field):
                data[field] = existing[field]
            else:
                data.pop(field, None)
        else:
            # Encrypt the new plaintext value
            from app.crypto import encrypt as _encrypt
            data[field] = _encrypt(new_val, enc_key)

    # If the backend type is changing, stash the current (old) config so the
    # migration endpoint can still reach the source credentials after the new
    # config has been saved over them.
    old_type = (existing.get("type") or "local").lower().strip()
    if old_type != backend_type and old_type != "local" and existing:
        save_migration_source_config(config, existing)
    elif old_type == backend_type:
        # Type unchanged — any previous stash is no longer relevant.
        delete_migration_source_config(config)

    save_backend_config(config, data)
    return jsonify({"ok": True})


@api_bp.route("/backend-config/test", methods=["POST"])
def test_backend_config_api():
    """
    Test connectivity to a backend using the provided credentials.
    Accepts either a full set of credentials (for a *new* config being saved)
    or an empty body (to test the currently saved config).
    """
    from flask import session as _session
    from app.backend_config import (
        get_backend_config, decrypt_fields, test_connectivity, SENSITIVE_FIELDS
    )

    config = current_app.config["TFG_CONFIG"]
    enc_key = _session.get("tgm_enc_key", "")
    body = request.get_json(silent=True) or {}

    if body:
        # Caller provided credentials directly (UI test before saving)
        backend_type = (body.get("type") or "local").lower().strip()
        creds = dict(body)
        creds["type"] = backend_type
        # Decrypt any encrypted fields that were passed through
        # (UI sends plaintext for new values, ciphertext token placeholder for unchanged,
        # or empty string when the field was never pre-filled in the form)
        for field in SENSITIVE_FIELDS.get(backend_type, []):
            val = creds.get(field, "")
            if not val or val == "••••••••":
                # Empty or placeholder → use the saved encrypted value.
                saved = get_backend_config(config)
                if saved.get(field) and enc_key:
                    from app.crypto import decrypt as _decrypt
                    try:
                        creds[field] = _decrypt(saved[field], enc_key)
                    except ValueError:
                        return jsonify(
                            {"ok": False,
                             "error": "Could not decrypt saved credentials."}
                        ), 400
                else:
                    creds.pop(field, None)
    else:
        # Test currently saved config
        saved = get_backend_config(config)
        if not saved:
            return jsonify({"ok": False, "error": "No backend config saved yet."}), 400
        backend_type = (saved.get("type") or "local").lower().strip()
        if backend_type == "local":
            return jsonify({"ok": True})  # local always works
        if not enc_key:
            return jsonify(
                {"ok": False, "error": "Portal must be unlocked to test credentials."}
            ), 400
        try:
            creds = decrypt_fields(saved, backend_type, enc_key)
        except ValueError:
            return jsonify(
                {"ok": False, "error": "Stored credentials could not be decrypted."}
            ), 400

    result = test_connectivity(backend_type, creds)
    return jsonify(result)


@api_bp.route("/backend-config/diff", methods=["GET"])
def diff_backend_api():
    """
    Compare the local backend vs the currently active cloud backend.
    Returns counts and lists of objects present in local but absent in cloud,
    and vice-versa, so the user can decide whether to migrate.

    Response:
      {
        "ok": true,
        "source": "local",
        "dest": "aws",
        "source_counts": { "variable_groups": 2, "executions": 5, "notification_channels": 0 },
        "dest_counts":   { "variable_groups": 0, "executions": 3, "notification_channels": 0 },
        "only_in_source": { "variable_groups": ["example", "prod"], "executions": 2 },
        "only_in_dest":   { "executions": 1 }
      }
    """
    from flask import session as _session
    from app.backend_config import get_backend_config, decrypt_fields

    config = current_app.config["TFG_CONFIG"]
    enc_key = _session.get("tgm_enc_key", "")

    saved = get_backend_config(config)
    dest_type = (saved.get("type") or "local").lower().strip()

    if dest_type == "local":
        return jsonify(
            {"ok": False, "error": "Active backend is already local — nothing to compare."}
        ), 400

    # Build local backend instance
    from app.storage.local_backend import LocalBackend
    local = LocalBackend()

    # Build cloud backend instance (needs decrypted creds)
    if not enc_key:
        return jsonify(
            {"ok": False, "error": "Portal must be unlocked to read cloud credentials."}
        ), 400
    try:
        creds = decrypt_fields(saved, dest_type, enc_key)
    except ValueError:
        return jsonify({"ok": False, "error": "Could not decrypt cloud credentials."}), 400

    try:
        from app.backend_config import _build_backend_from_creds
        cloud = _build_backend_from_creds(dest_type, creds)
    except Exception as exc:
        return jsonify(
            {"ok": False, "error": f"Could not connect to cloud backend: {exc}"}
        ), 400

    # --- collect data summaries ---
    def _safe(fn):
        try:
            return fn()
        except Exception:
            return []

    local_vg = _safe(local.list_variable_groups)
    cloud_vg = _safe(cloud.list_variable_groups)
    local_nc = _safe(local.list_notification_channels)
    cloud_nc = _safe(cloud.list_notification_channels)
    local_ex = _safe(lambda: local.list_all_executions())
    cloud_ex = _safe(lambda: cloud.list_all_executions())

    # Identify items only in source (by name / id)
    local_vg_names = {g.get("name", g.get("id", "?")) for g in local_vg}
    cloud_vg_names = {g.get("name", g.get("id", "?")) for g in cloud_vg}
    local_nc_names = {c.get("name", c.get("id", "?")) for c in local_nc}
    cloud_nc_names = {c.get("name", c.get("id", "?")) for c in cloud_nc}
    local_ex_ids = {e.get("id", e.get("timestamp", "?")) for e in local_ex}
    cloud_ex_ids = {e.get("id", e.get("timestamp", "?")) for e in cloud_ex}

    type_labels = {"aws": "AWS S3", "gcp": "GCP Storage", "azure": "Azure Blob"}

    return jsonify({
        "ok": True,
        "source": "local",
        "dest": dest_type,
        "source_label": "Local FS",
        "dest_label": type_labels.get(dest_type, dest_type.upper()),
        "source_counts": {
            "variable_groups": len(local_vg),
            "notification_channels": len(local_nc),
            "executions": len(local_ex),
        },
        "dest_counts": {
            "variable_groups": len(cloud_vg),
            "notification_channels": len(cloud_nc),
            "executions": len(cloud_ex),
        },
        "only_in_source": {
            "variable_groups": sorted(local_vg_names - cloud_vg_names),
            "notification_channels": sorted(local_nc_names - cloud_nc_names),
            "executions_count": len(local_ex_ids - cloud_ex_ids),
        },
        "only_in_dest": {
            "variable_groups": sorted(cloud_vg_names - local_vg_names),
            "notification_channels": sorted(cloud_nc_names - local_nc_names),
            "executions_count": len(cloud_ex_ids - local_ex_ids),
        },
    })


@api_bp.route("/backend-config/migrate", methods=["POST"])
def migrate_backend_api():
    """
    Migrate data from the current/old backend to the newly configured backend.
    Body:
      {
        "source_type": "local"|"aws"|...,   # optional, defaults to current backend
        "dest_type": "aws"|"gcp"|...,       # required
        "dest_creds": { ... }              # plaintext credentials for destination
      }
    """
    from flask import session as _session
    from app.backend_config import (
        get_backend_config, decrypt_fields, migrate_backend, SENSITIVE_FIELDS,
        get_migration_source_config,
    )

    config = current_app.config["TFG_CONFIG"]
    enc_key = _session.get("tgm_enc_key", "")
    body = request.get_json(silent=True) or {}

    dest_type = (body.get("dest_type") or "").lower().strip()
    if not dest_type:
        return jsonify({"ok": False, "error": "dest_type is required"}), 400

    # Resolve destination credentials
    dest_creds = dict(body.get("dest_creds") or {})
    dest_creds["type"] = dest_type

    # When no dest_creds supplied, use the saved (encrypted) config and decrypt it
    if not body.get("dest_creds"):
        saved = get_backend_config(config)
        if not enc_key:
            return jsonify(
                {"ok": False, "error": "Portal must be unlocked to read cloud credentials."}
            ), 400
        try:
            dest_creds = decrypt_fields(saved, dest_type, enc_key)
        except ValueError:
            return jsonify(
                {"ok": False, "error": "Could not decrypt destination credentials."}
            ), 400
        dest_creds["type"] = dest_type

    # Decrypt any "keep existing" placeholder values for dest (when dest_creds was provided)
    else:
        for field in SENSITIVE_FIELDS.get(dest_type, []):
            val = dest_creds.get(field, "")
            # Treat empty string the same as the mask placeholder: the UI did not
            # provide a new value (sensitive fields are never pre-filled in the form),
            # so always fall back to the saved encrypted value.
            if not val or val == "••••••••":
                saved = get_backend_config(config)
                if saved.get(field) and enc_key:
                    from app.crypto import decrypt as _decrypt
                    try:
                        dest_creds[field] = _decrypt(saved[field], enc_key)
                    except ValueError:
                        return jsonify(
                            {"ok": False,
                             "error": "Could not decrypt destination credentials."}
                        ), 400
                else:
                    dest_creds.pop(field, None)

    # Resolve source
    source_type_override = (body.get("source_type") or "").lower().strip()
    if source_type_override:
        source_type = source_type_override
        if source_type == "local":
            # Local backend needs no credentials
            source_creds_raw = {}
        elif body.get("source_creds"):
            source_creds_raw = dict(body["source_creds"])
        else:
            # Prefer the stashed migration-source config (populated by
            # save_backend_config_api when the type changed).  Fall back to
            # the current saved config only when no stash exists.
            stash = get_migration_source_config(config)
            if stash and (stash.get("type") or "local").lower() == source_type:
                source_creds_raw = stash
            else:
                source_creds_raw = get_backend_config(config)
    else:
        source_creds_raw = get_backend_config(config)
        source_type = (source_creds_raw.get("type") or "local").lower().strip()

    if source_type not in ("local",) and enc_key:
        try:
            source_creds = decrypt_fields(source_creds_raw, source_type, enc_key)
        except ValueError:
            return jsonify({"ok": False, "error": "Could not decrypt source credentials."}), 400
    else:
        source_creds = dict(source_creds_raw)

    # Run migration synchronously (short operation for typical TFG data sizes)
    result = migrate_backend(source_type, source_creds, dest_type, dest_creds)
    return jsonify(result)


# -------------------------------------------------------------------------
# GitHub module token check
# -------------------------------------------------------------------------

@api_bp.route("/workspace/<workspace_id>/github-token-check")
def github_token_check(workspace_id: str):
    """
    Run (and cache) the GitHub-module / GITHUB_TOKEN check for one workspace.
    The result is also stored in the in-memory cache so the dashboard reflects
    it immediately without waiting for the next full background scan.
    """
    workspace = _get_workspace_or_404(workspace_id)
    if workspace is None:
        return jsonify({"error": "Workspace not found"}), 404

    from app.github_module_checker import check_workspace as _check_ws
    result = _check_ws(
        workspace_id,
        workspace["abs_path"],
        workspace["name"],
        workspace["relative_path"],
    )
    return jsonify(result)


@api_bp.route("/github-token-warnings")
def github_token_warnings_api():
    """
    Return all workspaces (from the in-memory cache) that use GitHub-sourced
    modules but have no GITHUB_TOKEN variable configured.
    """
    from app.github_module_checker import get_all_warnings
    return jsonify({"warnings": get_all_warnings()})


@api_bp.route("/backend-config/delete-source", methods=["POST"])
def delete_source_backend_api():
    """
    Delete all data from the old/source backend after a verified migration.
    Body:
      { "source_type": "...", "source_creds": { ... } }
    """
    from flask import session as _session
    from app.backend_config import (
        get_backend_config, decrypt_fields, delete_backend_data,
        get_migration_source_config, delete_migration_source_config,
    )

    config = current_app.config["TFG_CONFIG"]
    enc_key = _session.get("tgm_enc_key", "")
    body = request.get_json(silent=True) or {}

    source_type = (body.get("source_type") or "local").lower().strip()
    if source_type == "local":
        source_creds_raw = {}
    else:
        # Prefer the stashed migration-source config so we always delete from
        # the correct (old) backend even after the new config has been saved.
        stash = get_migration_source_config(config)
        if stash and (stash.get("type") or "local").lower() == source_type:
            source_creds_raw = stash
        else:
            source_creds_raw = dict(body.get("source_creds") or get_backend_config(config) or {})

    if source_type not in ("local",) and enc_key:
        try:
            source_creds = decrypt_fields(source_creds_raw, source_type, enc_key)
        except ValueError:
            return jsonify({"ok": False, "error": "Could not decrypt source credentials."}), 400
    else:
        source_creds = source_creds_raw

    result = delete_backend_data(source_type, source_creds)
    if result.get("ok"):
        # Migration is complete — remove the stash.
        delete_migration_source_config(config)
    return jsonify(result)


@api_bp.route("/dashboard/stats", methods=["GET"])
def dashboard_stats_api():
    """
    Return execution statistics for the dashboard using the lightweight
    workspace last-state cache (O(workspaces), not O(all historical executions)).

    The cache is seeded from storage on startup and updated every time an
    execution completes, so this endpoint requires no storage I/O at all.
    """
    from app.workspace_state import get_all as _get_all_ws_states

    eq = current_app.config["EXECUTION_QUEUE"]

    # Start from the persisted last-state cache
    ws_latest: dict = _get_all_ws_states()

    # Overlay any in-memory executions (running / queued / just-finished)
    # so the dashboard reflects live state immediately without waiting for
    # the cache update that happens in the worker finally-block.
    for ex in eq.list_all():
        d = ex.to_dict()
        d["workspace_path"] = ex.workspace_path
        wid = ex.workspace_id
        existing = ws_latest.get(wid)
        if not existing or d.get("timestamp", "") >= existing.get("timestamp", ""):
            ws_latest[wid] = d

    total_plans = sum(1 for e in ws_latest.values() if e.get("command") == "plan")
    total_applies = sum(1 for e in ws_latest.values() if e.get("command") == "apply")

    errored_workspaces = []
    for wid, e in ws_latest.items():
        if e.get("status") != "failed":
            continue
        wp = e.get("workspace_path", "") or wid
        errored_workspaces.append({
            "workspace_id": wid,
            "workspace_path": wp,
            "workspace_name": wp.rstrip("/").split("/")[-1] or wid,
            "execution_id": e.get("id", ""),
            "command": e.get("command", "plan"),
            "timestamp": e.get("timestamp", ""),
        })
    errored_workspaces.sort(key=lambda x: x["timestamp"], reverse=True)

    return jsonify({
        "total_plans": total_plans,
        "total_applies": total_applies,
        "total_errored": len(errored_workspaces),
        "errored_workspaces": errored_workspaces,
        "ws_latest": ws_latest,
    })
