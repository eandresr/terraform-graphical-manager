"""
Sentinel Runner — executes HashiCorp Sentinel CLI policy checks against
Terraform plan JSON output.

Policy sets are directories containing .sentinel files. An optional
sentinel.hcl inside a policy set defines per-policy enforcement levels.

Enforcement levels:
  advisory        — always passes the overall check (warning only)
  soft-mandatory  — fails unless overridden (not yet implemented for local)
  hard-mandatory  — always fails when the policy fails

TGM automatically generates a "tfplan/v2" mock from the plan JSON so that
policy files written for Terraform Cloud work without modification.

Expected policy set layout:
  <policies_path>/
  ├── require-tags/
  │   ├── sentinel.hcl          ← optional, defines enforcement levels
  │   ├── require-tags.sentinel
  │   └── ...
  └── restrict-providers/
      ├── restrict-providers.sentinel
      └── ...

Example sentinel.hcl:
  policy "require-tags" {
    enforcement_level = "hard-mandatory"
  }
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
from typing import Any, Callable, Dict, List, Optional


LogCallback = Callable[[str], None]

ENFORCEMENT_LEVELS = ("advisory", "soft-mandatory", "hard-mandatory")


# ---------------------------------------------------------------------------
# Discovery helpers
# ---------------------------------------------------------------------------

def sentinel_available(cli_path: Optional[str]) -> bool:
    """Return True if the Sentinel CLI can be located."""
    if cli_path and os.path.isfile(cli_path) and os.access(cli_path, os.X_OK):
        return True
    return shutil.which("sentinel") is not None


def get_sentinel_binary(cli_path: Optional[str]) -> str:
    """Return the resolved path to the Sentinel binary."""
    if cli_path and os.path.isfile(cli_path) and os.access(cli_path, os.X_OK):
        return cli_path
    found = shutil.which("sentinel")
    return found or "sentinel"


def discover_policy_sets(policies_path: Optional[str]) -> List[Dict[str, Any]]:
    """
    Return a list of policy set dicts found under *policies_path*.
    A directory qualifies as a policy set when it contains ≥ 1 .sentinel file.
    """
    if not policies_path or not os.path.isdir(policies_path):
        return []
    results: List[Dict[str, Any]] = []
    for entry in sorted(os.scandir(policies_path), key=lambda e: e.name):
        if not entry.is_dir():
            continue
        sentinel_files = [
            f.name for f in os.scandir(entry.path) if f.name.endswith(".sentinel")
        ]
        if not sentinel_files:
            continue
        has_hcl = os.path.isfile(os.path.join(entry.path, "sentinel.hcl"))
        results.append({
            "name": entry.name,
            "path": entry.path,
            "policy_count": len(sentinel_files),
            "has_config": has_hcl,
        })
    return results


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

class SentinelRunner:
    """Execute Sentinel policy checks against a Terraform plan JSON."""

    def __init__(
        self,
        sentinel_binary: str,
        global_policies_path: Optional[str] = None,
        workspace_extra_policies: Optional[str] = None,
    ):
        self._binary = sentinel_binary
        self._global_policies = global_policies_path
        self._workspace_extra = workspace_extra_policies

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def check_plan(
        self,
        plan_json: Dict[str, Any],
        log_cb: LogCallback,
        workspace_extra_override: Optional[str] = None,
        active_global_sets: Optional[List[str]] = None,
        active_extra_sets: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Run applicable policy sets against *plan_json*.
        active_global_sets / active_extra_sets: when provided, only run the
        named policy sets; None means all are enabled.
        Returns a result dict:
          {
            "passed": bool,
            "policy_sets_checked": int,
            "results": [{"policy", "policy_set", "enforcement_level",
                         "passed", "output"}, ...],
            "error": str | None,
          }
        """
        result: Dict[str, Any] = {
            "passed": False,
            "policy_sets_checked": 0,
            "results": [],
            "error": None,
        }

        # Collect all policy set paths, filtered by active sets when provided
        policy_sets: List[str] = []
        for ps in discover_policy_sets(self._global_policies):
            if active_global_sets is None or ps["name"] in active_global_sets:
                policy_sets.append(ps["path"])
        extra = workspace_extra_override or self._workspace_extra
        for ps in discover_policy_sets(extra):
            if active_extra_sets is None or ps["name"] in active_extra_sets:
                policy_sets.append(ps["path"])

        if not policy_sets:
            log_cb("[Sentinel] No policy sets configured — skipping checks.")
            result["passed"] = True
            return result

        result["policy_sets_checked"] = len(policy_sets)
        all_passed = True

        for ps_path in policy_sets:
            ps_results = self._run_policy_set(ps_path, plan_json, log_cb)
            result["results"].extend(ps_results)
            for pr in ps_results:
                lvl = pr.get("enforcement_level", "advisory")
                if not pr["passed"] and lvl in ("hard-mandatory", "soft-mandatory"):
                    all_passed = False

        result["passed"] = all_passed
        return result

    # ------------------------------------------------------------------
    # Internal: run a single policy set
    # ------------------------------------------------------------------

    def _run_policy_set(
        self,
        policy_set_path: str,
        plan_json: Dict[str, Any],
        log_cb: LogCallback,
    ) -> List[Dict[str, Any]]:
        ps_name = os.path.basename(policy_set_path)
        log_cb(f"[Sentinel] Policy set: {ps_name}")

        enforcement_levels = self._parse_sentinel_hcl(policy_set_path)
        policy_files = sorted(
            f for f in os.listdir(policy_set_path) if f.endswith(".sentinel")
        )

        results: List[Dict[str, Any]] = []

        # Build a temporary working directory with mock data
        tmpdir = tempfile.mkdtemp(prefix="tgm-sentinel-")
        try:
            # Write plan as a Sentinel mock module.
            # Sentinel parses .sentinel files as statements — a bare JSON object
            # `{"k": "v"}` would be a block-statement where `:` is invalid.
            # Writing each top-level key as an individual assignment (`k = json_val`)
            # produces valid Sentinel: nested objects are expressions where `:` IS valid.
            mock_path = os.path.join(tmpdir, "mock-tfplan-v2.sentinel")
            with open(mock_path, "w", encoding="utf-8") as fh:
                for key, val in plan_json.items():
                    fh.write(f"{key} = {json.dumps(val)}\n")

            # Generate a sentinel.hcl that maps tfplan/v2 to our mock JSON.
            # If the policy set already has a sentinel.hcl we ALSO create this
            # override so our mock is available without replacing the user's config.
            generated_hcl = os.path.join(tmpdir, "run.hcl")
            with open(generated_hcl, "w", encoding="utf-8") as fh:
                fh.write('mock "tfplan/v2" {\n')
                fh.write('  module {\n')
                fh.write(f'    source = {json.dumps(mock_path)}\n')
                fh.write('  }\n')
                fh.write('}\n')

            for policy_file in policy_files:
                policy_name = policy_file[:-9]  # strip .sentinel
                policy_path = os.path.join(policy_set_path, policy_file)
                enforcement = enforcement_levels.get(policy_name, "advisory")

                log_cb(
                    f"[Sentinel]   checking {policy_name} "
                    f"[{enforcement}]..."
                )

                policy_result = self._apply_policy(
                    policy_path, generated_hcl, enforcement, log_cb
                )
                policy_result["policy_set"] = ps_name
                results.append(policy_result)

        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

        return results

    def _apply_policy(
        self,
        policy_path: str,
        config_path: str,
        enforcement: str,
        log_cb: LogCallback,
    ) -> Dict[str, Any]:
        policy_name = os.path.splitext(os.path.basename(policy_path))[0]
        try:
            proc = subprocess.run(
                [
                    self._binary, "apply",
                    f"-config={config_path}",
                    policy_path,
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            passed = proc.returncode == 0
            output = (proc.stdout + proc.stderr).strip()
        except subprocess.TimeoutExpired:
            passed = False
            output = "Policy check timed out after 30 seconds."
        except FileNotFoundError:
            passed = False
            output = (
                "Sentinel CLI not found. "
                "Install it from https://developer.hashicorp.com/sentinel/downloads "
                "or configure sentinel.cli_path in tfg.conf."
            )

        status_label = "PASS" if passed else "FAIL"
        log_cb(f"[Sentinel]     → {status_label}: {policy_name}")
        if not passed:
            for line in output.splitlines()[:20]:
                log_cb(f"[Sentinel]       {line}")

        return {
            "policy": policy_name,
            "enforcement_level": enforcement,
            "passed": passed,
            "output": output,
        }

    # ------------------------------------------------------------------
    # Internal: parse sentinel.hcl for enforcement levels
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_sentinel_hcl(policy_set_path: str) -> Dict[str, str]:
        """
        Extract enforcement levels from sentinel.hcl.
        Returns a dict mapping policy name → enforcement level string.
        Falls back to an empty dict on any parse error.
        """
        hcl_path = os.path.join(policy_set_path, "sentinel.hcl")
        if not os.path.isfile(hcl_path):
            return {}
        levels: Dict[str, str] = {}
        try:
            text = open(hcl_path, "r", encoding="utf-8").read()
            pattern = (
                r'policy\s+"([^"]+)"\s*\{[^}]*'
                r'enforcement_level\s*=\s*"([^"]+)"'
            )
            for m in re.finditer(pattern, text, re.DOTALL):
                name, level = m.group(1), m.group(2)
                if level in ENFORCEMENT_LEVELS:
                    levels[name] = level
        except OSError:
            pass
        return levels
