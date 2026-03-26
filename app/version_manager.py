"""
Terraform Version Manager — discovers locally installed Terraform versions
from a configured versions folder.

    Expected folder structure (both formats accepted):
    <versions_folder>/
    ├── 1.5.7/          (dots)      ← e.g. /opt/terraform/1.14.8/
    │   └── terraform
    ├── 1_6_0/          (underscores, legacy)
    │   └── terraform
    └── ...
"""
import json
import os
import re
import shutil
import subprocess
import sys
from typing import Dict, List, Optional


def discover_versions(versions_folder: Optional[str]) -> List[Dict]:
    """
    Scan *versions_folder* for subdirs named x_y_z containing a terraform
    binary.  Returns a list sorted newest-first:

        [{"version": "1.6.0", "label": "Terraform 1.6.0",
          "binary": "/abs/path/terraform", "dir_name": "1_6_0"}, ...]
    """
    versions: List[Dict] = []
    if not versions_folder or not os.path.isdir(versions_folder):
        return versions

    binary_name = "terraform.exe" if sys.platform == "win32" else "terraform"

    for entry in os.scandir(versions_folder):
        if not entry.is_dir():
            continue
        # Accept both "1.14.8" (dots) and "1_14_8" (underscores)
        if re.match(r"^\d+\.\d+\.\d+$", entry.name):
            version_str = entry.name                     # already dot-separated
        elif re.match(r"^\d+_\d+_\d+$", entry.name):
            version_str = entry.name.replace("_", ".")
        else:
            continue
        binary_path = os.path.join(entry.path, binary_name)

        if os.path.isfile(binary_path) and os.access(binary_path, os.X_OK):
            versions.append({
                "version": version_str,
                "label": f"Terraform {version_str}",
                "binary": binary_path,
                "dir_name": entry.name,
            })

    return sorted(
        versions,
        key=lambda v: [int(x) for x in v["version"].split(".")],
        reverse=True,
    )


def get_system_version() -> Optional[str]:
    """
    Run the system *terraform* and return the version string (e.g. "1.8.0").
    Returns None if terraform is not on PATH or the invocation fails.
    """
    terraform_cmd = shutil.which("terraform")
    if not terraform_cmd:
        return None

    # Try -json first (available since Terraform 0.13)
    try:
        result = subprocess.run(
            [terraform_cmd, "version", "-json"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            v = data.get("terraform_version")
            if v:
                return v
    except Exception:
        pass

    # Fallback: plain text
    try:
        result = subprocess.run(
            [terraform_cmd, "version"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            m = re.search(r"Terraform v([\d.]+)", result.stdout)
            if m:
                return m.group(1)
    except Exception:
        pass

    return None


def get_terraform_binary(version: Optional[str], versions_folder: Optional[str]) -> str:
    """
    Resolve the terraform binary path for *version*.

    - If *version* is None, ``"system"``, or empty → return the system binary.
    - If the requested version is found in *versions_folder* → return that path.
    - Otherwise fall back to the system binary.
    """
    if version and version != "system" and versions_folder:
        # Try dot-named dir first (e.g. "1.14.8"), then underscore (legacy "1_14_8")
        binary_name = "terraform.exe" if sys.platform == "win32" else "terraform"
        for dir_name in (version, version.replace(".", "_")):
            binary_path = os.path.join(versions_folder, dir_name, binary_name)
            if os.path.isfile(binary_path) and os.access(binary_path, os.X_OK):
                return binary_path

    # System default
    return shutil.which("terraform") or "terraform"
