"""
Notification Routes — REST API for notification channel CRUD + test.

Sensitive config fields (webhook_url, token, client_secret, …) are
stored encrypted at rest.  The routes handle encrypt-on-save, mask-on-read,
and decrypt-before-test transparently using the portal password stored in the
Flask session.
"""
from flask import Blueprint, jsonify, request, session

import app.notification_manager as nm

notification_bp = Blueprint("notifications", __name__)


def _enc_key() -> str:
    return session.get("tgm_enc_key") or ""


# ---------------------------------------------------------------------------
# Global channel CRUD
# ---------------------------------------------------------------------------

@notification_bp.route("/notification-channels/all", methods=["GET"])
def list_all_channels():
    channels = nm.list_all_channels()
    return jsonify([nm.mask_channel_secrets(c) for c in channels])


@notification_bp.route("/notification-channels", methods=["GET"])
def list_channels_for_workspace():
    workspace_id = request.args.get("workspace_id", "")
    if workspace_id:
        channels = nm.get_channels_for_workspace(workspace_id)
    else:
        channels = nm.list_all_channels()
    return jsonify([nm.mask_channel_secrets(c) for c in channels])


@notification_bp.route("/notification-channels", methods=["POST"])
def create_channel():
    data = request.get_json(silent=True) or {}
    if not data.get("name"):
        return jsonify({"error": "name is required"}), 400
    password = _enc_key()
    if password:
        data = nm.encrypt_channel_secrets(data, password)
    channel = nm.save_channel(data)
    return jsonify(nm.mask_channel_secrets(channel)), 201


@notification_bp.route("/notification-channels/<channel_id>", methods=["GET"])
def get_channel(channel_id: str):
    ch = nm.get_channel(channel_id)
    if ch is None:
        return jsonify({"error": "Channel not found"}), 404
    return jsonify(nm.mask_channel_secrets(ch))


@notification_bp.route(
    "/notification-channels/<channel_id>", methods=["PUT"]
)
def update_channel(channel_id: str):
    data = request.get_json(silent=True) or {}
    data["id"] = channel_id
    password = _enc_key()
    if password:
        data = nm.encrypt_channel_secrets(data, password)
    channel = nm.save_channel(data)
    return jsonify(nm.mask_channel_secrets(channel))


@notification_bp.route(
    "/notification-channels/<channel_id>", methods=["DELETE"]
)
def delete_channel(channel_id: str):
    nm.delete_channel(channel_id)
    return jsonify({"ok": True})


@notification_bp.route(
    "/notification-channels/<channel_id>/test", methods=["POST"]
)
def test_channel(channel_id: str):
    ch = nm.get_channel(channel_id)
    if ch is None:
        # Allow testing unsaved payload passed in body
        ch = request.get_json(silent=True) or {}
    password = _enc_key()
    if password:
        ch = nm.decrypt_channel_secrets(ch, password)
    result = nm.test_channel(ch)
    status_code = 200 if result.get("ok") else 502
    return jsonify(result), status_code


# ---------------------------------------------------------------------------
# Workspace-scoped channel assignment
# ---------------------------------------------------------------------------

@notification_bp.route(
    "/workspace/<workspace_id>/notification-channels", methods=["GET"]
)
def workspace_channels(workspace_id: str):
    return jsonify(nm.get_channels_for_workspace(workspace_id))


@notification_bp.route(
    "/workspace/<workspace_id>/notification-channels/assign", methods=["POST"]
)
def assign_channel(workspace_id: str):
    """Add workspace_id to an existing channel's workspace_ids list."""
    body = request.get_json(silent=True) or {}
    channel_id = body.get("channel_id", "")
    ch = nm.get_channel(channel_id)
    if ch is None:
        return jsonify({"error": "Channel not found"}), 404
    ids = ch.get("workspace_ids") or []
    if workspace_id not in ids and ids != ["*"]:
        ids.append(workspace_id)
        ch["workspace_ids"] = ids
        nm.save_channel(ch)
    return jsonify(ch)


@notification_bp.route(
    "/workspace/<workspace_id>/notification-channels/unassign", methods=["POST"]
)
def unassign_channel(workspace_id: str):
    """Remove workspace_id from an existing channel's workspace_ids list."""
    body = request.get_json(silent=True) or {}
    channel_id = body.get("channel_id", "")
    ch = nm.get_channel(channel_id)
    if ch is None:
        return jsonify({"error": "Channel not found"}), 404
    ids = ch.get("workspace_ids") or []
    if workspace_id in ids:
        ids.remove(workspace_id)
        ch["workspace_ids"] = ids
        nm.save_channel(ch)
    return jsonify(ch)
