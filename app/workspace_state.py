"""
Workspace State Cache — lightweight in-memory record of the *last* known
execution for each workspace.

This cache is:
  • Updated every time an execution finishes (via execution_queue._run_execution).
  • Seeded on startup from storage in a background thread (best-effort).
  • Read by the dashboard stats API to avoid scanning all historical executions.

Only the most-recent entry per workspace_id is kept, so memory usage is O(workspaces).
"""
from __future__ import annotations

import threading
from typing import Any, Dict, Optional

_LOCK = threading.Lock()
# workspace_id  →  last execution snapshot dict
_LAST_STATE: Dict[str, Dict[str, Any]] = {}


def update(workspace_id: str, data: Dict[str, Any]) -> None:
    """Record *data* as the latest execution for *workspace_id*.

    Only overwrites if *data* has a newer or equal timestamp than the
    currently cached entry — protects against out-of-order updates.
    """
    with _LOCK:
        existing = _LAST_STATE.get(workspace_id)
        if not existing or data.get("timestamp", "") >= existing.get("timestamp", ""):
            _LAST_STATE[workspace_id] = dict(data)


def get(workspace_id: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        entry = _LAST_STATE.get(workspace_id)
        return dict(entry) if entry else None


def get_all() -> Dict[str, Dict[str, Any]]:
    """Return a shallow copy of the entire cache."""
    with _LOCK:
        return {k: dict(v) for k, v in _LAST_STATE.items()}


def seed_from_storage_background() -> None:
    """Populate the cache from storage in a daemon thread (best-effort)."""
    t = threading.Thread(target=_seed, daemon=True, name="ws-state-seed")
    t.start()


def _seed() -> None:
    try:
        from app.storage import get_backend
        backend = get_backend()
        for meta in backend.list_all_executions():
            wid = meta.get("workspace_id")
            if wid:
                update(wid, meta)
    except Exception:
        pass
