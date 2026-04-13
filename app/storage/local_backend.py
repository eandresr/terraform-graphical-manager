"""
Local Filesystem Storage Backend — mirrors the cloud backend interface but
stores execution artefacts in a local directory.

The root directory defaults to a folder named 'TERRAFORM_GRAPHICAL_BACKEND'
inside the project working directory.  Override with:

    TERRAFORM_GRAPHICAL_BACKEND_LOCAL_PATH=/path/to/storage

Directory layout (identical to the cloud backends for easy migration):

    <root>/
    └── workspaces/
        └── {workspace_id}/
            └── runs/
                └── {timestamp}/
                    ├── metadata.json
                    ├── plan.log  |  apply.log
                    ├── plan.json
                    └── tfplan.binary
"""
import json
import os
import shutil
from typing import Any, Dict, List, Optional


class LocalBackend:
    def __init__(self):
        self._root = os.environ.get(
            "TERRAFORM_GRAPHICAL_BACKEND_LOCAL_PATH",
            os.path.join(os.getcwd(), "TERRAFORM_GRAPHICAL_BACKEND"),
        )
        os.makedirs(self._root, exist_ok=True)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def store_execution(self, execution) -> None:
        """Persist metadata, logs, and plan artefacts for an execution."""
        prefix = self._execution_prefix(execution.workspace_id, execution.timestamp)
        os.makedirs(prefix, exist_ok=True)

        # metadata.json
        self._write_json(os.path.join(prefix, "metadata.json"),
                         self._build_metadata(execution))

        # logs
        log_text = "\n".join(execution.logs)
        log_name = "plan.log" if execution.command == "plan" else "apply.log"
        self._write_text(os.path.join(prefix, log_name), log_text)

        # plan.json
        if execution.plan_json:
            self._write_json(os.path.join(prefix, "plan.json"), execution.plan_json)

        # tfplan.binary
        if execution.plan_binary_path and os.path.isfile(execution.plan_binary_path):
            shutil.copy2(execution.plan_binary_path, os.path.join(prefix, "tfplan.binary"))

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def list_executions(self, workspace_id: str) -> List[Dict[str, Any]]:
        """Return a list of metadata dicts for all runs of a workspace."""
        runs_dir = os.path.join(self._root, "workspaces", workspace_id, "runs")
        results: List[Dict[str, Any]] = []
        if not os.path.isdir(runs_dir):
            return results
        for entry in os.scandir(runs_dir):
            if not entry.is_dir():
                continue
            meta_path = os.path.join(entry.path, "metadata.json")
            meta = self._read_json(meta_path)
            if meta:
                results.append(meta)
        return sorted(results, key=lambda m: m.get("timestamp", ""), reverse=True)

    def list_all_executions(self) -> List[Dict[str, Any]]:
        """Return metadata for every run across all workspaces."""
        ws_root = os.path.join(self._root, "workspaces")
        results: List[Dict[str, Any]] = []
        if not os.path.isdir(ws_root):
            return results
        for ws_entry in os.scandir(ws_root):
            if not ws_entry.is_dir():
                continue
            runs_dir = os.path.join(ws_entry.path, "runs")
            if not os.path.isdir(runs_dir):
                continue
            for run_entry in os.scandir(runs_dir):
                if not run_entry.is_dir():
                    continue
                meta = self._read_json(os.path.join(run_entry.path, "metadata.json"))
                if meta:
                    results.append(meta)
        return sorted(results, key=lambda m: m.get("timestamp", ""), reverse=True)

    def get_execution(self, workspace_id: str, timestamp: str) -> Optional[Dict[str, Any]]:
        prefix = self._execution_prefix(workspace_id, timestamp)
        return self._read_json(os.path.join(prefix, "metadata.json"))

    def get_execution_by_id(self, execution_id: str) -> Optional[Dict[str, Any]]:
        """Scan all stored workspaces to find a run by its UUID."""
        run_dir = self._find_run_dir(execution_id)
        if run_dir is None:
            return None
        return self._read_json(os.path.join(run_dir, "metadata.json"))

    def get_logs(self, workspace_id: str, timestamp: str, command: str) -> Optional[str]:
        prefix = self._execution_prefix(workspace_id, timestamp)
        log_name = "plan.log" if command == "plan" else "apply.log"
        path = os.path.join(prefix, log_name)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            return None

    def get_logs_by_id(self, execution_id: str) -> Optional[str]:
        """Return logs for a run identified by UUID (tries both plan.log and apply.log)."""
        run_dir = self._find_run_dir(execution_id)
        if run_dir is None:
            return None
        for log_name in ("plan.log", "apply.log"):
            path = os.path.join(run_dir, log_name)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    return fh.read()
            except OSError:
                continue
        return None

    def get_plan_json(self, workspace_id: str, timestamp: str) -> Optional[Dict]:
        prefix = self._execution_prefix(workspace_id, timestamp)
        return self._read_json(os.path.join(prefix, "plan.json"))

    def get_plan_json_by_id(self, execution_id: str) -> Optional[Dict]:
        """Return plan.json for a run identified by UUID."""
        run_dir = self._find_run_dir(execution_id)
        if run_dir is None:
            return None
        return self._read_json(os.path.join(run_dir, "plan.json"))

    # ------------------------------------------------------------------
    # Per-workspace config (terraform version pin, etc.)
    # ------------------------------------------------------------------

    def get_workspace_config(self, workspace_id: str) -> Dict[str, Any]:
        path = os.path.join(self._root, "workspaces", workspace_id, "workspace_config.json")
        return self._read_json(path) or {}

    def set_workspace_config(self, workspace_id: str, config: Dict[str, Any]) -> None:
        ws_dir = os.path.join(self._root, "workspaces", workspace_id)
        os.makedirs(ws_dir, exist_ok=True)
        self._write_json(os.path.join(ws_dir, "workspace_config.json"), config)

    # ------------------------------------------------------------------
    # Per-workspace execution lock
    # ------------------------------------------------------------------

    def get_execution_lock(self, workspace_id: str) -> Optional[Dict[str, Any]]:
        path = os.path.join(self._root, "workspaces", workspace_id, "execution_lock.json")
        return self._read_json(path)

    def set_execution_lock(self, workspace_id: str, lock_data: Dict[str, Any]) -> None:
        ws_dir = os.path.join(self._root, "workspaces", workspace_id)
        os.makedirs(ws_dir, exist_ok=True)
        self._write_json(os.path.join(ws_dir, "execution_lock.json"), lock_data)

    def clear_execution_lock(self, workspace_id: str) -> None:
        path = os.path.join(self._root, "workspaces", workspace_id, "execution_lock.json")
        try:
            os.remove(path)
        except OSError:
            pass

    # ------------------------------------------------------------------
    # Per-workspace Sentinel last result
    # ------------------------------------------------------------------

    def get_sentinel_last_result(self, workspace_id: str) -> Optional[Dict[str, Any]]:
        path = os.path.join(self._root, "workspaces", workspace_id, "sentinel_last_result.json")
        return self._read_json(path)

    def set_sentinel_last_result(self, workspace_id: str, data: Dict[str, Any]) -> None:
        ws_dir = os.path.join(self._root, "workspaces", workspace_id)
        os.makedirs(ws_dir, exist_ok=True)
        self._write_json(os.path.join(ws_dir, "sentinel_last_result.json"), data)

    # ------------------------------------------------------------------
    # Resource history (created/modified per run)
    # ------------------------------------------------------------------

    def get_resource_history(self, workspace_id: str) -> Dict[str, Any]:
        path = os.path.join(self._root, "workspaces", workspace_id, "resource_history.json")
        return self._read_json(path) or {}

    def set_resource_history(self, workspace_id: str, data: Dict[str, Any]) -> None:
        ws_dir = os.path.join(self._root, "workspaces", workspace_id)
        os.makedirs(ws_dir, exist_ok=True)
        self._write_json(os.path.join(ws_dir, "resource_history.json"), data)

    # ------------------------------------------------------------------
    # Variable Groups
    # ------------------------------------------------------------------

    def list_variable_groups(self) -> List[Dict[str, Any]]:
        """Return all variable groups sorted by name."""
        vg_dir = os.path.join(self._root, "variable_groups")
        results: List[Dict[str, Any]] = []
        if not os.path.isdir(vg_dir):
            return results
        for entry in os.scandir(vg_dir):
            if entry.is_file() and entry.name.endswith(".json"):
                data = self._read_json(entry.path)
                if data:
                    results.append(data)
        return sorted(results, key=lambda g: (g.get("name") or "").lower())

    def get_variable_group(self, group_id: str) -> Optional[Dict[str, Any]]:
        path = os.path.join(self._root, "variable_groups", f"{group_id}.json")
        return self._read_json(path)

    def save_variable_group(self, group_id: str, data: Dict[str, Any]) -> None:
        vg_dir = os.path.join(self._root, "variable_groups")
        os.makedirs(vg_dir, exist_ok=True)
        self._write_json(os.path.join(vg_dir, f"{group_id}.json"), data)

    def delete_variable_group(self, group_id: str) -> None:
        path = os.path.join(self._root, "variable_groups", f"{group_id}.json")
        try:
            os.remove(path)
        except OSError:
            pass

    # ------------------------------------------------------------------
    # Notification Channels
    # ------------------------------------------------------------------

    def list_notification_channels(self) -> List[Dict[str, Any]]:
        nc_dir = os.path.join(self._root, "notification_channels")
        results: List[Dict[str, Any]] = []
        if not os.path.isdir(nc_dir):
            return results
        for entry in os.scandir(nc_dir):
            if entry.is_file() and entry.name.endswith(".json"):
                data = self._read_json(entry.path)
                if data:
                    results.append(data)
        return sorted(results, key=lambda c: (c.get("name") or "").lower())

    def get_notification_channel(self, channel_id: str) -> Optional[Dict[str, Any]]:
        path = os.path.join(self._root, "notification_channels", f"{channel_id}.json")
        return self._read_json(path)

    def save_notification_channel(
        self, channel_id: str, data: Dict[str, Any]
    ) -> None:
        nc_dir = os.path.join(self._root, "notification_channels")
        os.makedirs(nc_dir, exist_ok=True)
        self._write_json(os.path.join(nc_dir, f"{channel_id}.json"), data)

    def delete_notification_channel(self, channel_id: str) -> None:
        path = os.path.join(
            self._root, "notification_channels", f"{channel_id}.json"
        )
        try:
            os.remove(path)
        except OSError:
            pass

    # ------------------------------------------------------------------
    # Workspace Workflows  (stored inside workspace_config.json)
    # ------------------------------------------------------------------

    def list_workspace_workflows(self, workspace_id: str) -> List[Dict[str, Any]]:
        """Return all workflow definitions for a workspace."""
        cfg = self.get_workspace_config(workspace_id)
        return list(cfg.get("workflows") or [])

    def save_workspace_workflow(
        self, workspace_id: str, workflow: Dict[str, Any]
    ) -> None:
        """Upsert a single workflow in the workspace config."""
        cfg = self.get_workspace_config(workspace_id)
        workflows: List[Dict[str, Any]] = list(cfg.get("workflows") or [])
        wf_id = workflow.get("id")
        for i, wf in enumerate(workflows):
            if wf.get("id") == wf_id:
                workflows[i] = workflow
                break
        else:
            workflows.append(workflow)
        cfg["workflows"] = workflows
        self.set_workspace_config(workspace_id, cfg)

    def delete_workspace_workflow(
        self, workspace_id: str, workflow_id: str
    ) -> None:
        """Remove a workflow from the workspace config."""
        cfg = self.get_workspace_config(workspace_id)
        workflows = [
            wf for wf in (cfg.get("workflows") or [])
            if wf.get("id") != workflow_id
        ]
        cfg["workflows"] = workflows
        self.set_workspace_config(workspace_id, cfg)

    def patch_execution_workflow_results(
        self,
        workspace_id: str,
        execution_id: str,
        results: List[Dict[str, Any]],
    ) -> None:
        """Append workflow execution results to the run's metadata.json."""
        run_dir = self._find_run_dir(execution_id)
        if run_dir is None:
            return
        path = os.path.join(run_dir, "metadata.json")
        meta = self._read_json(path) or {}
        meta["workflow_results"] = results
        self._write_json(path, meta)

    def _find_run_dir(self, execution_id: str) -> Optional[str]:
        """Locate the run directory for a given execution UUID."""
        ws_root = os.path.join(self._root, "workspaces")
        if not os.path.isdir(ws_root):
            return None
        for ws_entry in os.scandir(ws_root):
            if not ws_entry.is_dir():
                continue
            runs_dir = os.path.join(ws_entry.path, "runs")
            if not os.path.isdir(runs_dir):
                continue
            for run_entry in os.scandir(runs_dir):
                if not run_entry.is_dir():
                    continue
                meta = self._read_json(os.path.join(run_entry.path, "metadata.json"))
                if meta and meta.get("id") == execution_id:
                    return run_entry.path
        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _execution_prefix(self, workspace_id: str, timestamp: str) -> str:
        safe_ts = timestamp.replace(":", "-").replace(" ", "_")
        return os.path.join(self._root, "workspaces", workspace_id, "runs", safe_ts)

    @staticmethod
    def _build_metadata(execution) -> Dict[str, Any]:
        # Compute resource counts from plan_json when available.
        resource_counts = None
        plan_json = getattr(execution, "plan_json", None)
        if plan_json:
            try:
                from app.plan_parser import parse_plan
                resource_counts = parse_plan(plan_json).get("counts")
            except Exception:
                pass

        return {
            "id": execution.id,
            "workspace_id": execution.workspace_id,
            "workspace_path": execution.workspace_path,
            "command": execution.command,
            "status": execution.status.value,
            "timestamp": execution.timestamp,
            "providers": execution.providers,
            "backend": execution.backend,
            "terraform_version": execution.terraform_version,
            "duration_seconds": execution.duration_seconds,
            "sentinel_result": getattr(execution, "sentinel_result", None),
            "run_params": getattr(execution, "run_params", []),
            "resource_counts": resource_counts,
            "state_resource_count": getattr(execution, "state_resource_count", None),
            "git_ref": getattr(execution, "git_ref", None),
            "git_pull": getattr(execution, "git_pull", False),
            "workflow_results": getattr(execution, "workflow_results", []) or [],
        }

    @staticmethod
    def _write_json(path: str, data: Any) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)

    @staticmethod
    def _write_text(path: str, text: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

    @staticmethod
    def _read_json(path: str) -> Optional[Any]:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError):
            return None
