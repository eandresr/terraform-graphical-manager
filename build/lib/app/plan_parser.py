"""
Plan Parser — parses the JSON output of `terraform show -json <plan>` and
extracts resource_changes for the plan diff view.
"""
from typing import Any, Dict, List, Optional


# Action label and color mapping
ACTION_META: Dict[str, Dict[str, str]] = {
    "create":          {"label": "create",           "color": "green",  "symbol": "+"},
    "update":          {"label": "update in-place",  "color": "yellow", "symbol": "~"},
    "delete":          {"label": "destroy",          "color": "red",    "symbol": "-"},
    "replace":         {"label": "replace",          "color": "red",    "symbol": "-/+"},
    "no-op":           {"label": "no changes",       "color": "gray",   "symbol": "="},
    "read":            {"label": "read",             "color": "blue",   "symbol": "<="},
    "create_then_delete": {"label": "create then destroy", "color": "red", "symbol": "+/-"},
    "delete_then_create": {"label": "destroy then create", "color": "red", "symbol": "-/+"},
}


def parse_plan(plan_json: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parse a plan.json dict (from `terraform show -json`) and return a
    structured summary suitable for the plan diff UI.
    """
    if not plan_json:
        return _empty_summary()

    resource_changes: List[Dict[str, Any]] = plan_json.get("resource_changes", [])
    output_changes: Dict[str, Any] = plan_json.get("output_changes", {})

    counts: Dict[str, int] = {"create": 0, "update": 0, "delete": 0, "replace": 0, "no-op": 0}
    changes: List[Dict[str, Any]] = []

    for rc in resource_changes:
        change = rc.get("change", {})
        raw_actions: List[str] = change.get("actions", ["no-op"])
        action = _normalise_action(raw_actions)

        if action in counts:
            counts[action] += 1
        elif action not in ("read",):
            counts["no-op"] += 1

        before = change.get("before", {}) or {}
        after = change.get("after", {}) or {}

        diff_lines = _build_diff_lines(before, after, action)

        meta = ACTION_META.get(action, ACTION_META["no-op"])
        changes.append(
            {
                "address":        rc.get("address", ""),
                "module_address": rc.get("module_address"),
                "type":           rc.get("type", ""),
                "name":           rc.get("name", ""),
                "provider":       rc.get("provider_name", ""),
                "action":         action,
                "label":          meta["label"],
                "color":          meta["color"],
                "symbol":         meta["symbol"],
                "diff_lines":     diff_lines,
                "before_count":   _safe_count(before),
                "after_count":    _safe_count(after),
            }
        )

    # Sort: deletes first, then replacements, updates, creates, no-ops
    sort_order = {"delete": 0, "replace": 1, "update": 2, "create": 3, "no-op": 4, "read": 5}
    changes.sort(key=lambda c: sort_order.get(c["action"], 99))

    return {
        "counts": counts,
        "total_changes": (
            counts["create"] + counts["update"] + counts["delete"] + counts["replace"]
        ),
        "changes": changes,
        "output_changes": _parse_output_changes(output_changes),
        "terraform_version": plan_json.get("terraform_version"),
        "format_version": plan_json.get("format_version"),
    }


def _empty_summary() -> Dict[str, Any]:
    return {
        "counts": {"create": 0, "update": 0, "delete": 0, "replace": 0, "no-op": 0},
        "total_changes": 0,
        "changes": [],
        "output_changes": [],
        "terraform_version": None,
        "format_version": None,
    }


def _normalise_action(actions: List[str]) -> str:
    if actions == ["create"]:
        return "create"
    if actions == ["delete"]:
        return "delete"
    if actions == ["update"]:
        return "update"
    if set(actions) == {"create", "delete"}:
        return "replace"
    if actions == ["no-op"]:
        return "no-op"
    if actions == ["read"]:
        return "read"
    return "no-op"


def _build_diff_lines(before: Dict, after: Dict, action: str) -> List[Dict[str, str]]:
    """Build a per-attribute diff list for display."""
    if action == "no-op" or action == "read":
        return []

    all_keys = sorted(set(list(before.keys()) + list(after.keys())))
    lines: List[Dict[str, str]] = []

    for key in all_keys:
        b_val = before.get(key)
        a_val = after.get(key)

        if action == "create":
            lines.append({"key": key, "before": None, "after": _fmt(a_val), "type": "add"})
        elif action == "delete":
            lines.append({"key": key, "before": _fmt(b_val), "after": None, "type": "remove"})
        elif b_val != a_val:
            lines.append({
                "key": key, "before": _fmt(b_val), "after": _fmt(a_val), "type": "change"
            })

    return lines


def _fmt(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    import json
    return json.dumps(value)


def _safe_count(obj: Any) -> int:
    if isinstance(obj, dict):
        return len(obj)
    return 0


def _parse_output_changes(output_changes: Dict[str, Any]) -> List[Dict[str, Any]]:
    result = []
    for name, ch in output_changes.items():
        actions = ch.get("actions", ["no-op"])
        action = _normalise_action(actions)
        meta = ACTION_META.get(action, ACTION_META["no-op"])
        result.append({
            "name": name,
            "action": action,
            "color": meta["color"],
            "symbol": meta["symbol"],
        })
    return result
