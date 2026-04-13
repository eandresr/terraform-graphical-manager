"""
Workflow Routes — REST API for workspace workflow CRUD + test dispatch.

Sensitive config fields (token, api_token) are stored encrypted at rest
using the same Fernet / Vault pattern as notification channels.
"""
from flask import Blueprint, jsonify, request, session

import app.workflow_runner as wr

workflow_bp = Blueprint("workflows", __name__)


def _enc_key() -> str:
    return session.get("tgm_enc_key") or ""


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

@workflow_bp.route("/workflows", methods=["GET"])
def list_workflows():
    workspace_id = request.args.get("workspace_id", "")
    if not workspace_id:
        return jsonify({"error": "workspace_id is required"}), 400
    workflows = wr.list_workflows(workspace_id)
    return jsonify([wr.mask_workflow_secrets(w) for w in workflows])


@workflow_bp.route("/workflows", methods=["POST"])
def create_workflow():
    data = request.get_json(silent=True) or {}
    workspace_id = data.get("workspace_id") or ""
    if not workspace_id:
        return jsonify({"error": "workspace_id is required"}), 400
    if not data.get("name"):
        return jsonify({"error": "name is required"}), 400

    password = _enc_key()
    if password:
        data = wr.encrypt_workflow_secrets(data, password)

    saved = wr.save_workflow(workspace_id, data)
    return jsonify(wr.mask_workflow_secrets(saved)), 201


@workflow_bp.route("/workflows/<workflow_id>", methods=["GET"])
def get_workflow(workflow_id: str):
    workspace_id = request.args.get("workspace_id", "")
    if not workspace_id:
        return jsonify({"error": "workspace_id is required"}), 400
    wf = wr.get_workflow(workspace_id, workflow_id)
    if wf is None:
        return jsonify({"error": "Workflow not found"}), 404
    return jsonify(wr.mask_workflow_secrets(wf))


@workflow_bp.route("/workflows/<workflow_id>", methods=["PUT"])
def update_workflow(workflow_id: str):
    data = request.get_json(silent=True) or {}
    workspace_id = data.get("workspace_id") or request.args.get("workspace_id", "")
    if not workspace_id:
        return jsonify({"error": "workspace_id is required"}), 400
    data["id"] = workflow_id
    data["workspace_id"] = workspace_id

    password = _enc_key()
    if password:
        data = wr.encrypt_workflow_secrets(data, password)

    saved = wr.save_workflow(workspace_id, data)
    return jsonify(wr.mask_workflow_secrets(saved))


@workflow_bp.route("/workflows/<workflow_id>", methods=["DELETE"])
def delete_workflow(workflow_id: str):
    workspace_id = request.args.get("workspace_id", "")
    if not workspace_id:
        return jsonify({"error": "workspace_id is required"}), 400
    wr.delete_workflow(workspace_id, workflow_id)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Test dispatch  — execute a single workflow with a synthetic run context
# ---------------------------------------------------------------------------

@workflow_bp.route("/workflows/<workflow_id>/test", methods=["POST"])
def test_workflow(workflow_id: str):
    """
    Execute a workflow immediately with a synthetic run context built from
    an optional ``run`` object in the request body.  Secrets are decrypted
    using the session enc_key.
    """
    body = request.get_json(silent=True) or {}
    workspace_id = body.get("workspace_id") or request.args.get("workspace_id", "")
    if not workspace_id:
        return jsonify({"error": "workspace_id is required"}), 400

    wf = wr.get_workflow(workspace_id, workflow_id)
    if wf is None:
        # Allow testing an unsaved payload passed inline
        wf = body.get("workflow")
        if not wf:
            return jsonify({"error": "Workflow not found"}), 404

    password = _enc_key()
    if password:
        wf = wr.decrypt_workflow_secrets(wf, password)

    # Build a synthetic context
    synthetic_exec = {
        "id": "test-run",
        "workspace_id": workspace_id,
        "command": body.get("command", "plan"),
        "status": body.get("status", "completed"),
        "duration_seconds": 0,
        "timestamp": "",
        "terraform_version": "",
    }
    from app.workspace_scanner import WorkspaceScanner
    from flask import current_app
    cfg = current_app.config.get("TFG_CONFIG")
    workspace_name = workspace_id
    try:
        scanner = WorkspaceScanner(cfg.repos_root)
        for ws in scanner.get_flat_list():
            if ws.get("id") == workspace_id:
                workspace_name = ws.get("name") or workspace_id
                break
    except Exception:
        pass

    context = wr.build_run_context(synthetic_exec, workspace_name, enc_key=password)

    wf_type = (wf.get("type") or "").lower()
    plugin_cls = wr.WORKFLOW_REGISTRY.get(wf_type)
    if plugin_cls is None:
        return jsonify({"error": f"Unknown workflow type: '{wf_type}'"}), 400

    cfg_copy = dict(wf.get("config") or {})
    cfg_copy["_workflow_id"] = wf.get("id", "test")
    cfg_copy["_workflow_name"] = wf.get("name", "Test")

    plugin = plugin_cls()
    try:
        result = plugin.execute(cfg_copy, context, enc_key=password)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    status_code = 200 if result.status == "success" else 502
    return jsonify(result.to_dict()), status_code


# ---------------------------------------------------------------------------
# Plugin metadata  — so the UI can dynamically build config forms
# ---------------------------------------------------------------------------

@workflow_bp.route("/workflows/plugins", methods=["GET"])
def list_plugins():
    return jsonify(wr.plugin_info())
