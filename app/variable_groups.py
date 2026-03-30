"""
Variable Groups — named containers of Terraform / environment variables
that can be applied to one or more workspaces.

Variable types
--------------
  terraform  → injected as TF_VAR_<key>=<value>
  env        → injected as <key>=<value>

Sensitive variables
-------------------
Sensitive values are stored Fernet-encrypted in the backend.
The encryption key is derived from the portal plaintext password.
If no password is configured, sensitive variables cannot be used:
the sensitive toggle is disabled in the UI and the API rejects attempts
to create them.

Group scope
-----------
  workspace_ids == ["*"]         → applied to every workspace
  workspace_ids == ["id1","id2"] → applied to the listed workspaces
  workspace_ids == []            → not applied anywhere (draft)
"""
import uuid
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Storage helpers (backend-agnostic)
# ---------------------------------------------------------------------------

def _backend():
    from app.storage import get_backend
    return get_backend()


def list_all_groups() -> List[Dict[str, Any]]:
    """Return all groups; sensitive values remain encrypted blobs."""
    try:
        return _backend().list_variable_groups()
    except AttributeError:
        return []


def get_group(group_id: str) -> Optional[Dict[str, Any]]:
    try:
        return _backend().get_variable_group(group_id)
    except AttributeError:
        return None


def save_group(
    group_data: Dict[str, Any],
    password: str,
    *,
    existing_group: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Persist *group_data*, encrypting any sensitive variables.

    Rules for sensitive variable values
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    - If *value* is non-empty → encrypt and store.
    - If *value* is empty/None and *existing_group* exists → keep the
      old encrypted blob unchanged (caller is not re-setting the value).
    - If *value* is empty and there is no prior value → store empty.
    """
    from app.crypto import encrypt

    if not group_data.get("id"):
        group_data = {**group_data, "id": str(uuid.uuid4())}

    encrypted_vars: List[Dict[str, Any]] = []
    for var in group_data.get("variables", []):
        var = dict(var)
        if var.get("sensitive"):
            if not password:
                raise ValueError(
                    "A portal password must be set before using sensitive variables."
                )
            new_val: str = (var.get("value") or "").strip()
            if new_val:
                var["value"] = encrypt(new_val, password)
            elif existing_group:
                old = next(
                    (v for v in existing_group.get("variables", [])
                     if v.get("key") == var.get("key")),
                    None,
                )
                var["value"] = old["value"] if old else ""
            else:
                var["value"] = ""
        encrypted_vars.append(var)

    group_data = {**group_data, "variables": encrypted_vars}
    _backend().save_variable_group(group_data["id"], group_data)
    return group_data


def delete_group(group_id: str) -> None:
    try:
        _backend().delete_variable_group(group_id)
    except AttributeError:
        pass


def unsensitize_all_sensitive(password: str) -> int:
    """
    Decrypt every sensitive variable in every group using *password* and
    store the plaintext value with ``sensitive=False``.
    Returns the number of variables converted.
    """
    from app.crypto import decrypt

    converted = 0
    for group in list_all_groups():
        changed = False
        new_vars: List[Dict[str, Any]] = []
        for var in group.get("variables", []):
            var = dict(var)
            if var.get("sensitive") and var.get("value"):
                try:
                    var["value"] = decrypt(var["value"], password)
                    var["sensitive"] = False
                    converted += 1
                    changed = True
                except Exception:
                    pass  # leave corrupted blob as-is
            new_vars.append(var)
        if changed:
            updated = {**group, "variables": new_vars}
            _backend().save_variable_group(updated["id"], updated)
    return converted


def reencrypt_all_sensitive(old_password: str, new_password: str) -> int:
    """
    Re-encrypt every sensitive variable in every group using *new_password*.
    Decrypts each value with *old_password* first; silently skips values that
    cannot be decrypted (e.g. already corrupted blobs).
    Returns the number of variables successfully migrated.
    """
    from app.crypto import decrypt, encrypt

    migrated = 0
    for group in list_all_groups():
        changed = False
        new_vars: List[Dict[str, Any]] = []
        for var in group.get("variables", []):
            var = dict(var)
            if var.get("sensitive") and var.get("value"):
                try:
                    plaintext = decrypt(var["value"], old_password)
                    var["value"] = encrypt(plaintext, new_password)
                    migrated += 1
                    changed = True
                except Exception:
                    pass  # leave corrupted blob as-is
            new_vars.append(var)
        if changed:
            updated = {**group, "variables": new_vars}
            _backend().save_variable_group(updated["id"], updated)
    return migrated


# ---------------------------------------------------------------------------
# Runtime helpers: build env dict for an execution
# ---------------------------------------------------------------------------

def get_vars_for_workspace(
    workspace_id: str,
    password: str,
) -> Tuple[Dict[str, str], List[str], List[Dict[str, Any]]]:
    """
    Return ``(env_dict, sensitive_values, var_log_entries)`` for all groups
    applied to *workspace_id*.

    *env_dict*         — maps env var names to their (decrypted) values.
    *sensitive_values* — plaintext values that must be masked in logs.
    *var_log_entries*  — list of dicts for run-header logging, each with:
                           env_key       — final env var name (e.g. ``TF_VAR_foo``)
                           display_value — plaintext value or ``"***"`` if sensitive
                           source        — ``"workspace"`` or ``"carpeta"``
                           sensitive     — bool

    Precedence rule: workspace-scoped groups override global groups.
    Within the same scope, later groups (alphabetically) override earlier ones.
    So: global-a < global-z < ws-local-a < ws-local-z.

    Sensitive variables are silently skipped when *password* is empty.
    """
    from app.crypto import decrypt

    env: Dict[str, str] = {}
    sensitive_values: List[str] = []
    # key → entry dict so later (higher precedence) groups overwrite earlier
    var_log: Dict[str, Dict[str, Any]] = {}

    all_groups = list_all_groups()  # sorted by name already
    # Split into two buckets so workspace-scoped always wins
    global_groups = [g for g in all_groups
                     if (g.get("workspace_ids") or []) == ["*"]]
    local_groups = [g for g in all_groups
                    if workspace_id in (g.get("workspace_ids") or [])
                    and (g.get("workspace_ids") or []) != ["*"]]

    for source_label, group_list in (
        ("carpeta", global_groups),
        ("workspace", local_groups),
    ):
        for group in group_list:
            for var in group.get("variables", []):
                key = (var.get("key") or "").strip()
                if not key:
                    continue

                raw_value: str = var.get("value") or ""
                is_sensitive: bool = var.get("sensitive", False)

                if is_sensitive:
                    if not password or not raw_value:
                        continue
                    try:
                        value = decrypt(raw_value, password)
                    except ValueError:
                        continue
                    sensitive_values.append(value)
                    display_value = "***"
                else:
                    value = raw_value
                    display_value = value

                var_type = var.get("type", "terraform")
                if var_type == "terraform":
                    env_key = f"TF_VAR_{key}"
                else:
                    env_key = key

                env[env_key] = value
                var_log[env_key] = {
                    "env_key": env_key,
                    "display_value": display_value,
                    "source": source_label,
                    "sensitive": is_sensitive,
                }

    return env, sensitive_values, list(var_log.values())


# ---------------------------------------------------------------------------
# Frontend serialisation (never expose encrypted blobs)
# ---------------------------------------------------------------------------

def sanitize_for_frontend(group: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of *group* with sensitive values replaced by None."""
    sanitized_vars = [
        {**v, "value": None} if v.get("sensitive") else v
        for v in group.get("variables", [])
    ]
    return {**group, "variables": sanitized_vars}
