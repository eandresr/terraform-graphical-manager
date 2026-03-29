"""
Terraform Runner — executes Terraform CLI commands via subprocess and streams
logs line by line through the provided callback.

All commands are run with an isolated environment dict (never shell=True).
"""
import json
import os
import re
import subprocess
import tempfile
from typing import Callable, Dict, List, Optional


LogCallback = Callable[[str], None]


class TerraformRunner:
    def __init__(
        self,
        workspace_path: str,
        env: Dict[str, str],
        terraform_binary: Optional[str] = None,
    ):
        self.workspace_path = workspace_path
        self.env = env
        self._tf_binary = terraform_binary or self._find_terraform()

    # ------------------------------------------------------------------
    # Public commands
    # ------------------------------------------------------------------

    def init(self, log_cb: LogCallback) -> bool:
        return self._run(["init", "-no-color", "-input=false"], log_cb)

    def plan(
        self,
        log_cb: LogCallback,
        plan_binary_path: str,
    ) -> bool:
        return self._run(
            ["plan", "-no-color", "-input=false", f"-out={plan_binary_path}"],
            log_cb,
        )

    def show_json(self, plan_binary_path: str, log_cb: LogCallback) -> Optional[Dict]:
        """Run `terraform show -json <plan>` and return parsed JSON."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as tmp:
            tmp_path = tmp.name

        try:
            ok = self._run(["show", "-json", plan_binary_path], log_cb, capture_to=tmp_path)
            if not ok:
                return None
            with open(tmp_path, "r") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError):
            return None
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def apply(self, log_cb: LogCallback, plan_binary_path: str) -> bool:
        return self._run(
            ["apply", "-no-color", "-input=false", "-auto-approve", plan_binary_path],
            log_cb,
        )

    def state_pull(self) -> Optional[Dict]:
        """Run `terraform state pull` and return parsed JSON."""
        result = self._capture(["state", "pull"])
        if result is None:
            return None
        try:
            return json.loads(result)
        except json.JSONDecodeError:
            return None

    def graph(self) -> Optional[str]:
        """Run `terraform graph` and return raw DOT output."""
        return self._capture(["graph"])

    def plan_refresh_only(self) -> Optional[Dict]:
        """Run `terraform plan -refresh-only -json` for drift detection."""
        result = self._capture(
            ["plan", "-refresh-only", "-json", "-input=false", "-no-color"]
        )
        if result is None:
            return None
        # The output is newline-delimited JSON objects
        changes: List[Dict] = []
        for line in result.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                changes.append(obj)
            except json.JSONDecodeError:
                pass
        return changes

    def version(self) -> Optional[str]:
        """Return terraform version string."""
        result = self._capture(["version", "-json"])
        if result:
            try:
                data = json.loads(result)
                return data.get("terraform_version")
            except json.JSONDecodeError:
                pass
        # Fallback: plain text output
        result = self._capture(["version"])
        if result:
            m = re.search(r"Terraform v([\d.]+)", result)
            if m:
                return m.group(1)
        return None

    def output_json(self) -> Optional[Dict]:
        """Run `terraform output -json` and return parsed dict."""
        result = self._capture(["output", "-json", "-no-color"])
        if result is None:
            return None
        try:
            return json.loads(result)
        except json.JSONDecodeError:
            return None

    def check_lock(self) -> bool:
        """
        Run a very short plan with -lock-timeout=1s to detect state locks.
        Returns True if a lock was detected.
        """
        result = self._capture(
            ["plan", "-lock-timeout=1s", "-input=false", "-no-color"]
        )
        if result and "Error locking state" in result:
            return True
        return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run(
        self,
        args: List[str],
        log_cb: LogCallback,
        capture_to: Optional[str] = None,
    ) -> bool:
        """
        Run a Terraform subcommand, streaming stdout/stderr to log_cb.
        If capture_to is set, write stdout to that file path instead.
        Returns True on exit code 0.
        """
        cmd = [self._tf_binary] + args
        capture_fh = None
        try:
            if capture_to:
                capture_fh = open(capture_to, "w")

            proc = subprocess.Popen(
                cmd,
                cwd=self.workspace_path,
                env=self.env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )

            for line in proc.stdout:  # type: ignore[union-attr]
                stripped = line.rstrip("\n")
                if capture_fh:
                    capture_fh.write(line)
                else:
                    log_cb(stripped)

            proc.wait()
            return proc.returncode == 0
        except FileNotFoundError:
            log_cb("ERROR: terraform binary not found. Is Terraform installed?")
            return False
        except Exception as exc:
            log_cb(f"ERROR: {exc}")
            return False
        finally:
            if capture_fh:
                capture_fh.close()

    def _capture(self, args: List[str]) -> Optional[str]:
        """Run a Terraform subcommand and return combined stdout+stderr as string."""
        cmd = [self._tf_binary] + args
        try:
            result = subprocess.run(
                cmd,
                cwd=self.workspace_path,
                env=self.env,
                capture_output=True,
                text=True,
                timeout=120,
            )
            return result.stdout
        except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
            return None

    @staticmethod
    def _find_terraform() -> str:
        """Locate the terraform binary; fall back to bare name if not found."""
        import shutil
        path = shutil.which("terraform")
        return path or "terraform"
