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
    execution.run_params = _collect_run_params(
        workspace_path=workspace["abs_path"],
        var_entries=_var_entries,
        user_env=user_env,
    )

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
    return jsonify({"groups": [sanitize_for_frontend(g) for g in list_all_groups()]})


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
