"""Git Manager — helpers for branch/tag listing, checkout, pull,
and PAT (Personal Access Token) resolution per workspace.
"""
import os
import subprocess
from typing import Any, Dict, Optional


def is_git_repo(path: str, boundary: str = "") -> bool:
    """Return True if *path* (or a parent up to *boundary*) has a .git directory.

    *boundary* (typically repos_root) prevents walking above the workspace
    tree and mistakenly detecting a parent repository (e.g. the app's own
    git repo when workspaces live inside the source tree).
    """
    current = os.path.realpath(path)
    boundary = os.path.realpath(boundary) if boundary else ""
    while True:
        if os.path.isdir(os.path.join(current, ".git")):
            return True
        if boundary and (current == boundary or
                         not current.startswith(boundary + os.sep)):
            return False
        parent = os.path.dirname(current)
        if parent == current:
            return False
        current = parent


def list_refs(path: str) -> Dict[str, Any]:
    """Return branches (local + remote), tags and the current HEAD ref.

    Each branch entry is a dict ``{name, local, remote}`` so the frontend can
    distinguish branches that only exist on the remote from locally-checked-out
    ones.
    """
    try:
        import git as gitpython  # type: ignore
        repo = gitpython.Repo(path, search_parent_directories=True)
        local_names = {b.name for b in repo.branches}
        remote_names: set = set()
        for remote in repo.remotes:
            for ref in remote.refs:
                parts = ref.name.split("/", 1)
                if len(parts) == 2 and parts[1] != "HEAD":
                    remote_names.add(parts[1])
        all_names = sorted(local_names | remote_names, key=str.lower)
        branches = [
            {"name": n, "local": n in local_names, "remote": n in remote_names}
            for n in all_names
        ]
        tags = sorted([t.name for t in repo.tags], key=str.lower, reverse=True)
        if repo.head.is_detached:
            cur_sha = repo.head.commit.hexsha
            tag_match = next(
                (t.name for t in repo.tags if t.commit.hexsha == cur_sha), None
            )
            cur_type = "tag" if tag_match else "commit"
            cur_name = tag_match or cur_sha[:8]
        else:
            cur_name = repo.active_branch.name
            cur_sha = repo.head.commit.hexsha
            tag_match = next(
                (t.name for t in repo.tags if t.commit.hexsha == cur_sha), None
            )
            cur_type = "tag" if tag_match else "branch"
        return {
            "branches": branches,
            "tags": tags,
            "current": {"type": cur_type, "name": cur_name},
        }
    except ImportError:
        pass
    except Exception:
        pass

    # Fallback: subprocess
    try:
        b_res = subprocess.run(
            ["git", "branch", "--format=%(refname:short)"],
            cwd=path, capture_output=True, text=True, timeout=15,
        )
        local_names = {ln.strip() for ln in b_res.stdout.splitlines() if ln.strip()}
        rb_res = subprocess.run(
            ["git", "branch", "-r", "--format=%(refname:short)"],
            cwd=path, capture_output=True, text=True, timeout=15,
        )
        remote_names = set()
        for ln in rb_res.stdout.splitlines():
            ln = ln.strip()
            if not ln or "HEAD" in ln:
                continue
            parts = ln.split("/", 1)
            if len(parts) == 2:
                remote_names.add(parts[1])
        all_names = sorted(local_names | remote_names, key=str.lower)
        branches = [
            {"name": n, "local": n in local_names, "remote": n in remote_names}
            for n in all_names
        ]
        t_res = subprocess.run(
            ["git", "tag", "--sort=-version:refname"],
            cwd=path, capture_output=True, text=True, timeout=15,
        )
        tags = [ln.strip() for ln in t_res.stdout.splitlines() if ln.strip()]
        cur_res = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=path, capture_output=True, text=True, timeout=10,
        )
        cur_name = cur_res.stdout.strip()
        return {
            "branches": branches,
            "tags": tags,
            "current": {"type": "branch", "name": cur_name},
        }
    except Exception as exc:
        return {
            "branches": [],
            "tags": [],
            "current": {"type": "unknown", "name": ""},
            "error": str(exc),
        }


def _find_git_root(path: str) -> str:
    """Return the git root directory for the repo containing *path*.

    Falls back to *path* itself if the root cannot be determined.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=path, capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return path


def checkout_ref(
    path: str, ref: str, remote_only: bool = False
) -> Dict[str, Any]:
    """Checkout *ref* in the repo at *path*.

    Always runs from the git root so the whole working tree is switched, not
    just the workspace subdirectory.

    When *remote_only* is ``True`` the branch only exists on the remote:
    ``git fetch origin`` is run first (populates refs/remotes/origin/*) so
    git's DWIM can create a local tracking branch on checkout.
    Rejects refs containing '..' or starting with '-'.
    """
    if not ref or ".." in ref or ref.startswith("-"):
        return {"ok": False, "output": "Invalid ref."}
    root = _find_git_root(path)
    try:
        output_parts = []
        if remote_only:
            # fetch ALL remote refs so origin/<ref> exists for DWIM checkout
            fetch = subprocess.run(
                ["git", "fetch", "origin"],
                cwd=root, capture_output=True, text=True, timeout=60,
            )
            fetch_out = (fetch.stdout + fetch.stderr).strip()
            if fetch_out:
                output_parts.append(fetch_out)
            if fetch.returncode != 0:
                return {
                    "ok": False,
                    "output": "\n".join(output_parts) or "git fetch failed.",
                }
        result = subprocess.run(
            ["git", "checkout", ref],
            cwd=root, capture_output=True, text=True, timeout=30,
        )
        checkout_out = (result.stdout + result.stderr).strip()
        if checkout_out:
            output_parts.append(checkout_out)
        return {
            "ok": result.returncode == 0,
            "output": "\n".join(output_parts),
        }
    except Exception as exc:
        return {"ok": False, "output": str(exc)}


def fetch_all(path: str, token: Optional[str] = None) -> Dict[str, Any]:
    """Run ``git fetch --all --prune`` to refresh all remote refs."""
    root = _find_git_root(path)
    env = _env_with_token(root, token)
    try:
        result = subprocess.run(
            ["git", "fetch", "--all", "--prune"],
            cwd=root, capture_output=True, text=True, timeout=120, env=env,
        )
        return {"ok": result.returncode == 0, "output": result.stdout + result.stderr}
    except Exception as exc:
        return {"ok": False, "output": str(exc)}


def pull(path: str, token: Optional[str] = None) -> Dict[str, Any]:
    """Run ``git pull`` in *path*, injecting an HTTPS token when provided."""
    root = _find_git_root(path)
    env = _env_with_token(root, token)
    try:
        result = subprocess.run(
            ["git", "pull"],
            cwd=root, capture_output=True, text=True, timeout=120, env=env,
        )
        return {"ok": result.returncode == 0, "output": result.stdout + result.stderr}
    except Exception as exc:
        return {"ok": False, "output": str(exc)}


def get_token_for_workspace(workspace_id: str, enc_key: str = "") -> Optional[str]:
    """
    Resolve a git PAT for *workspace_id*. Search order:
      1. GITHUB_TOKEN / GIT_TOKEN environment variable (system-level).
      2. Workspace-level env variable named GITHUB_TOKEN or GIT_TOKEN.
      3. Group variables visible to this workspace.
    """
    for key in ("GITHUB_TOKEN", "GIT_TOKEN"):
        token = os.environ.get(key)
        if token:
            return token

    try:
        from app.storage import get_backend
        from app.crypto import decrypt as _decrypt
        backend = get_backend()
        ws_cfg = backend.get_workspace_config(workspace_id)
        for var in ws_cfg.get("variables") or []:
            k = (var.get("key") or "").strip()
            if k not in ("GITHUB_TOKEN", "GIT_TOKEN"):
                continue
            if var.get("type", "terraform") != "env":
                continue
            raw = var.get("value") or ""
            if var.get("sensitive") and enc_key and raw:
                try:
                    return _decrypt(raw, enc_key)
                except Exception:
                    continue
            if raw:
                return raw
    except Exception:
        pass

    try:
        from app.variable_groups import get_vars_for_workspace
        env_dict, _, _ = get_vars_for_workspace(workspace_id, enc_key)
        for key in ("GITHUB_TOKEN", "GIT_TOKEN"):
            if env_dict.get(key):
                return env_dict[key]
    except Exception:
        pass

    return None


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _env_with_token(path: str, token: Optional[str]) -> Dict[str, str]:
    """Return an env dict that injects an OAuth2 token for HTTPS GitHub remotes."""
    env = dict(os.environ)
    if token:
        env["GITHUB_TOKEN"] = token
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_CONFIG_COUNT"] = "1"
        # Inject credentials transparently without modifying .git/config
        env["GIT_CONFIG_KEY_0"] = (
            "url.https://oauth2:" + token + "@github.com/.insteadOf"
        )
        env["GIT_CONFIG_VALUE_0"] = "https://github.com/"
    return env
