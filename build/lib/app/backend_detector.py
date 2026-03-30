"""
Backend Detector — parses .tf files in a workspace directory to determine
which Terraform remote backend is configured (s3, gcs, azurerm, remote, local…).
"""
import os
import re
from typing import Optional

_BACKEND_PATTERNS: list = [
    ("s3", re.compile(r'backend\s+"s3"\s*{', re.IGNORECASE)),
    ("gcs", re.compile(r'backend\s+"gcs"\s*{', re.IGNORECASE)),
    ("azurerm", re.compile(r'backend\s+"azurerm"\s*{', re.IGNORECASE)),
    ("remote", re.compile(r'backend\s+"remote"\s*{', re.IGNORECASE)),
    ("http", re.compile(r'backend\s+"http"\s*{', re.IGNORECASE)),
    ("consul", re.compile(r'backend\s+"consul"\s*{', re.IGNORECASE)),
    ("kubernetes", re.compile(r'backend\s+"kubernetes"\s*{', re.IGNORECASE)),
    ("local", re.compile(r'backend\s+"local"\s*{', re.IGNORECASE)),
]


def detect_backend(workspace_path: str) -> Optional[str]:
    """
    Return the name of the first backend found in .tf files, or 'local'
    if no backend block is present at all.
    """
    content = _read_tf_files(workspace_path)
    for backend_name, pattern in _BACKEND_PATTERNS:
        if pattern.search(content):
            return backend_name
    # If there are tf files but no backend block → implicitly local
    if content.strip():
        return "local"
    return None


def _read_tf_files(workspace_path: str) -> str:
    parts: list = []
    try:
        for fname in os.listdir(workspace_path):
            if fname.endswith(".tf"):
                fpath = os.path.join(workspace_path, fname)
                try:
                    with open(fpath, "r", encoding="utf-8", errors="replace") as fh:
                        parts.append(fh.read())
                except OSError:
                    pass
    except OSError:
        pass
    return "\n".join(parts)
