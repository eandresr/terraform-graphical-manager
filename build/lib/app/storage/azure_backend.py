"""
Azure Blob Storage Backend — stores execution metadata and logs in an Azure
Blob Storage container.

Required environment variables:
    TERRAFORM_GRAPHICAL_BACKEND_CONTAINER
    TERRAFORM_GRAPHICAL_BACKEND_CONNECTION_STRING
"""
import json
import os
from typing import Any, Dict, List, Optional

from azure.storage.blob import BlobServiceClient, ContentSettings
from azure.core.exceptions import AzureError


class AzureBackend:
    def __init__(self):
        connection_string = os.environ["TERRAFORM_GRAPHICAL_BACKEND_CONNECTION_STRING"]
        self._container_name = os.environ["TERRAFORM_GRAPHICAL_BACKEND_CONTAINER"]
        self._client = BlobServiceClient.from_connection_string(connection_string)
        self._container = self._client.get_container_client(self._container_name)
        # Create container if it does not exist
        try:
            self._container.create_container()
        except AzureError:
            pass

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def store_execution(self, execution) -> None:
        prefix = self._execution_prefix(execution.workspace_id, execution.timestamp)

        metadata = self._build_metadata(execution)
        self._put_json(f"{prefix}metadata.json", metadata)

        log_text = "\n".join(execution.logs)
        if execution.command == "plan":
            self._put_text(f"{prefix}plan.log", log_text)
        else:
            self._put_text(f"{prefix}apply.log", log_text)

        if execution.plan_json:
            self._put_json(f"{prefix}plan.json", execution.plan_json)

        if execution.plan_binary_path and os.path.isfile(execution.plan_binary_path):
            blob_client = self._container.get_blob_client(f"{prefix}tfplan.binary")
            with open(execution.plan_binary_path, "rb") as fh:
                blob_client.upload_blob(fh, overwrite=True)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def list_executions(self, workspace_id: str) -> List[Dict[str, Any]]:
        prefix = f"workspaces/{workspace_id}/runs/"
        results: List[Dict[str, Any]] = []
        seen_prefixes: set = set()
        try:
            for blob in self._container.list_blobs(name_starts_with=prefix):
                # Extract the run timestamp prefix
                rest = blob.name[len(prefix):]
                run_ts = rest.split("/")[0]
                run_prefix = f"{prefix}{run_ts}/"
                if run_prefix not in seen_prefixes:
                    seen_prefixes.add(run_prefix)
                    meta = self._get_json(f"{run_prefix}metadata.json")
                    if meta:
                        results.append(meta)
        except AzureError:
            pass
        return sorted(results, key=lambda m: m.get("timestamp", ""), reverse=True)

    def get_plan_json(self, workspace_id: str, timestamp: str) -> Optional[Dict]:
        key = f"{self._execution_prefix(workspace_id, timestamp)}plan.json"
        return self._get_json(key)

    def get_logs(self, workspace_id: str, timestamp: str, log_type: str = "plan") -> str:
        key = f"{self._execution_prefix(workspace_id, timestamp)}{log_type}.log"
        return self._get_text(key) or ""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _execution_prefix(workspace_id: str, timestamp: str) -> str:
        safe_ts = timestamp.replace(":", "-").replace(".", "-")
        return f"workspaces/{workspace_id}/runs/{safe_ts}/"

    @staticmethod
    def _build_metadata(execution) -> Dict[str, Any]:
        return {
            "workspace": execution.workspace_id,
            "timestamp": execution.timestamp,
            "command": execution.command,
            "status": execution.status.value,
            "duration_seconds": execution.duration_seconds,
            "providers": execution.providers,
            "backend": execution.backend,
            "terraform_version": execution.terraform_version,
        }

    def _put_json(self, key: str, data: Dict) -> None:
        try:
            blob_client = self._container.get_blob_client(key)
            blob_client.upload_blob(
                json.dumps(data, indent=2, default=str).encode(),
                overwrite=True,
                content_settings=ContentSettings(content_type="application/json"),
            )
        except AzureError:
            pass

    def _put_text(self, key: str, text: str) -> None:
        try:
            blob_client = self._container.get_blob_client(key)
            blob_client.upload_blob(
                text.encode(),
                overwrite=True,
                content_settings=ContentSettings(content_type="text/plain"),
            )
        except AzureError:
            pass

    def _get_json(self, key: str) -> Optional[Dict]:
        try:
            blob_client = self._container.get_blob_client(key)
            data = blob_client.download_blob().readall()
            return json.loads(data)
        except (AzureError, json.JSONDecodeError):
            return None

    def _get_text(self, key: str) -> Optional[str]:
        try:
            blob_client = self._container.get_blob_client(key)
            return blob_client.download_blob().readall().decode()
        except AzureError:
            return None
