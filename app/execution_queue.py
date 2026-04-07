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

        # Sensitive variable values (plaintext) — used for log masking only;
        # never persisted to storage.
        self.sensitive_values: List[str] = []

        # Structured variable parameters recorded at run submission time.
        self.run_params: List[Dict[str, Any]] = []

        # Git integration
        self.git_pull: bool = False
        self.git_ref: Optional[str] = None   # e.g. "git-branch:main"
        self.enc_key: str = ""               # enc key for token lookups in worker threads
        self.repos_root: str = ""            # boundary for git repo detection

        self.timestamp = datetime.datetime.utcnow().isoformat()
        self.status = ExecutionStatus.QUEUED
        self.logs: List[str] = []
        self.plan_json: Optional[Dict[str, Any]] = None
        self.plan_binary_path: Optional[str] = None
        self.terraform_version: Optional[str] = None
        self.duration_seconds: Optional[int] = None
        self.state_resource_count: Optional[int] = None  # resource count from state after apply
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

    def mask(self, line: str) -> str:
        """Replace each sensitive value with *** in *line*."""
        for val in self.sensitive_values:
            if val and val in line:
                line = line.replace(val, "***")
        return line

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
            "run_params": self.run_params,
            "git_ref": self.git_ref,
            "git_pull": getattr(self, "git_pull", False),
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
        obj.state_resource_count = meta.get("state_resource_count")
        obj.terraform_binary = meta.get("terraform_binary")
        obj.sentinel_result = meta.get("sentinel_result")
        obj.run_params = meta.get("run_params") or []
        obj.sentinel_policies_override = None
        obj.sensitive_values = []
        obj.git_pull = meta.get("git_pull", False)
        obj.git_ref = meta.get("git_ref")
        obj.enc_key = ""
        obj._workdir = None
        obj._canceled = threading.Event()
        obj._lock = threading.Lock()
        obj._from_storage = True  # marker: this is a historical record
        return obj


# ---------------------------------------------------------------------------
# Queue manager
# ---------------------------------------------------------------------------

class ExecutionQueue:
    def __init__(self, max_workers: int = 3, socketio_instance=None, flask_app=None):
        self.max_workers = max_workers
        self._socketio = socketio_instance
        self._flask_app = flask_app  # needed to push app context in worker threads
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

    def get(self, execution_id: str, enc_key: str = "") -> Optional[Execution]:
        # 1. Check in-memory first
        if execution_id in self._executions:
            return self._executions[execution_id]
        # 2. Fall back to storage
        try:
            from app.storage import get_backend
            backend = get_backend(enc_key)
            meta = backend.get_execution_by_id(execution_id)
            if meta:
                return Execution.from_metadata(meta)
        except Exception:
            pass
        return None

    def list_all(self) -> List[Execution]:
        with self._lock:
            return list(self._executions.values())

    def list_for_workspace(self, workspace_id: str, enc_key: str = "") -> List[Execution]:
        # In-memory runs (running/queued/recent)
        with self._lock:
            in_memory = {e.id: e for e in self._executions.values()
                         if e.workspace_id == workspace_id}
        # Historical runs from storage
        try:
            from app.storage import get_backend
            backend = get_backend(enc_key)
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
        # Push a Flask application context for the lifetime of this worker thread
        # so that current_app, g, and (read-only) config are accessible.
        # flask.session is NOT available here (no request context) — that is why
        # enc_key is carried explicitly on the Execution object.
        if self._flask_app is not None:
            ctx = self._flask_app.app_context()
            ctx.push()
        else:
            ctx = None
        try:
            self._worker_loop()
        finally:
            if ctx is not None:
                ctx.pop()

    def _worker_loop(self) -> None:
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

        # Persist an execution lock so other sessions can detect an active run.
        try:
            from app.storage import get_backend as _get_backend
            _get_backend(execution.enc_key).set_execution_lock(
                execution.workspace_id,
                {
                    "execution_id": execution.id,
                    "command": execution.command,
                    "started_at": datetime.datetime.utcnow().isoformat(),
                    "workspace_id": execution.workspace_id,
                },
            )
        except Exception:
            pass

        start = datetime.datetime.utcnow()
        workdir = tempfile.mkdtemp(prefix=f"tgm-{execution.id[:8]}-")
        execution._workdir = workdir

        def log(line: str) -> None:
            line = execution.mask(line)
            execution.add_log(line)
            self._emit_log(execution, line)

        try:
            ts = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
            log(f"=== Run started: {ts}  |  command: {execution.command} ===")

            # Git: optionally pull and record current ref label
            self._handle_git(execution, log)

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
                _before = runner.state_pull() or {}
                from app.resource_tracker import build_snapshot
                snap_before = build_snapshot(_before)
                self._do_apply(runner, execution, workdir, log)
                _after = runner.state_pull() or {}
                snap_after = build_snapshot(_after)
                self._track_changes(execution, snap_before, snap_after)
                try:
                    from app.state_parser import parse_state as _parse_state
                    execution.state_resource_count = _parse_state(_after).get("resource_count")
                except Exception:
                    pass

            elif execution.command == "destroy":
                _before = runner.state_pull() or {}
                from app.resource_tracker import build_snapshot
                snap_before = build_snapshot(_before)
                log("=== terraform destroy ===")
                ok = runner.destroy(log)
                if not ok:
                    raise RuntimeError("terraform destroy failed")
                _after = runner.state_pull() or {}
                snap_after = build_snapshot(_after)
                self._track_changes(execution, snap_before, snap_after)

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
            # Update the lightweight last-state cache used by the dashboard.
            try:
                from app.workspace_state import update as _update_ws_state
                d = execution.to_dict()
                d["workspace_path"] = execution.workspace_path
                _update_ws_state(execution.workspace_id, d)
            except Exception:
                pass
            # Export metrics to the configured backend (best-effort, never breaks run)
            try:
                from flask import current_app
                from app.metrics_exporter import export_execution_metrics
                _cfg = current_app.config.get("TFG_CONFIG")
                if _cfg:
                    _ws_name = (
                        execution.workspace_path.rstrip("/").split("/")[-1]
                    )
                    export_execution_metrics(
                        execution.to_dict(),
                        _cfg,
                        _ws_name,
                    )
            except Exception:
                pass
            # Send notifications (best-effort, never breaks run)
            try:
                from app.notification_manager import send_notifications_for_execution
                _ws_name_n = execution.workspace_path.rstrip("/").split("/")[-1]
                send_notifications_for_execution(
                    execution.to_dict(),
                    _ws_name_n,
                )
            except Exception:
                pass
            # Release the execution lock now that the run has finished.
            try:
                from app.storage import get_backend as _get_backend
                _get_backend(execution.enc_key).clear_execution_lock(execution.workspace_id)
            except Exception:
                pass
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

        # Capture current managed-resource count from state so the resource
        # chart has a data point for every run, not just apply runs.
        try:
            from app.state_parser import parse_state as _parse_state
            _state = runner.state_pull() or {}
            execution.state_resource_count = _parse_state(_state).get("resource_count")
        except Exception:
            pass

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

    def _handle_git(self, execution: Execution, log) -> None:
        """Optionally git pull and record the current ref label on the execution."""
        try:
            from app.git_manager import (
                is_git_repo, list_refs,
                pull as git_pull_fn,
                get_token_for_workspace,
            )
            if not is_git_repo(execution.workspace_path, execution.repos_root):
                return
            refs = list_refs(execution.workspace_path)
            cur = refs.get("current", {})
            ref_name = cur.get("name", "")
            ref_type = cur.get("type", "branch")
            label = f"git-{ref_type}:{ref_name}"
            if execution.git_pull:
                log(f"=== git pull  [{label}] ===")
                token = get_token_for_workspace(
                    execution.workspace_id, execution.enc_key
                )
                result = git_pull_fn(execution.workspace_path, token)
                if result.get("ok"):
                    log("[git] Pull complete.")
                else:
                    log(f"[git] Pull failed: {result.get('output', '').strip()}")
            else:
                label += " (local)"
                log(f"=== git: using local code  [{label}] ===")
            execution.git_ref = label
        except Exception as exc:
            log(f"[git] Warning: could not process git state: {exc}")

    def _track_changes(self, execution: Execution, snap_before, snap_after) -> None:
        """Diff state snapshots and persist resource history."""
        try:
            from app.resource_tracker import diff_snapshots, record_run_changes
            changes = diff_snapshots(snap_before, snap_after)
            record_run_changes(
                execution.workspace_id,
                execution.id,
                execution.timestamp,
                changes,
            )
        except Exception:
            pass

    def _store_execution(self, execution: Execution) -> None:
        try:
            from app.storage import get_backend
            # Pass enc_key explicitly: this method runs in a background thread
            # with no Flask request context, so session is not available.
            backend = get_backend(execution.enc_key)
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
