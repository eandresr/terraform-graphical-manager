"""
Workspace Scanner — recursively discovers Terraform workspaces under repos_root.
A directory is considered a workspace if it contains at least one .tf file.
"""
import base64
import os
from typing import Any, Dict, List, Optional

from app.provider_detector import detect_providers
from app.backend_detector import detect_backend


# ---------------------------------------------------------------------------
# ID encoding helpers
# ---------------------------------------------------------------------------

def encode_workspace_id(relative_path: str) -> str:
    """Encode a relative workspace path as a URL-safe identifier."""
    return base64.urlsafe_b64encode(relative_path.encode()).decode().rstrip("=")


def decode_workspace_id(workspace_id: str) -> str:
    """Decode a workspace ID back to its relative path."""
    padding = 4 - len(workspace_id) % 4
    if padding != 4:
        workspace_id += "=" * padding
    return base64.urlsafe_b64decode(workspace_id.encode()).decode()


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------

class WorkspaceScanner:
    def __init__(self, repos_root: str):
        self.repos_root = os.path.realpath(os.path.expanduser(repos_root))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_flat_list(self) -> List[Dict[str, Any]]:
        """Return a flat list of workspace dicts."""
        workspaces: List[Dict[str, Any]] = []
        if not os.path.isdir(self.repos_root):
            return workspaces

        for dirpath, dirnames, filenames in os.walk(self.repos_root):
            # Skip hidden directories and .terraform cache dirs
            dirnames[:] = [
                d
                for d in sorted(dirnames)
                if not d.startswith(".") and d != ".terraform"
            ]

            if self._has_tf_files(dirpath, filenames):
                rel = os.path.relpath(dirpath, self.repos_root)
                workspaces.append(self._build_workspace(dirpath, rel))

        return workspaces

    def get_tree(self) -> Dict[str, Any]:
        """Return a nested tree structure for the sidebar."""
        root: Dict[str, Any] = {}
        for ws in self.get_flat_list():
            parts = ws["relative_path"].replace("\\", "/").split("/")
            node = root
            for i, part in enumerate(parts):
                if part not in node:
                    node[part] = {"children": {}, "workspace": None}
                if i == len(parts) - 1:
                    node[part]["workspace"] = ws
                node = node[part]["children"]
        return root

    def get_workspace_by_id(self, workspace_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a workspace dict by its encoded ID."""
        try:
            rel = decode_workspace_id(workspace_id)
        except Exception:
            return None
        abs_path = os.path.realpath(os.path.join(self.repos_root, rel))
        # Security: ensure path is still within repos_root
        if not abs_path.startswith(self.repos_root + os.sep) and abs_path != self.repos_root:
            return None
        if not os.path.isdir(abs_path):
            return None
        return self._build_workspace(abs_path, rel)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _has_tf_files(dirpath: str, filenames: List[str]) -> bool:
        return any(f.endswith(".tf") for f in filenames)

    def _build_workspace(self, abs_path: str, rel: str) -> Dict[str, Any]:
        workspace_id = encode_workspace_id(rel)
        parts = rel.replace("\\", "/").split("/")
        name = parts[-1]
        providers = detect_providers(abs_path)
        backend = detect_backend(abs_path)
        has_git = self._has_git_repo(abs_path)
        git_info = self._get_git_info(abs_path) if has_git else {
            "branch": None, "commit_hash": None,
            "commit_author": None, "commit_message": None,
        }
        return {
            "id": workspace_id,
            "name": name,
            "relative_path": rel,
            "abs_path": abs_path,
            "parts": parts,
            "providers": providers,
            "backend": backend,
            "has_git": has_git,
            "git": git_info,
        }

    @staticmethod
    def _has_git_repo(path: str) -> bool:
        """Check if the workspace (or any parent) has a .git directory."""
        current = os.path.realpath(path)
        while True:
            if os.path.isdir(os.path.join(current, ".git")):
                return True
            parent = os.path.dirname(current)
            if parent == current:
                return False
            current = parent

    @staticmethod
    def _get_git_info(path: str) -> Dict[str, Any]:
        """Detect Git metadata for a workspace directory."""
        try:
            import git as gitpython

            # Walk up to find the git root
            repo = gitpython.Repo(path, search_parent_directories=True)
            head = repo.head
            commit = head.commit
            return {
                "branch": head.reference.name if not repo.head.is_detached else "DETACHED",
                "commit_hash": commit.hexsha[:8],
                "commit_author": str(commit.author),
                "commit_message": commit.message.strip().splitlines()[0],
            }
        except Exception:
            return {
                "branch": None,
                "commit_hash": None,
                "commit_author": None,
                "commit_message": None,
            }
