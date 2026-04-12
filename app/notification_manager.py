"""
Notification Manager — manages notification channels and dispatches alerts
after Terraform execution lifecycle events.

Supported backends
------------------
  slack          — Incoming Webhook (webhook_url) OR Slack API with bot token
  teams          — Microsoft Teams via Incoming Webhook (webhook_url, secret)
                   OR Microsoft Graph API (tenant_id, client_id, client_secret)
  email          — SMTP (STARTTLS or SSL, optional auth)
  pagerduty      — PagerDuty Events API v2 (routing_key, severity)
  alertmanager   — Prometheus Alertmanager /api/v2/alerts
                   auth: none (open URL) | token (Bearer) | basic (user/password)

Sensitive fields per type
-------------------------
  slack/webhook          : webhook_url
  slack/token            : token
  teams/webhook          : webhook_url
  teams/graph            : client_secret
  email                  : smtp_password
  pagerduty              : routing_key
  alertmanager/token     : token
  alertmanager/basic     : password

These fields are stored Fernet-encrypted (same algorithm as workspace sensitive
variables) when a portal password is active.  Stored values are prefixed with
"enc:" to distinguish ciphertext from plaintext.

Channel scope
-------------
  global           — workspace_ids == ["*"]  →  all workspaces
  workspace        — workspace_ids == ["id1", "id2"]  →  explicit workspaces
  (draft)          — workspace_ids == []  →  unassigned

Notification triggers (stored as list on each channel)
-------------------------------------------------------
  on_success         run completed
  on_failure         run failed
  on_sentinel_fail   Sentinel policy check failed

Template variables (available in prefix_template / body_template)
-----------------------------------------------------------------
  {workspace_name}, {workspace_id}, {command}, {status},
  {duration}, {timestamp}, {terraform_version},
  {sentinel_status}, {sentinel_summary}
"""
import email.mime.text
import smtplib
import ssl
import sys
import uuid
from typing import Any, Dict, List, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

import json as _json

# ---------------------------------------------------------------------------
# Default templates
# ---------------------------------------------------------------------------

DEFAULT_PREFIX = "[TGM] [{workspace_name}]"
DEFAULT_BODY = (
    "*{command}* {status} on workspace *{workspace_name}*\n"
    "Duration: {duration}s · Version: {terraform_version}\n"
    "Sentinel: {sentinel_status}"
)

# Fields that must be encrypted at rest, keyed by (type, method/subtype).
# "*" means the map applies regardless of method.
_SENSITIVE: Dict[str, List[str]] = {
    "slack:webhook":      ["webhook_url"],
    "slack:token":        ["token"],
    "teams:webhook":      ["webhook_url"],
    "teams:graph":        ["client_secret"],
    "email:*":            ["smtp_password"],
    "pagerduty:*":        ["routing_key"],
    "alertmanager:token": ["token"],
    "alertmanager:basic": ["password"],
}

_ENC_PREFIX = "enc:"


def _sensitive_fields_for(ch_type: str, method: str) -> List[str]:
    """Return list of config field names that should be encrypted."""
    key_specific = f"{ch_type}:{method}"
    key_wildcard = f"{ch_type}:*"
    return (
        _SENSITIVE.get(key_specific)
        or _SENSITIVE.get(key_wildcard)
        or []
    )


# ---------------------------------------------------------------------------
# Encryption helpers
# ---------------------------------------------------------------------------

def _get_enc_key() -> Optional[str]:
    try:
        from flask import session
        return session.get("tgm_enc_key") or None
    except RuntimeError:
        return None


def _encrypt_field(value: str, password: str) -> str:
    from app.crypto import encrypt
    return _ENC_PREFIX + encrypt(value, password)


def _decrypt_field(value: str, password: str) -> str:
    if value.startswith(_ENC_PREFIX):
        from app.crypto import decrypt
        return decrypt(value[len(_ENC_PREFIX):], password)
    return value


def encrypt_channel_secrets(channel: Dict[str, Any], password: str) -> Dict[str, Any]:
    """
    Return a copy of *channel* with sensitive config fields encrypted.
    If Vault is enabled the value is written to Vault and a ``vault:<path>``
    reference is stored instead of a Fernet blob.  Empty values are preserved.
    """
    channel = _json.loads(_json.dumps(channel))  # deep copy
    cfg = channel.get("config") or {}
    ch_type = (channel.get("type") or "").lower()
    fields = _sensitive_fields_for(ch_type, _resolve_method(ch_type, cfg))
    channel_id = channel.get("id") or "unknown"

    # Check Vault availability once
    _vault_cfg = None
    try:
        from flask import current_app
        _vault_cfg = current_app.config.get("TFG_CONFIG")
    except RuntimeError:
        pass
    vault_enabled = _vault_cfg and getattr(_vault_cfg, "vault_enabled", False)

    for field in fields:
        raw = (cfg.get(field) or "").strip()
        if not raw:
            # Preserve existing blob/ref
            continue
        if raw.startswith(_ENC_PREFIX) or raw.startswith("vault:"):
            # Already encoded – keep as-is
            continue
        if vault_enabled and password:
            try:
                from app import vault_manager as _vm
                path = _vm.notification_channel_path(
                    _vault_cfg.vault_path_prefix, channel_id, field
                )
                cfg[field] = _vm.store_secret(_vault_cfg, password, path, raw)
            except Exception:
                cfg[field] = _encrypt_field(raw, password)
        else:
            cfg[field] = _encrypt_field(raw, password)

    channel["config"] = cfg
    return channel


def decrypt_channel_secrets(channel: Dict[str, Any], password: str) -> Dict[str, Any]:
    """Return a copy of *channel* with sensitive config fields decrypted.
    Vault references (``vault:<path>``) are resolved via vault_manager."""
    channel = _json.loads(_json.dumps(channel))
    cfg = channel.get("config") or {}
    ch_type = (channel.get("type") or "").lower()
    fields = _sensitive_fields_for(ch_type, _resolve_method(ch_type, cfg))

    _vault_cfg = None
    try:
        from flask import current_app
        _vault_cfg = current_app.config.get("TFG_CONFIG")
    except RuntimeError:
        pass

    for field in fields:
        val = cfg.get(field) or ""
        if val.startswith("vault:"):
            if _vault_cfg and password:
                try:
                    from app import vault_manager as _vm
                    cfg[field] = _vm.resolve_secret(_vault_cfg, password, val)
                except Exception:
                    cfg[field] = ""
            else:
                cfg[field] = ""
        elif val.startswith(_ENC_PREFIX):
            try:
                cfg[field] = _decrypt_field(val, password)
            except Exception:
                cfg[field] = ""

    channel["config"] = cfg
    return channel


def mask_channel_secrets(channel: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy with sensitive blob fields replaced by '***'."""
    channel = _json.loads(_json.dumps(channel))
    cfg = channel.get("config") or {}
    ch_type = (channel.get("type") or "").lower()
    fields = _sensitive_fields_for(ch_type, _resolve_method(ch_type, cfg))

    for field in fields:
        val = cfg.get(field) or ""
        if val.startswith(_ENC_PREFIX) or val:
            cfg[field] = "***" if val else ""

    channel["config"] = cfg
    return channel


def _default_method(ch_type: str) -> str:
    if ch_type in ("teams", "slack"):
        return "webhook"
    if ch_type == "alertmanager":
        return "none"
    return "*"


def _resolve_method(ch_type: str, cfg: Dict[str, Any]) -> str:
    """
    Return the method/subtype key used for sensitive-field lookup.
    Alertmanager uses ``auth_type`` instead of ``method``.
    """
    if ch_type == "alertmanager":
        return (cfg.get("auth_type") or "none").lower()
    return (cfg.get("method") or _default_method(ch_type)).lower()


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------

def _backend():
    from app.storage import get_backend
    return get_backend()


def list_all_channels() -> List[Dict[str, Any]]:
    try:
        return _backend().list_notification_channels()
    except AttributeError:
        return []


def get_channel(channel_id: str) -> Optional[Dict[str, Any]]:
    try:
        return _backend().get_notification_channel(channel_id)
    except AttributeError:
        return None


def save_channel(channel_data: Dict[str, Any]) -> Dict[str, Any]:
    if not channel_data.get("id"):
        channel_data = {**channel_data, "id": str(uuid.uuid4())}
    try:
        _backend().save_notification_channel(channel_data["id"], channel_data)
    except AttributeError:
        pass
    return channel_data


def delete_channel(channel_id: str) -> None:
    try:
        _backend().delete_notification_channel(channel_id)
    except AttributeError:
        pass


def get_channels_for_workspace(workspace_id: str) -> List[Dict[str, Any]]:
    """Return all channels applicable to *workspace_id* (global + scoped)."""
    all_ch = list_all_channels()
    result = []
    for ch in all_ch:
        ids = ch.get("workspace_ids") or []
        if ids == ["*"] or workspace_id in ids:
            result.append(ch)
    return result


# ---------------------------------------------------------------------------
# Re-encryption (called when portal password changes)
# ---------------------------------------------------------------------------

def reencrypt_all_sensitive(old_password: str, new_password: str) -> int:
    """
    Re-encrypt every sensitive notification channel secret with *new_password*.
    Fields that are already Vault references are left unchanged (they do not
    depend on the portal password).
    Returns the count of channels updated.
    """
    from app.crypto import decrypt, encrypt
    channels = list_all_channels()
    count = 0
    for ch in channels:
        cfg = ch.get("config") or {}
        ch_type = (ch.get("type") or "").lower()
        fields = _sensitive_fields_for(ch_type, _resolve_method(ch_type, cfg))
        changed = False
        for field in fields:
            val = cfg.get(field) or ""
            if val.startswith("vault:"):
                # Vault refs are independent of portal password – skip
                continue
            if val.startswith(_ENC_PREFIX):
                try:
                    plaintext = decrypt(val[len(_ENC_PREFIX):], old_password)
                    cfg[field] = _ENC_PREFIX + encrypt(plaintext, new_password)
                    changed = True
                except Exception:
                    pass
        if changed:
            ch["config"] = cfg
            save_channel(ch)
            count += 1
    return count


# ---------------------------------------------------------------------------
# Template rendering
# ---------------------------------------------------------------------------

def _render(template: str, ctx: Dict[str, Any]) -> str:
    try:
        return template.format_map(ctx)
    except (KeyError, ValueError):
        return template


def _build_context(execution_meta: Dict[str, Any], workspace_name: str) -> Dict[str, Any]:
    sentinel = execution_meta.get("sentinel_result") or {}
    if sentinel:
        passed = sentinel.get("passed", False)
        total = sentinel.get("total_policies", 0)
        ok = sentinel.get("passed_count", 0)
        sentinel_status = "passed" if passed else "FAILED"
        sentinel_summary = f"{ok}/{total} policies passed"
    else:
        sentinel_status = "not_run"
        sentinel_summary = "N/A"

    return {
        "workspace_name": workspace_name or execution_meta.get("workspace_id", "?"),
        "workspace_id": execution_meta.get("workspace_id", ""),
        "command": execution_meta.get("command", ""),
        "status": execution_meta.get("status", ""),
        "duration": execution_meta.get("duration_seconds", "?"),
        "timestamp": execution_meta.get("timestamp", ""),
        "terraform_version": execution_meta.get("terraform_version") or "unknown",
        "sentinel_status": sentinel_status,
        "sentinel_summary": sentinel_summary,
    }


# ---------------------------------------------------------------------------
# Trigger evaluation
# ---------------------------------------------------------------------------

def _should_notify(channel: Dict[str, Any], execution_meta: Dict[str, Any]) -> bool:
    triggers = channel.get("notify_on") or []
    if not triggers:
        return False

    status = execution_meta.get("status", "")
    sentinel = execution_meta.get("sentinel_result") or {}
    sentinel_failed = sentinel and not sentinel.get("passed", True)

    if "on_success" in triggers and status == "completed":
        return True
    if "on_failure" in triggers and status == "failed":
        return True
    if "on_sentinel_fail" in triggers and sentinel_failed:
        return True
    return False


# ---------------------------------------------------------------------------
# Public dispatch entry point
# ---------------------------------------------------------------------------

def send_notifications_for_execution(
    execution_meta: Dict[str, Any],
    workspace_name: str,
) -> None:
    """
    Called after every completed/failed run.
    Fetches applicable channels and dispatches notifications best-effort.
    Secrets are decrypted inline before sending; plaintext never persisted.
    """
    workspace_id = execution_meta.get("workspace_id", "")
    try:
        channels = get_channels_for_workspace(workspace_id)
    except Exception:
        return

    ctx = _build_context(execution_meta, workspace_name)

    # Decrypt secrets using stored enc key (available via app context).
    enc_key = _get_enc_key()

    for ch in channels:
        if not ch.get("enabled", True):
            continue
        if not _should_notify(ch, execution_meta):
            continue
        # Decrypt secrets for this send only
        ch_plain = ch
        if enc_key:
            try:
                ch_plain = decrypt_channel_secrets(ch, enc_key)
            except Exception:
                pass
        try:
            _dispatch(ch_plain, ctx)
        except Exception as exc:
            print(
                f"[TGM][notifications] Error sending to channel "
                f"'{ch.get('name')}': {exc}",
                file=sys.stderr,
            )


def _dispatch(channel: Dict[str, Any], ctx: Dict[str, Any]) -> None:
    prefix = _render(channel.get("prefix_template") or DEFAULT_PREFIX, ctx)
    body = _render(channel.get("body_template") or DEFAULT_BODY, ctx)
    ch_type = (channel.get("type") or "").lower()
    cfg = channel.get("config") or {}

    if ch_type == "slack":
        _send_slack(cfg, prefix, body)
    elif ch_type == "teams":
        _send_teams(cfg, prefix, body)
    elif ch_type == "email":
        _send_email(cfg, prefix, body)
    elif ch_type == "pagerduty":
        _send_pagerduty(cfg, prefix, body, ctx)
    elif ch_type == "alertmanager":
        _send_alertmanager(cfg, prefix, body, ctx)


# ---------------------------------------------------------------------------
# Slack — Incoming Webhook  OR  Slack API (bot token)
# ---------------------------------------------------------------------------

def _send_slack(cfg: Dict[str, Any], prefix: str, body: str) -> None:
    method = (cfg.get("method") or "webhook").lower()

    if method == "token":
        _send_slack_api(cfg, prefix, body)
    else:
        _send_slack_webhook(cfg, prefix, body)


def _send_slack_webhook(cfg: Dict[str, Any], prefix: str, body: str) -> None:
    webhook_url = (cfg.get("webhook_url") or "").strip()
    if not webhook_url:
        return

    payload: Dict[str, Any] = {"text": f"{prefix}\n{body}"}
    channel_override = (cfg.get("channel") or "").strip()
    username = (cfg.get("username") or "").strip()
    icon = (cfg.get("icon_emoji") or "").strip()
    if channel_override:
        payload["channel"] = channel_override
    if username:
        payload["username"] = username
    if icon:
        payload["icon_emoji"] = icon

    verify_ssl = cfg.get("verify_ssl", True)
    _do_http_json(webhook_url, payload, verify_ssl=verify_ssl)


def _send_slack_api(cfg: Dict[str, Any], prefix: str, body: str) -> None:
    token = (cfg.get("token") or "").strip()
    channel = (cfg.get("channel") or "").strip()
    if not token or not channel:
        return

    username = (cfg.get("username") or "").strip()
    icon = (cfg.get("icon_emoji") or "").strip()

    payload: Dict[str, Any] = {
        "channel": channel,
        "text": f"{prefix}\n{body}",
    }
    if username:
        payload["username"] = username
    if icon:
        payload["icon_emoji"] = icon

    verify_ssl = cfg.get("verify_ssl", True)
    _do_http_json(
        "https://slack.com/api/chat.postMessage",
        payload,
        headers={"Authorization": f"Bearer {token}"},
        verify_ssl=verify_ssl,
    )


# ---------------------------------------------------------------------------
# Microsoft Teams — Incoming Webhook  OR  Microsoft Graph API
# ---------------------------------------------------------------------------

def _send_teams(cfg: Dict[str, Any], prefix: str, body: str) -> None:
    method = (cfg.get("method") or "webhook").lower()

    if method == "graph":
        _send_teams_graph(cfg, prefix, body)
    else:
        _send_teams_webhook(cfg, prefix, body)


def _send_teams_webhook(cfg: Dict[str, Any], prefix: str, body: str) -> None:
    webhook_url = (cfg.get("webhook_url") or "").strip()
    if not webhook_url:
        return

    theme_color = "0076D7"
    if "failed" in body.lower() or "FAILED" in body:
        theme_color = "C4314B"
    elif "completed" in body.lower():
        theme_color = "107C10"

    payload = {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "themeColor": theme_color,
        "summary": prefix,
        "sections": [
            {
                "activityTitle": prefix,
                "activityText": body.replace("\n", "\n\n"),
                "markdown": True,
            }
        ],
    }

    verify_ssl = cfg.get("verify_ssl", True)
    _do_http_json(webhook_url, payload, verify_ssl=verify_ssl)


def _send_teams_graph(cfg: Dict[str, Any], prefix: str, body: str) -> None:
    """
    Send via Microsoft Graph API:
      1. Obtain an app-only access token from Azure AD.
      2. POST a chatMessage to the configured team/channel.
    """
    tenant_id = (cfg.get("tenant_id") or "").strip()
    client_id = (cfg.get("client_id") or "").strip()
    client_secret = (cfg.get("client_secret") or "").strip()
    team_id = (cfg.get("team_id") or "").strip()
    channel_id = (cfg.get("channel_id") or "").strip()

    if not all([tenant_id, client_id, client_secret, team_id, channel_id]):
        return

    verify_ssl = cfg.get("verify_ssl", True)

    # 1. Acquire token (client_credentials)
    token_url = (
        f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    )
    token_payload = (
        f"client_id={client_id}"
        f"&client_secret={_url_encode(client_secret)}"
        "&scope=https%3A%2F%2Fgraph.microsoft.com%2F.default"
        "&grant_type=client_credentials"
    ).encode("utf-8")

    token_req = Request(
        token_url,
        data=token_payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    ssl_ctx: Optional[ssl.SSLContext] = None
    if not verify_ssl:
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE

    with urlopen(token_req, context=ssl_ctx, timeout=15) as resp:
        token_data = _json.loads(resp.read())

    access_token = token_data.get("access_token", "")
    if not access_token:
        raise RuntimeError("Teams Graph: failed to acquire access token")

    # 2. Post message
    msg_url = (
        f"https://graph.microsoft.com/v1.0"
        f"/teams/{team_id}/channels/{channel_id}/messages"
    )
    content = f"<strong>{prefix}</strong><br>{body.replace(chr(10), '<br>')}"
    msg_payload = {
        "body": {"contentType": "html", "content": content}
    }

    _do_http_json(
        msg_url,
        msg_payload,
        headers={"Authorization": f"Bearer {access_token}"},
        verify_ssl=verify_ssl,
    )


def _url_encode(value: str) -> str:
    from urllib.parse import quote
    return quote(value, safe="")


# ---------------------------------------------------------------------------
# Email — SMTP
# ---------------------------------------------------------------------------

def _send_email(cfg: Dict[str, Any], prefix: str, body: str) -> None:
    host = (cfg.get("smtp_host") or "").strip()
    if not host:
        return

    port = int(cfg.get("smtp_port") or 587)
    from_addr = (cfg.get("from_address") or "tgm@localhost").strip()
    to_raw = (cfg.get("to_addresses") or "").strip()
    to_addrs = [a.strip() for a in to_raw.split(",") if a.strip()]
    if not to_addrs:
        return

    username = (cfg.get("smtp_username") or "").strip()
    password = (cfg.get("smtp_password") or "").strip()
    use_ssl = cfg.get("use_ssl", False)
    use_starttls = cfg.get("use_starttls", True)

    subject = prefix
    msg = email.mime.text.MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = ", ".join(to_addrs)

    ctx = ssl.create_default_context()

    if use_ssl:
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=10) as server:
            if username:
                server.login(username, password)
            server.sendmail(from_addr, to_addrs, msg.as_string())
    else:
        with smtplib.SMTP(host, port, timeout=10) as server:
            if use_starttls:
                server.ehlo()
                server.starttls(context=ctx)
                server.ehlo()
            if username:
                server.login(username, password)
            server.sendmail(from_addr, to_addrs, msg.as_string())


# ---------------------------------------------------------------------------
# PagerDuty — Events API v2
# ---------------------------------------------------------------------------

def _send_pagerduty(
    cfg: Dict[str, Any], prefix: str, body: str, ctx: Dict[str, Any]
) -> None:
    routing_key = (cfg.get("routing_key") or "").strip()
    if not routing_key:
        return

    severity = (cfg.get("severity") or "warning").lower()
    if severity not in ("critical", "error", "warning", "info"):
        severity = "warning"

    base_url = (cfg.get("base_url") or "https://events.pagerduty.com").rstrip("/")
    verify_ssl = cfg.get("verify_ssl", True)

    payload = {
        "routing_key": routing_key,
        "event_action": "trigger",
        "payload": {
            "summary": f"{prefix} — {ctx.get('command')} {ctx.get('status')}",
            "severity": severity,
            "source": "terraform-graphical-manager",
            "custom_details": {
                "workspace": ctx.get("workspace_name"),
                "command": ctx.get("command"),
                "status": ctx.get("status"),
                "duration_seconds": ctx.get("duration"),
                "sentinel": ctx.get("sentinel_summary"),
                "body": body,
            },
        },
    }

    _do_http_json(f"{base_url}/v2/enqueue", payload, verify_ssl=verify_ssl)


# ---------------------------------------------------------------------------
# Prometheus Alertmanager — /api/v2/alerts
# ---------------------------------------------------------------------------

def _send_alertmanager(
    cfg: Dict[str, Any], prefix: str, body: str, ctx: Dict[str, Any]
) -> None:
    """
    POST a single alert to the Alertmanager HTTP API.

    Auth options
    ------------
    none   — open endpoint, no Authorization header
    token  — Bearer token (``token`` config field, encrypted)
    basic  — HTTP Basic auth (``username`` + ``password``, password encrypted)
    """
    url = (cfg.get("url") or "").strip().rstrip("/")
    if not url:
        return

    auth_type = (cfg.get("auth_type") or "none").lower()
    severity = (cfg.get("severity") or "warning").lower()
    if severity not in ("critical", "error", "warning", "info"):
        severity = "warning"
    verify_ssl = cfg.get("verify_ssl", True)
    generator_url = (cfg.get("generator_url") or "").strip()

    status = ctx.get("status", "")
    labels: Dict[str, str] = {
        "alertname": "TerraformRun",
        "severity": severity,
        "workspace": ctx.get("workspace_name", ""),
        "workspace_id": ctx.get("workspace_id", ""),
        "command": ctx.get("command", ""),
        "status": status,
        "source": "terraform-graphical-manager",
    }

    annotations: Dict[str, str] = {
        "summary": prefix,
        "description": body,
        "duration_seconds": str(ctx.get("duration", "")),
        "terraform_version": str(ctx.get("terraform_version", "")),
        "sentinel": str(ctx.get("sentinel_summary", "")),
    }

    alert: Dict[str, Any] = {
        "labels": labels,
        "annotations": annotations,
        "startsAt": ctx.get("timestamp", ""),
    }
    if generator_url:
        alert["generatorURL"] = generator_url

    # Build Authorization header
    extra_headers: Dict[str, str] = {}
    if auth_type == "token":
        token = (cfg.get("token") or "").strip()
        if token:
            extra_headers["Authorization"] = f"Bearer {token}"
    elif auth_type == "basic":
        import base64 as _b64
        username = (cfg.get("username") or "").strip()
        password = (cfg.get("password") or "").strip()
        if username:
            creds = _b64.b64encode(
                f"{username}:{password}".encode("utf-8")
            ).decode("ascii")
            extra_headers["Authorization"] = f"Basic {creds}"

    _do_http_json(
        f"{url}/api/v2/alerts",
        [alert],
        headers=extra_headers or None,
        verify_ssl=verify_ssl,
    )


# ---------------------------------------------------------------------------
# Test a channel (called from API — receives already-decrypted channel)
# ---------------------------------------------------------------------------

def test_channel(channel: Dict[str, Any]) -> Dict[str, Any]:
    """Fire a synthetic test notification. Returns {"ok": bool, "error": str}."""
    ctx = {
        "workspace_name": "test-workspace",
        "workspace_id": "test-ws-id",
        "command": "plan",
        "status": "completed",
        "duration": 12,
        "timestamp": "2026-01-01T00:00:00Z",
        "terraform_version": "1.0.0",
        "sentinel_status": "not_run",
        "sentinel_summary": "N/A",
    }
    try:
        _dispatch(channel, ctx)
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Internal HTTP helper
# ---------------------------------------------------------------------------

def _do_http_json(
    url: str,
    payload: Any,
    headers: Optional[Dict[str, str]] = None,
    verify_ssl: bool = True,
) -> Optional[bytes]:
    data = _json.dumps(payload).encode("utf-8")
    h = {"Content-Type": "application/json; charset=utf-8"}
    if headers:
        h.update(headers)
    req = Request(url, data=data, headers=h, method="POST")

    ssl_ctx: Optional[ssl.SSLContext] = None
    if not verify_ssl:
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE

    try:
        with urlopen(req, context=ssl_ctx, timeout=10) as resp:
            return resp.read()
    except URLError as exc:
        raise RuntimeError(str(exc)) from exc
