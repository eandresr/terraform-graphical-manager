"""
GitHub Module Checker
=====================
Detects Terraform module blocks whose ``source`` points to GitHub and verifies
whether the workspace has a ``GITHUB_TOKEN`` (or equivalent) env-var configured
via variable groups or workspace-level variables.

Why this matters
----------------
When Terraform pulls a private (or even public) module from GitHub over HTTPS,
it honours the ``GITHUB_TOKEN`` environment variable for authentication.
If the token is absent the ``terraform init`` call will fail with an
``authentication required`` or ``repository not found`` error.

What is checked
---------------
* Env-type variables whose key is one of :data:`GITHUB_TOKEN_VARS`
  present in any variable group that applies to the workspace (global or
  explicitly scoped to it).
* Env-type workspace-level variables stored in ``workspace_config.json``.

SSH-based sources (``git@github.com:...`` or ``git::ssh://git@github.com/...``)
do **not** require ``GITHUB_TOKEN``; they rely on SSH key authentication.  Such
sources are still reported so the operator is aware, but ``missing_token`` is
set to ``False`` for workspaces whose *only* GitHub sources are SSH-based.
"""
import os
import re
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Env-var keys accepted as "GitHub token is configured".
GITHUB_TOKEN_VARS: frozenset = frozenset({"GITHUB_TOKEN", "GITHUB_PAT", "GIT_PASSWORD"})

# Regex: source = "...github.com..."
_GITHUB_SOURCE_RE = re.compile(
    r'source\s*=\s*"([^"]*github\.com[^"]*)"',
    re.IGNORECASE,
)

# Regex: module "name"
_MODULE_NAME_RE = re.compile(
    r'\bmodule\s+"([^"]+)"',
    re.IGNORECASE,
)

# SSH-style GitHub sources that do NOT need GITHUB_TOKEN
_SSH_PATTERNS = (
    "git@github.com:",
    "git::ssh://git@github.com",
)

# ---------------------------------------------------------------------------
# Thread-safe in-memory cache
# ---------------------------------------------------------------------------

_LOCK = threading.Lock()
_CACHE: Dict[str, Dict[str, Any]] = {}

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def scan_github_modules(abs_path: str) -> List[Dict[str, str]]:
    """
    Scan all ``.tf`` files in *abs_path* (non-recursive) and return a list of
    dicts for each module block whose ``source`` points to GitHub.

    Each dict contains:
    * ``name``   — the Terraform module label (e.g. ``"vpc"``)
    * ``source`` — the raw source string    (e.g. ``"github.com/org/repo"``)
    * ``needs_token`` — ``True`` unless the source uses SSH authentication
    """
    modules: List[Dict[str, str]] = []
    try:
        entries = os.listdir(abs_path)
    except OSError:
        return modules

    for filename in sorted(entries):
        if not filename.endswith(".tf"):
            continue
        filepath = os.path.join(abs_path, filename)
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except OSError:
            continue
        _extract_github_modules(content, modules)

    # Deduplicate by (name, source)
    seen: set = set()
    unique: List[Dict[str, str]] = []
    for m in modules:
        key = (m["name"], m["source"])
        if key not in seen:
            seen.add(key)
            unique.append(m)

    return unique


def has_github_token(workspace_id: str) -> bool:
    """
    Return ``True`` if *workspace_id* has a valid GitHub-token variable
    configured in any reachable variable group or as a workspace variable.

    Sensitive variables are **not** decrypted — their mere presence (key
    existing) is enough to satisfy the check.
    """
    from app.variable_groups import list_all_groups
    from app.storage import get_backend

    # ---- Variable groups (global + workspace-scoped) ----
    try:
        for group in list_all_groups():
            ws_ids = group.get("workspace_ids") or []
            if ws_ids != ["*"] and workspace_id not in ws_ids:
                continue
            for var in group.get("variables", []):
                if var.get("type") == "env" and var.get("key", "") in GITHUB_TOKEN_VARS:
                    return True
    except Exception:
        pass

    # ---- Workspace individual variables ----
    try:
        ws_cfg = get_backend().get_workspace_config(workspace_id)
        for var in ws_cfg.get("variables", []):
            if var.get("type") == "env" and var.get("key", "") in GITHUB_TOKEN_VARS:
                return True
    except Exception:
        pass

    return False


def check_workspace(
    workspace_id: str,
    abs_path: str,
    workspace_name: str,
    workspace_path: str,
) -> Dict[str, Any]:
    """
    Run the full GitHub-token check for one workspace, update the cache, and
    return the result dict.

    The dict keys are:
    * ``workspace_id``       — encoded workspace ID
    * ``workspace_name``     — display name (last path component)
    * ``workspace_path``     — relative path under repos_root
    * ``uses_github_modules``— ``True`` if at least one module source points to GitHub
    * ``missing_token``      — ``True`` if any module *needs* a token and none is configured
    * ``github_modules``     — list of ``{name, source, needs_token}`` dicts
    * ``checked_at``         — ISO-8601 UTC timestamp
    """
    github_modules = scan_github_modules(abs_path)
    uses_github = bool(github_modules)

    # Only flag as missing when at least one source requires an HTTP token
    needs_token_modules = [m for m in github_modules if m.get("needs_token", True)]

    if needs_token_modules:
        missing_token = not has_github_token(workspace_id)
    else:
        missing_token = False

    result: Dict[str, Any] = {
        "workspace_id": workspace_id,
        "workspace_name": workspace_name,
        "workspace_path": workspace_path,
        "uses_github_modules": uses_github,
        "missing_token": missing_token,
        "github_modules": github_modules,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }

    with _LOCK:
        _CACHE[workspace_id] = result

    return result


def get_cached_result(workspace_id: str) -> Optional[Dict[str, Any]]:
    """Return the cached check result for *workspace_id*, or ``None``."""
    with _LOCK:
        entry = _CACHE.get(workspace_id)
        return dict(entry) if entry else None


def get_all_warnings() -> List[Dict[str, Any]]:
    """
    Return all cached entries where ``uses_github_modules`` is ``True``
    *and* ``missing_token`` is ``True``, sorted by workspace path.
    """
    with _LOCK:
        warnings = [
            dict(v)
            for v in _CACHE.values()
            if v.get("uses_github_modules") and v.get("missing_token")
        ]
    warnings.sort(key=lambda w: w.get("workspace_path", ""))
    return warnings


def scan_all_workspaces_background(repos_root: str) -> None:
    """
    Start a daemon thread that scans *every* workspace under *repos_root*.
    Called once from the Flask app factory so the cache is warm when users
    open the dashboard.
    """
    thread = threading.Thread(
        target=_scan_all_workspaces,
        args=(repos_root,),
        daemon=True,
        name="github-module-scanner",
    )
    thread.start()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_github_modules(content: str, modules: List[Dict[str, str]]) -> None:
    """
    Append GitHub-sourced module entries to *modules* by scanning *content*.

    Strategy: find every ``source = "...github.com..."`` occurrence and
    then walk backwards to find the nearest preceding ``module "name"``
    declaration.  This is robust enough for well-formed HCL without
    requiring a full parser.
    """
    for src_match in _GITHUB_SOURCE_RE.finditer(content):
        source_val = src_match.group(1)

        # Walk backwards for the nearest module label
        preceding = content[: src_match.start()]
        mod_matches = list(_MODULE_NAME_RE.finditer(preceding))
        mod_name = mod_matches[-1].group(1) if mod_matches else "unknown"

        needs_token = not any(source_val.startswith(pat) for pat in _SSH_PATTERNS)

        modules.append({
            "name": mod_name,
            "source": source_val,
            "needs_token": needs_token,
        })


def _scan_all_workspaces(repos_root: str) -> None:
    """Background worker: scan every workspace and populate the cache."""
    try:
        from app.workspace_scanner import WorkspaceScanner
        scanner = WorkspaceScanner(repos_root)
        workspaces = scanner.get_flat_list()
        for ws in workspaces:
            try:
                check_workspace(
                    ws["id"],
                    ws["abs_path"],
                    ws["name"],
                    ws["relative_path"],
                )
            except Exception:
                pass
    except Exception:
        pass
