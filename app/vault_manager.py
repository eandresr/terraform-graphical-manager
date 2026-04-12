"""
HashiCorp Vault Integration — secrets backend for sensitive variables.

Architecture
------------
When Vault is enabled, sensitive variable values (variable groups and
workspace-level variables) are stored in Vault KV-v2 instead of being
Fernet-encrypted in the local/cloud backend storage.

The value stored in the TGM backend JSON is replaced by a Vault *reference*
token of the form:  ``vault:<path>``

On read, TGM detects the ``vault:`` prefix, resolves the secret from Vault,
and returns the plaintext to the caller — exactly the same interface that the
Fernet-based crypto module exposes.

Auth methods
------------
  token   : Vault token stored encrypted in tfg.conf → vault.token
  approle : role_id (plain) + secret_id (encrypted) in tfg.conf

KV path layout
--------------
  <mount>/data/<path_prefix>/variable_groups/<group_id>/<var_key>
  <mount>/data/<path_prefix>/workspaces/<workspace_id>/vars/<var_key>

Migration helpers
-----------------
  migrate_to_vault(enc_key)        — move all Fernet secrets → Vault
  migrate_from_vault(new_enc_key)  — pull all Vault secrets → Fernet
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

# Prefix used in stored values to mark a Vault reference
VAULT_REF_PREFIX = "vault:"


# ---------------------------------------------------------------------------
# Low-level Vault client
# ---------------------------------------------------------------------------

class VaultClient:
    """Thin wrapper around hvac (HashiCorp Vault Python SDK)."""

    def __init__(
        self,
        url: str,
        auth_method: str,
        *,
        token: str = "",
        role_id: str = "",
        secret_id: str = "",
        mount: str = "secret",
        namespace: str = "",
        verify_ssl: bool = True,
    ):
        try:
            import hvac
        except ImportError as exc:
            raise RuntimeError(
                "hvac is required for Vault integration. "
                "Install it with: pip install hvac"
            ) from exc

        client = hvac.Client(
            url=url,
            verify=verify_ssl,
            namespace=namespace or None,
        )

        if auth_method == "token":
            if not token:
                raise ValueError("vault_token is required for token auth method.")
            client.token = token
        elif auth_method == "approle":
            if not role_id or not secret_id:
                raise ValueError(
                    "vault_role_id and vault_secret_id are required for AppRole auth."
                )
            resp = client.auth.approle.login(role_id=role_id, secret_id=secret_id)
            client.token = resp["auth"]["client_token"]
        else:
            raise ValueError(f"Unsupported Vault auth method: {auth_method!r}")

        if not client.is_authenticated():
            raise RuntimeError(
                "Vault authentication failed — check credentials and Vault URL."
            )

        self._client = client
        self._mount = mount

    # ------------------------------------------------------------------
    # KV-v2 operations
    # ------------------------------------------------------------------

    def write_secret(self, path: str, value: str) -> None:
        self._client.secrets.kv.v2.create_or_update_secret(
            path=path,
            secret={"value": value},
            mount_point=self._mount,
        )

    def read_secret(self, path: str) -> Optional[str]:
        try:
            resp = self._client.secrets.kv.v2.read_secret_version(
                path=path,
                mount_point=self._mount,
                raise_on_deleted_version=True,
            )
            return resp["data"]["data"].get("value")
        except Exception:
            return None

    def delete_secret(self, path: str) -> None:
        try:
            self._client.secrets.kv.v2.delete_metadata_and_all_versions(
                path=path,
                mount_point=self._mount,
            )
        except Exception:
            pass

    def list_secrets(self, path: str) -> list[str]:
        try:
            resp = self._client.secrets.kv.v2.list_secrets(
                path=path,
                mount_point=self._mount,
            )
            return resp["data"].get("keys", [])
        except Exception:
            return []

    def ping(self) -> Dict[str, Any]:
        """Return Vault health information; raises on error."""
        return self._client.sys.read_health_status(method="GET")


# ---------------------------------------------------------------------------
# Config-aware factory
# ---------------------------------------------------------------------------

def _get_vault_client(config, enc_key: str) -> VaultClient:
    """
    Build a VaultClient from the current TFG configuration.
    Sensitive credentials (token / secret_id) are Fernet-decrypted first.
    """
    from app.crypto import decrypt as _decrypt

    url = config.vault_url
    if not url:
        raise RuntimeError("Vault URL is not configured.")

    auth_method = config.vault_auth_method
    token = ""
    secret_id = ""

    if auth_method == "token":
        raw = config.vault_token
        if raw and enc_key:
            try:
                token = _decrypt(raw, enc_key)
            except Exception:
                token = raw  # already plaintext (legacy / test)
        else:
            token = raw
    elif auth_method == "approle":
        raw = config.vault_secret_id
        if raw and enc_key:
            try:
                secret_id = _decrypt(raw, enc_key)
            except Exception:
                secret_id = raw
        else:
            secret_id = raw

    return VaultClient(
        url=url,
        auth_method=auth_method,
        token=token,
        role_id=config.vault_role_id,
        secret_id=secret_id,
        mount=config.vault_mount,
        namespace=config.vault_namespace,
        verify_ssl=config.vault_verify_ssl,
    )


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _safe_key(key: str) -> str:
    """Sanitise a variable key for use as a Vault path segment."""
    return re.sub(r"[^a-zA-Z0-9_\-.]", "_", key)


def var_group_path(path_prefix: str, group_id: str, var_key: str) -> str:
    prefix = path_prefix.strip("/") or "tgm"
    return f"{prefix}/variable_groups/{group_id}/{_safe_key(var_key)}"


def workspace_var_path(path_prefix: str, workspace_id: str, var_key: str) -> str:
    prefix = path_prefix.strip("/") or "tgm"
    return f"{prefix}/workspaces/{workspace_id}/vars/{_safe_key(var_key)}"


def backend_credential_path(path_prefix: str, backend_type: str, field_name: str) -> str:
    """Vault path for a storage backend sensitive credential field."""
    prefix = path_prefix.strip("/") or "tgm"
    return f"{prefix}/backend_credentials/{backend_type}/{_safe_key(field_name)}"


def notification_channel_path(path_prefix: str, channel_id: str, field_name: str) -> str:
    """Vault path for a notification channel sensitive config field."""
    prefix = path_prefix.strip("/") or "tgm"
    return f"{prefix}/notification_channels/{channel_id}/{_safe_key(field_name)}"


# ---------------------------------------------------------------------------
# Reference helpers (used by variable_groups.py / api_routes.py)
# ---------------------------------------------------------------------------

def is_vault_ref(value: str) -> bool:
    return isinstance(value, str) and value.startswith(VAULT_REF_PREFIX)


def make_vault_ref(path: str) -> str:
    return f"{VAULT_REF_PREFIX}{path}"


def extract_path(ref: str) -> str:
    return ref[len(VAULT_REF_PREFIX):]


def store_secret(config, enc_key: str, path: str, plaintext: str) -> str:
    """
    Write *plaintext* to Vault at *path* and return the vault reference string.
    """
    client = _get_vault_client(config, enc_key)
    client.write_secret(path, plaintext)
    return make_vault_ref(path)


def resolve_secret(config, enc_key: str, ref: str) -> str:
    """
    Given a vault reference string (``vault:<path>``), return the plaintext.
    Raises RuntimeError if the secret cannot be fetched.
    """
    path = extract_path(ref)
    client = _get_vault_client(config, enc_key)
    value = client.read_secret(path)
    if value is None:
        raise RuntimeError(f"Vault secret not found at path: {path!r}")
    return value


def delete_secret_by_ref(config, enc_key: str, ref: str) -> None:
    path = extract_path(ref)
    client = _get_vault_client(config, enc_key)
    client.delete_secret(path)


# ---------------------------------------------------------------------------
# Connection test
# ---------------------------------------------------------------------------

def test_vault_connection(
    url: str,
    auth_method: str,
    *,
    token: str = "",
    role_id: str = "",
    secret_id: str = "",
    mount: str = "secret",
    namespace: str = "",
    verify_ssl: bool = True,
) -> Dict[str, Any]:
    """
    Attempt to authenticate and read the Vault health endpoint.
    Returns {"ok": True, "version": "..."} or {"ok": False, "error": "..."}.
    """
    try:
        client = VaultClient(
            url=url,
            auth_method=auth_method,
            token=token,
            role_id=role_id,
            secret_id=secret_id,
            mount=mount,
            namespace=namespace,
            verify_ssl=verify_ssl,
        )
        health = client.ping()
        version = health.get("version", "unknown")
        return {"ok": True, "version": version}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Migration: local Fernet  →  Vault
# ---------------------------------------------------------------------------

def migrate_to_vault(config, enc_key: str) -> Dict[str, Any]:
    """
    Iterate every sensitive variable in every variable group and workspace
    config, write the plaintext to Vault, and replace the stored blob with
    a ``vault:<path>`` reference.

    Returns a summary dict: {"groups_migrated": int, "vars_migrated": int, "errors": [...]}
    """
    from app.crypto import decrypt as _decrypt
    from app.storage import get_backend

    backend = get_backend(enc_key)
    client = _get_vault_client(config, enc_key)
    prefix = config.vault_path_prefix

    summary: Dict[str, Any] = {"groups_migrated": 0, "vars_migrated": 0, "errors": []}

    # ── Variable groups ────────────────────────────────────────────────
    try:
        groups = backend.list_variable_groups()
    except AttributeError:
        groups = []

    for group in groups:
        gid = group.get("id", "")
        changed = False
        new_vars = []
        for var in group.get("variables", []):
            var = dict(var)
            if var.get("sensitive") and var.get("value") and not is_vault_ref(var["value"]):
                try:
                    plaintext = _decrypt(var["value"], enc_key)
                    path = var_group_path(prefix, gid, var["key"])
                    client.write_secret(path, plaintext)
                    var["value"] = make_vault_ref(path)
                    summary["vars_migrated"] += 1
                    changed = True
                except Exception as exc:
                    summary["errors"].append(
                        f"group {gid} var {var.get('key')!r}: {exc}"
                    )
            new_vars.append(var)
        if changed:
            try:
                backend.save_variable_group(gid, {**group, "variables": new_vars})
                summary["groups_migrated"] += 1
            except Exception as exc:
                summary["errors"].append(f"save group {gid}: {exc}")

    # ── Per-workspace variables ────────────────────────────────────────
    try:
        from app.workspace_scanner import WorkspaceScanner
        scanner = WorkspaceScanner(config.repos_root)
        workspaces = scanner.get_flat_list()
    except Exception:
        workspaces = []

    for ws in workspaces:
        wid = ws.get("id", "")
        try:
            ws_cfg = backend.get_workspace_config(wid)
        except Exception:
            continue
        changed = False
        new_wvars = []
        for var in ws_cfg.get("variables", []):
            var = dict(var)
            if var.get("sensitive") and var.get("value") and not is_vault_ref(var["value"]):
                try:
                    plaintext = _decrypt(var["value"], enc_key)
                    path = workspace_var_path(prefix, wid, var["key"])
                    client.write_secret(path, plaintext)
                    var["value"] = make_vault_ref(path)
                    summary["vars_migrated"] += 1
                    changed = True
                except Exception as exc:
                    summary["errors"].append(
                        f"workspace {wid} var {var.get('key')!r}: {exc}"
                    )
            new_wvars.append(var)
        if changed:
            try:
                ws_cfg["variables"] = new_wvars
                backend.set_workspace_config(wid, ws_cfg)
            except Exception as exc:
                summary["errors"].append(f"save workspace config {wid}: {exc}")

    # ── Backend connector credentials ──────────────────────────────────
    try:
        from app.backend_config import (
            get_backend_config, save_backend_config, SENSITIVE_FIELDS as BC_SENSITIVE,
        )
        bc = get_backend_config(config)
        bt = (bc.get("type") or "").lower().strip()
        if bt and BC_SENSITIVE.get(bt):
            changed = False
            new_bc = dict(bc)
            for field in BC_SENSITIVE[bt]:
                raw = bc.get(field, "")
                if raw and not is_vault_ref(raw):
                    try:
                        plaintext = _decrypt(raw, enc_key)
                        path = backend_credential_path(prefix, bt, field)
                        client.write_secret(path, plaintext)
                        new_bc[field] = make_vault_ref(path)
                        summary["vars_migrated"] += 1
                        changed = True
                    except Exception as exc:
                        summary["errors"].append(
                            f"backend credential {bt}/{field}: {exc}"
                        )
            if changed:
                save_backend_config(config, new_bc)
    except Exception as exc:
        summary["errors"].append(f"backend credentials migration: {exc}")

    # ── Notification channels ─────────────────────────────────────
    try:
        from app.notification_manager import (
            list_all_channels, save_channel,
            _sensitive_fields_for, _resolve_method, _ENC_PREFIX,
        )
        for ch in list_all_channels():
            cfg = ch.get("config") or {}
            ch_type = (ch.get("type") or "").lower()
            fields = _sensitive_fields_for(ch_type, _resolve_method(ch_type, cfg))
            changed = False
            for field in fields:
                raw = cfg.get(field) or ""
                if raw.startswith(_ENC_PREFIX):
                    try:
                        plaintext = _decrypt(raw[len(_ENC_PREFIX):], enc_key)
                        path = notification_channel_path(
                            prefix, ch.get("id", "unknown"), field
                        )
                        client.write_secret(path, plaintext)
                        cfg[field] = make_vault_ref(path)
                        summary["vars_migrated"] += 1
                        changed = True
                    except Exception as exc:
                        summary["errors"].append(
                            f"channel {ch.get('id')!r} field {field!r}: {exc}"
                        )
            if changed:
                ch["config"] = cfg
                save_channel(ch)
    except Exception as exc:
        summary["errors"].append(f"notification channels migration: {exc}")

    # ── Global tfg.conf sensitive fields (metrics) ────────────────────────
    _GLOBAL_SENSITIVE = [
        ("metrics", "influxdb_token"),
        ("metrics", "prometheus_password"),
    ]
    for section, key in _GLOBAL_SENSITIVE:
        raw = config._parser.get(section, key, fallback="")
        if raw and not is_vault_ref(raw):
            try:
                path = f"{prefix}/global_config/{section}/{_safe_key(key)}"
                client.write_secret(path, raw)
                config.save({f"{section}.{key}": make_vault_ref(path)})
                summary["vars_migrated"] += 1
            except Exception as exc:
                summary["errors"].append(f"global config {section}.{key}: {exc}")

    return summary


# ---------------------------------------------------------------------------
# Migration: Vault  →  local Fernet
# ---------------------------------------------------------------------------

def migrate_from_vault(config, enc_key: str, new_enc_key: str) -> Dict[str, Any]:
    """
    Reverse migration: pull all ``vault:<path>`` references back into
    Fernet-encrypted blobs stored in the TGM backend, then delete the
    corresponding Vault paths.

    *new_enc_key* is the portal password used to encrypt the values locally
    (may be the same as *enc_key*).

    Returns {"vars_migrated": int, "errors": [...]}
    """
    from app.crypto import encrypt as _encrypt
    from app.storage import get_backend

    backend = get_backend(enc_key)
    client = _get_vault_client(config, enc_key)

    summary: Dict[str, Any] = {"vars_migrated": 0, "errors": []}

    # ── Variable groups ────────────────────────────────────────────────
    try:
        groups = backend.list_variable_groups()
    except AttributeError:
        groups = []

    for group in groups:
        gid = group.get("id", "")
        changed = False
        new_vars = []
        for var in group.get("variables", []):
            var = dict(var)
            if var.get("sensitive") and is_vault_ref(var.get("value", "")):
                try:
                    plaintext = resolve_secret(config, enc_key, var["value"])
                    old_path = extract_path(var["value"])
                    var["value"] = _encrypt(plaintext, new_enc_key)
                    client.delete_secret(old_path)
                    summary["vars_migrated"] += 1
                    changed = True
                except Exception as exc:
                    summary["errors"].append(
                        f"group {gid} var {var.get('key')!r}: {exc}"
                    )
            new_vars.append(var)
        if changed:
            try:
                backend.save_variable_group(gid, {**group, "variables": new_vars})
            except Exception as exc:
                summary["errors"].append(f"save group {gid}: {exc}")

    # ── Per-workspace variables ────────────────────────────────────────
    try:
        from app.workspace_scanner import WorkspaceScanner
        scanner = WorkspaceScanner(config.repos_root)
        workspaces = scanner.get_flat_list()
    except Exception:
        workspaces = []

    for ws in workspaces:
        wid = ws.get("id", "")
        try:
            ws_cfg = backend.get_workspace_config(wid)
        except Exception:
            continue
        changed = False
        new_wvars = []
        for var in ws_cfg.get("variables", []):
            var = dict(var)
            if var.get("sensitive") and is_vault_ref(var.get("value", "")):
                try:
                    plaintext = resolve_secret(config, enc_key, var["value"])
                    old_path = extract_path(var["value"])
                    var["value"] = _encrypt(plaintext, new_enc_key)
                    client.delete_secret(old_path)
                    summary["vars_migrated"] += 1
                    changed = True
                except Exception as exc:
                    summary["errors"].append(
                        f"workspace {wid} var {var.get('key')!r}: {exc}"
                    )
            new_wvars.append(var)
        if changed:
            try:
                ws_cfg["variables"] = new_wvars
                backend.set_workspace_config(wid, ws_cfg)
            except Exception as exc:
                summary["errors"].append(f"save workspace config {wid}: {exc}")

    # ── Backend connector credentials ──────────────────────────────────
    try:
        from app.backend_config import (
            get_backend_config, save_backend_config, SENSITIVE_FIELDS as BC_SENSITIVE,
        )
        bc = get_backend_config(config)
        bt = (bc.get("type") or "").lower().strip()
        if bt and BC_SENSITIVE.get(bt):
            changed = False
            new_bc = dict(bc)
            for field in BC_SENSITIVE[bt]:
                raw = bc.get(field, "")
                if raw and is_vault_ref(raw):
                    try:
                        plaintext = resolve_secret(config, enc_key, raw)
                        old_path = extract_path(raw)
                        new_bc[field] = _encrypt(plaintext, new_enc_key)
                        client.delete_secret(old_path)
                        summary["vars_migrated"] += 1
                        changed = True
                    except Exception as exc:
                        summary["errors"].append(
                            f"backend credential {bt}/{field}: {exc}"
                        )
            if changed:
                save_backend_config(config, new_bc)
    except Exception as exc:
        summary["errors"].append(f"backend credentials migration: {exc}")

    # ── Notification channels ─────────────────────────────────────
    try:
        from app.notification_manager import (
            list_all_channels, save_channel,
            _sensitive_fields_for, _resolve_method, _ENC_PREFIX,
        )
        for ch in list_all_channels():
            cfg = ch.get("config") or {}
            ch_type = (ch.get("type") or "").lower()
            fields = _sensitive_fields_for(ch_type, _resolve_method(ch_type, cfg))
            changed = False
            for field in fields:
                raw = cfg.get(field) or ""
                if is_vault_ref(raw):
                    try:
                        plaintext = resolve_secret(config, enc_key, raw)
                        old_path = extract_path(raw)
                        cfg[field] = _ENC_PREFIX + _encrypt(plaintext, new_enc_key)
                        client.delete_secret(old_path)
                        summary["vars_migrated"] += 1
                        changed = True
                    except Exception as exc:
                        summary["errors"].append(
                            f"channel {ch.get('id')!r} field {field!r}: {exc}"
                        )
            if changed:
                ch["config"] = cfg
                save_channel(ch)
    except Exception as exc:
        summary["errors"].append(f"notification channels migration: {exc}")

    # ── Global tfg.conf sensitive fields (metrics) ────────────────────────
    _GLOBAL_SENSITIVE = [
        ("metrics", "influxdb_token"),
        ("metrics", "prometheus_password"),
    ]
    for section, key in _GLOBAL_SENSITIVE:
        raw = config._parser.get(section, key, fallback="")
        if raw and is_vault_ref(raw):
            try:
                plaintext = resolve_secret(config, enc_key, raw)
                old_path = extract_path(raw)
                config.save({f"{section}.{key}": plaintext})
                client.delete_secret(old_path)
                summary["vars_migrated"] += 1
            except Exception as exc:
                summary["errors"].append(f"global config {section}.{key}: {exc}")

    return summary
