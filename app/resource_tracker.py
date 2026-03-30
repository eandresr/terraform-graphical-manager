"""
Resource Tracker — builds snapshots of Terraform state and diffs them to
record which resources were created, modified, or deleted in each run.

History is stored per workspace as ``resource_history.json`` in the storage
backend.  Each key is a fully-qualified instance address; value contains:
  - created_run_id / created_at   — run that first created the resource
  - last_run_id / last_run_at     — run that most recently touched it
  - last_action                   — "created" | "modified" | "deleted"
"""
import hashlib
import json
from typing import Any, Dict


def build_snapshot(raw_state: Dict[str, Any]) -> Dict[str, str]:
    """
    Return ``{instance_address: attrs_hash}`` from a raw terraform state dict
    (i.e. the JSON from ``terraform state pull``).
    """
    snapshot: Dict[str, str] = {}
    for r in raw_state.get("resources", []):
        resource_type = r.get("type", "")
        name = r.get("name", "")
        module = r.get("module")
        mode = r.get("mode", "managed")

        if mode == "data":
            base = (
                f"{module}.data.{resource_type}.{name}"
                if module
                else f"data.{resource_type}.{name}"
            )
        else:
            base = (
                f"{module}.{resource_type}.{name}"
                if module
                else f"{resource_type}.{name}"
            )

        for inst in r.get("instances", []):
            ik = inst.get("index_key")
            if ik is not None:
                addr = base + (f'["{ik}"]' if isinstance(ik, str) else f"[{ik}]")
            else:
                addr = base

            attrs = inst.get("attributes") or {}
            h = hashlib.md5(
                json.dumps(attrs, sort_keys=True).encode()
            ).hexdigest()
            snapshot[addr] = h

    return snapshot


def diff_snapshots(
    before: Dict[str, str],
    after: Dict[str, str],
) -> Dict[str, str]:
    """
    Compare two snapshots and return ``{addr: action}`` where action is
    ``"created"``, ``"modified"``, or ``"deleted"``.
    """
    changes: Dict[str, str] = {}
    for addr, h in after.items():
        if addr not in before:
            changes[addr] = "created"
        elif before[addr] != h:
            changes[addr] = "modified"
    for addr in before:
        if addr not in after:
            changes[addr] = "deleted"
    return changes


def record_run_changes(
    workspace_id: str,
    execution_id: str,
    timestamp: str,
    changes: Dict[str, str],
) -> None:
    """
    Persist resource-level history so the Resources tab can show which run
    created / last-modified each entry.  Silently does nothing on errors so
    a tracking failure never blocks an execution.
    """
    if not changes:
        return
    try:
        from app.storage import get_backend
        backend = get_backend()

        history: Dict[str, Any] = {}
        if hasattr(backend, "get_resource_history"):
            history = backend.get_resource_history(workspace_id) or {}

        for addr, action in changes.items():
            entry = history.get(addr, {})
            if action == "created" or not entry.get("created_run_id"):
                entry["created_run_id"] = execution_id
                entry["created_at"] = timestamp
            entry["last_run_id"] = execution_id
            entry["last_run_at"] = timestamp
            entry["last_action"] = action
            history[addr] = entry

        if hasattr(backend, "set_resource_history"):
            backend.set_resource_history(workspace_id, history)

    except Exception:
        pass
