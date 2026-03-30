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


# ---------------------------------------------------------------------------
# Runtime helpers: build env dict for an execution
# ---------------------------------------------------------------------------

def get_vars_for_workspace(
    workspace_id: str,
    password: str,
) -> Tuple[Dict[str, str], List[str]]:
    """
    Return ``(env_dict, sensitive_values)`` for all groups applied to
    *workspace_id*.

    *env_dict*         — maps env var names to their (decrypted) values.
    *sensitive_values* — plaintext values that must be masked in logs.

    Precedence rule: workspace-scoped groups override global groups.
    Within the same scope, later groups (alphabetically) override earlier ones.
    So: global-a < global-z < ws-local-a < ws-local-z.

    Sensitive variables are silently skipped when *password* is empty.
    """
    from app.crypto import decrypt

    env: Dict[str, str] = {}
    sensitive_values: List[str] = []

    all_groups = list_all_groups()  # sorted by name already
    # Split into two buckets so workspace-scoped always wins
    global_groups = [g for g in all_groups
                     if (g.get("workspace_ids") or []) == ["*"]]
    local_groups = [g for g in all_groups
                    if workspace_id in (g.get("workspace_ids") or [])
                    and (g.get("workspace_ids") or []) != ["*"]]

    for group in (*global_groups, *local_groups):  # globals first → local overrides

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
            else:
                value = raw_value

            var_type = var.get("type", "terraform")
            if var_type == "terraform":
                env[f"TF_VAR_{key}"] = value
            else:
                env[key] = value

    return env, sensitive_values


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
