"""
Execution Queue — manages a pool of worker threads that process Terraform
executions. Each execution has its own isolated environment, log buffer,
and lifecycle state.

Lifecycle:  queued → running → completed | failed | canceled
"""
import datetime
import os
import queue
import tempfile
import threading
import uuid
from enum import Enum
from typing import Any, Dict, List, Optional


class ExecutionStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class Execution:
    def __init__(
        self,
        workspace_id: str,
        workspace_path: str,
        command: str,            # "plan" | "apply"
        env_vars: Dict[str, str],
        providers: List[str],
        backend: Optional[str] = None,
        plan_execution_id: Optional[str] = None,  # for apply → use existing plan
    ):
        self.id = str(uuid.uuid4())
        self.workspace_id = workspace_id
        self.workspace_path = workspace_path
        self.command = command
        self.env_vars = env_vars
        self.providers = providers
        self.backend = backend
        self.plan_execution_id = plan_execution_id

        # Sentinel
        self.sentinel_result: Optional[Dict[str, Any]] = None  # populated after plan
        self.sentinel_policies_override: Optional[str] = None  # ws-level extra policies

        self.timestamp = datetime.datetime.utcnow().isoformat()
        self.status = ExecutionStatus.QUEUED
        self.logs: List[str] = []
        self.plan_json: Optional[Dict[str, Any]] = None
        self.plan_binary_path: Optional[str] = None
        self.terraform_version: Optional[str] = None
        self.duration_seconds: Optional[int] = None
        self.terraform_binary: Optional[str] = None  # resolved path to tf binary
        self._workdir: Optional[str] = None  # temp dir for plan artefacts
        self._canceled = threading.Event()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def add_log(self, line: str) -> None:
        with self._lock:
            self.logs.append(line)

    def cancel(self) -> None:
        self._canceled.set()

    def is_canceled(self) -> bool:
        return self._canceled.is_set()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "workspace_id": self.workspace_id,
            "command": self.command,
            "timestamp": self.timestamp,
            "status": self.status.value,
            "providers": self.providers,
            "backend": self.backend,
            "terraform_version": self.terraform_version,
            "duration_seconds": self.duration_seconds,
            "log_lines": len(self.logs),
            "sentinel_result": self.sentinel_result,
        }

    @classmethod
    def from_metadata(cls, meta: Dict[str, Any]) -> "Execution":
        """Reconstruct a read-only Execution proxy from a stored metadata dict."""
        obj = cls.__new__(cls)
        obj.id = meta["id"]
        obj.workspace_id = meta["workspace_id"]
        obj.workspace_path = meta.get("workspace_path", "")
        obj.command = meta.get("command", "plan")
        obj.env_vars = {}
        obj.providers = meta.get("providers") or []
        obj.backend = meta.get("backend")
        obj.plan_execution_id = None
        obj.timestamp = meta.get("timestamp", "")
        obj.status = ExecutionStatus(meta.get("status", "completed"))
        obj.logs = []           # logs loaded on demand via backend
        obj.plan_json = None    # loaded on demand via backend
        obj.plan_binary_path = None
        obj.terraform_version = meta.get("terraform_version")
        obj.duration_seconds = meta.get("duration_seconds")
        obj.terraform_binary = meta.get("terraform_binary")
        obj.sentinel_result = meta.get("sentinel_result")
        obj.sentinel_policies_override = None
        obj._workdir = None
        obj._canceled = threading.Event()
        obj._lock = threading.Lock()
        obj._from_storage = True  # marker: this is a historical record
        return obj


# ---------------------------------------------------------------------------
# Queue manager
# ---------------------------------------------------------------------------

class ExecutionQueue:
    def __init__(self, max_workers: int = 3, socketio_instance=None):
        self.max_workers = max_workers
        self._socketio = socketio_instance
        self._queue: queue.Queue = queue.Queue()
        self._executions: Dict[str, Execution] = {}
        self._workers: List[threading.Thread] = []
        self._running = False
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        self._running = True
        for i in range(self.max_workers):
            t = threading.Thread(
                target=self._worker, name=f"tf-worker-{i}", daemon=True
            )
            t.start()
            self._workers.append(t)

    def stop(self) -> None:
        self._running = False
        for _ in self._workers:
            self._queue.put(None)  # poison pills

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def submit(self, execution: Execution) -> str:
        with self._lock:
            self._executions[execution.id] = execution
        self._queue.put(execution)
        return execution.id

    def get(self, execution_id: str) -> Optional[Execution]:
        # 1. Check in-memory first
        if execution_id in self._executions:
            return self._executions[execution_id]
        # 2. Fall back to storage
        try:
            from app.storage import get_backend
            backend = get_backend()
            meta = backend.get_execution_by_id(execution_id)
            if meta:
                return Execution.from_metadata(meta)
        except Exception:
            pass
        return None

    def list_all(self) -> List[Execution]:
        with self._lock:
            return list(self._executions.values())

    def list_for_workspace(self, workspace_id: str) -> List[Execution]:
        # In-memory runs (running/queued/recent)
        with self._lock:
            in_memory = {e.id: e for e in self._executions.values()
                         if e.workspace_id == workspace_id}
        # Historical runs from storage
        try:
            from app.storage import get_backend
            backend = get_backend()
            for meta in backend.list_executions(workspace_id):
                eid = meta.get("id")
                if eid and eid not in in_memory:
                    in_memory[eid] = Execution.from_metadata(meta)
        except Exception:
            pass
        return list(in_memory.values())

    def cancel(self, execution_id: str) -> bool:
        execution = self._executions.get(execution_id)
        if execution:
            execution.cancel()
            if execution.status == ExecutionStatus.QUEUED:
                execution.status = ExecutionStatus.CANCELED
            return True
        return False

    # ------------------------------------------------------------------
    # Worker thread
    # ------------------------------------------------------------------

    def _worker(self) -> None:
        while self._running:
            try:
                execution = self._queue.get(timeout=1)
                if execution is None:
                    break
                if execution.is_canceled():
                    execution.status = ExecutionStatus.CANCELED
                    self._emit_status(execution)
                    continue
                self._run_execution(execution)
            except queue.Empty:
                continue
            except Exception:
                pass

    def _run_execution(self, execution: Execution) -> None:
        from app.terraform_runner import TerraformRunner

        execution.status = ExecutionStatus.RUNNING
        self._emit_status(execution)

        start = datetime.datetime.utcnow()
        workdir = tempfile.mkdtemp(prefix=f"tgm-{execution.id[:8]}-")
        execution._workdir = workdir

        def log(line: str) -> None:
            execution.add_log(line)
            self._emit_log(execution, line)

        try:
            runner = TerraformRunner(
                execution.workspace_path, execution.env_vars, execution.terraform_binary
            )

            # Detect terraform version
            execution.terraform_version = runner.version()

            # 1 — Init
            log("=== terraform init ===")
            ok = runner.init(log)
            if not ok or execution.is_canceled():
                raise RuntimeError("terraform init failed or execution was canceled")

            if execution.command == "plan":
                self._do_plan(runner, execution, workdir, log)

            elif execution.command == "apply":
                self._do_apply(runner, execution, workdir, log)

            # Sentinel check (after plan JSON is available)
            from flask import current_app
            try:
                app_config = current_app.config["TFG_CONFIG"]
            except RuntimeError:
                app_config = None
            self._run_sentinel(execution, app_config, log)

            if execution.is_canceled():
                execution.status = ExecutionStatus.CANCELED
            else:
                execution.status = ExecutionStatus.COMPLETED

        except Exception as exc:
            log(f"EXECUTION ERROR: {exc}")
            execution.status = ExecutionStatus.FAILED

        finally:
            end = datetime.datetime.utcnow()
            execution.duration_seconds = int((end - start).total_seconds())
            self._emit_status(execution)
            # Store BEFORE cleaning up workdir so plan binary is still available
            self._store_execution(execution)
            import shutil
            try:
                shutil.rmtree(workdir, ignore_errors=True)
            except Exception:
                pass

    # ------------------------------------------------------------------

    def _do_plan(
        self,
        runner,
        execution: Execution,
        workdir: str,
        log,
    ) -> None:
        plan_binary = os.path.join(workdir, "tfplan.binary")

        log("=== terraform plan ===")
        ok = runner.plan(log, plan_binary_path=plan_binary)
        if not ok:
            raise RuntimeError("terraform plan failed")

        log("=== terraform show -json ===")
        plan_json = runner.show_json(plan_binary, log)
        execution.plan_json = plan_json
        execution.plan_binary_path = plan_binary

    # ------------------------------------------------------------------

    def _run_sentinel(
        self,
        execution: Execution,
        app_config,
        log,
    ) -> None:
        """Run Sentinel policy checks if configured and plan JSON is available."""
        if app_config is None:
            return
        if not execution.plan_json:
            return

        enforce_plan = getattr(app_config, "sentinel_enforce_on_plan", False)
        enforce_apply = getattr(app_config, "sentinel_enforce_on_apply", False)

        # Only run after plan (and apply if enforce_on_apply is set)
        should_run = False
        if execution.command == "plan" and enforce_plan:
            should_run = True
        elif execution.command == "apply" and (enforce_plan or enforce_apply):
            should_run = True
        # Always run if the execution has a workspace-level override
        if execution.sentinel_policies_override:
            should_run = True

        if not should_run:
            return

        from app.sentinel_runner import SentinelRunner, get_sentinel_binary, sentinel_available
        cli_path = getattr(app_config, "sentinel_cli_path", "")
        binary = get_sentinel_binary(cli_path)

        if not sentinel_available(cli_path):
            log("[Sentinel] WARNING: sentinel binary not found — skipping checks.")
            return

        global_policies = getattr(app_config, "sentinel_global_policies", "")
        sentinel = SentinelRunner(
            sentinel_binary=binary,
            global_policies_path=global_policies or None,
            workspace_extra_policies=execution.sentinel_policies_override or None,
        )

        log("=== sentinel check ===")
        sentinel_result = sentinel.check_plan(
            execution.plan_json,
            log_cb=log,
        )
        execution.sentinel_result = sentinel_result

        if not sentinel_result["passed"]:
            enforce_apply_flag = getattr(app_config, "sentinel_enforce_on_apply", False)
            if execution.command == "apply" and enforce_apply_flag:
                raise RuntimeError(
                    "Sentinel policy check failed — apply blocked. "
                    "Review policy violations above."
                )

    def _do_apply(
        self,
        runner,
        execution: Execution,
        workdir: str,
        log,
    ) -> None:
        # If an upstream plan execution is referenced, check it
        plan_binary: Optional[str] = None

        if execution.plan_execution_id:
            plan_exec = self._executions.get(execution.plan_execution_id)
            if plan_exec and plan_exec.plan_binary_path and os.path.isfile(
                plan_exec.plan_binary_path
            ):
                plan_binary = plan_exec.plan_binary_path

        if plan_binary is None:
            # No saved plan → do fresh init + plan first
            log("=== terraform plan (for apply) ===")
            fresh_binary = os.path.join(workdir, "tfplan.binary")
            ok = runner.plan(log, plan_binary_path=fresh_binary)
            if not ok:
                raise RuntimeError("terraform plan (pre-apply) failed")
            plan_binary = fresh_binary

        log("=== terraform apply ===")
        ok = runner.apply(log, plan_binary_path=plan_binary)
        if not ok:
            raise RuntimeError("terraform apply failed")

    # ------------------------------------------------------------------
    # Socket.IO emission
    # ------------------------------------------------------------------

    def _emit_log(self, execution: Execution, line: str) -> None:
        if self._socketio:
            try:
                self._socketio.emit(
                    "execution_log",
                    {"execution_id": execution.id, "line": line},
                )
            except Exception:
                pass

    def _emit_status(self, execution: Execution) -> None:
        if self._socketio:
            try:
                self._socketio.emit(
                    "execution_status",
                    {"execution_id": execution.id, "status": execution.status.value},
                )
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Cloud storage
    # ------------------------------------------------------------------

    def _store_execution(self, execution: Execution) -> None:
        try:
            from app.storage import get_backend
            backend = get_backend()
            backend.store_execution(execution)
        except Exception as exc:
            # Log to stderr so it's visible in the server console
            import traceback
            import sys
            print(
                f"[TGM] WARNING: could not store execution {execution.id}: {exc}",
                file=sys.stderr,
            )
            traceback.print_exc(file=sys.stderr)
