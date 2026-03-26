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

    eq = current_app.config["EXECUTION_QUEUE"]

    # ---- Gather all executions (in-memory + storage), dedup by id ----
    all_execs: dict = {}
    for ex in eq.list_all():
        d = ex.to_dict()
        d["workspace_path"] = ex.workspace_path  # to_dict() omits this
        all_execs[ex.id] = d

    try:
        from app.storage import get_backend
        backend = get_backend()
        for meta in backend.list_all_executions():
            eid = meta.get("id")
            if eid and eid not in all_execs:
                all_execs[eid] = meta
    except Exception:
        backend = None
    else:
        backend = get_backend()

    # ---- Stats ----
    total_plans = sum(1 for e in all_execs.values() if e.get("command") == "plan")
    total_applies = sum(1 for e in all_execs.values() if e.get("command") == "apply")
    total_errored = sum(1 for e in all_execs.values() if e.get("status") == "failed")

    # ---- Errored workspaces: workspace whose LATEST run is failed ----
    ws_latest: dict = {}
    for e in all_execs.values():
        wid = e.get("workspace_id")
        if not wid:
            continue
        if wid not in ws_latest or e.get("timestamp", "") > ws_latest[wid].get("timestamp", ""):
            ws_latest[wid] = e

    errored_workspaces = []
    for wid, e in ws_latest.items():
        if e.get("status") != "failed":
            continue
        error_snippet = ""
        if backend:
            try:
                logs = backend.get_logs(wid, e.get("timestamp", ""), e.get("command", "plan"))
                if logs:
                    lines = [ln for ln in logs.strip().splitlines() if ln.strip()]
                    error_snippet = "\n".join(lines[-5:])
            except Exception:
                pass
        wp = e.get("workspace_path", "") or wid
        errored_workspaces.append({
            "workspace_id": wid,
            "workspace_path": wp,
            "workspace_name": wp.rstrip("/").split("/")[-1] or wid,
            "execution_id": e.get("id", ""),
            "command": e.get("command", "plan"),
            "timestamp": e.get("timestamp", ""),
            "error_snippet": error_snippet,
        })
    errored_workspaces.sort(key=lambda x: x["timestamp"], reverse=True)

    return render_template(
        "dashboard.html",
        total_workspaces=total,
        repos_root=config.repos_root,
        total_plans=total_plans,
        total_applies=total_applies,
        total_errored=total_errored,
        errored_workspaces=errored_workspaces,
    )


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
