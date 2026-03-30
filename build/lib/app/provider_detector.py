"""
Provider Detector — parses .tf files in a workspace directory to identify
which Terraform providers are used (aws, google, azurerm, etc.).
"""
import os
import re
from typing import List

# Patterns for explicit provider blocks and required_providers source entries
_PROVIDER_PATTERNS: List[tuple] = [
    ("aws", re.compile(r'provider\s+"aws"\s*{', re.IGNORECASE)),
    ("aws", re.compile(r'source\s*=\s*"hashicorp/aws"', re.IGNORECASE)),
    ("google", re.compile(r'provider\s+"google"\s*{', re.IGNORECASE)),
    ("google", re.compile(r'source\s*=\s*"hashicorp/google(?:-beta)?"', re.IGNORECASE)),
    ("azurerm", re.compile(r'provider\s+"azurerm"\s*{', re.IGNORECASE)),
    ("azurerm", re.compile(r'source\s*=\s*"hashicorp/azurerm"', re.IGNORECASE)),
    ("kubernetes", re.compile(r'provider\s+"kubernetes"\s*{', re.IGNORECASE)),
    ("helm", re.compile(r'provider\s+"helm"\s*{', re.IGNORECASE)),
    ("github", re.compile(r'provider\s+"github"\s*{', re.IGNORECASE)),
    ("datadog", re.compile(r'provider\s+"datadog"\s*{', re.IGNORECASE)),
    ("vault", re.compile(r'provider\s+"vault"\s*{', re.IGNORECASE)),
]


def detect_providers(workspace_path: str) -> List[str]:
    """
    Return a deduplicated list of provider names found in .tf files
    inside workspace_path (non-recursive — only the top-level dir).
    """
    content = _read_tf_files(workspace_path)
    found: set = set()
    for provider_name, pattern in _PROVIDER_PATTERNS:
        if pattern.search(content):
            found.add(provider_name)
    return sorted(found)


def _read_tf_files(workspace_path: str) -> str:
    """Concatenate content of all .tf files in the given directory."""
    parts: List[str] = []
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
