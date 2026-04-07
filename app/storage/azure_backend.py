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


def _get_azure_creds(enc_key: str = "") -> dict:
    """Resolve Azure credentials: env vars take precedence over stored config.

    *enc_key* is the plaintext portal password.  Falls back to
    ``flask.session["tgm_enc_key"]`` when inside a request context.
    """
    if os.environ.get("TERRAFORM_GRAPHICAL_BACKEND_CONNECTION_STRING"):
        return {
            "_connection_string": os.environ["TERRAFORM_GRAPHICAL_BACKEND_CONNECTION_STRING"],
            "container": os.environ.get("TERRAFORM_GRAPHICAL_BACKEND_CONTAINER", ""),
            "prefix": "",
        }
    try:
        from flask import current_app
        cfg = current_app.config["TFG_CONFIG"]
        from app.backend_config import get_backend_config, decrypt_fields
        bc = get_backend_config(cfg)
        key = enc_key
        if not key:
            try:
                from flask import session
                key = session.get("tgm_enc_key", "")
            except RuntimeError:
                key = ""
        if key:
            bc = decrypt_fields(bc, "azure", key)
        return bc
    except Exception:
        return {}


class AzureBackend:
    def __init__(self, enc_key: str = ""):
        creds = _get_azure_creds(enc_key)

        container_name = creds.get("container") or ""
        if not container_name:
            raise RuntimeError(
                "Azure backend: container not configured. "
                "Set TERRAFORM_GRAPHICAL_BACKEND_CONTAINER "
                "or configure the backend via the Settings page."
            )
        self._container_name = container_name
        self._prefix = (creds.get("prefix") or "").strip().strip("/")
        if self._prefix:
            self._prefix += "/"

        # Legacy: connection string (env var path)
        conn_str = creds.get("_connection_string") or ""
        if conn_str:
            self._client = BlobServiceClient.from_connection_string(conn_str)
        else:
            # UI-configured path: service principal
            from azure.identity import ClientSecretCredential
            storage_account = creds.get("azure_storage_account") or ""
            if not storage_account:
                raise RuntimeError("Azure backend: storage account name is required.")
            credential = ClientSecretCredential(
                tenant_id=creds.get("azure_tenant_id", ""),
                client_id=creds.get("azure_client_id", ""),
                client_secret=creds.get("azure_client_secret", ""),
            )
            account_url = f"https://{storage_account}.blob.core.windows.net"
            self._client = BlobServiceClient(account_url=account_url, credential=credential)

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
        prefix = f"{self._prefix}workspaces/{workspace_id}/runs/"
        results: List[Dict[str, Any]] = []
        seen_prefixes: set = set()
        try:
            for blob in self._container.list_blobs(name_starts_with=prefix):
                # Extract the run timestamp prefix (strip the base prefix first)
                rel = blob.name[len(self._prefix):]  # strip configured prefix
                rest = rel[len(f"workspaces/{workspace_id}/runs/"):]
                run_ts = rest.split("/")[0]
                run_prefix = f"{self._prefix}workspaces/{workspace_id}/runs/{run_ts}/"
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

    def list_all_executions(self) -> List[Dict[str, Any]]:
        prefix = f"{self._prefix}workspaces/"
        results: List[Dict[str, Any]] = []
        try:
            for blob in self._container.list_blobs(name_starts_with=prefix):
                if blob.name.endswith("/metadata.json") and "/runs/" in blob.name:
                    meta = self._get_json(blob.name)
                    if meta:
                        results.append(meta)
        except AzureError:
            pass
        return sorted(results, key=lambda m: m.get("timestamp", ""), reverse=True)

    def get_execution_by_id(self, execution_id: str) -> Optional[Dict[str, Any]]:
        for meta in self.list_all_executions():
            if meta.get("id") == execution_id:
                return meta
        return None

    def get_logs_by_id(self, execution_id: str) -> Optional[str]:
        meta = self.get_execution_by_id(execution_id)
        if not meta:
            return None
        ws_id = meta.get("workspace_id") or meta.get("workspace", "")
        ts = meta.get("timestamp", "")
        cmd = meta.get("command", "plan")
        return self.get_logs(ws_id, ts, cmd)

    def get_plan_json_by_id(self, execution_id: str) -> Optional[Dict]:
        meta = self.get_execution_by_id(execution_id)
        if not meta:
            return None
        ws_id = meta.get("workspace_id") or meta.get("workspace", "")
        ts = meta.get("timestamp", "")
        return self.get_plan_json(ws_id, ts)

    # ------------------------------------------------------------------
    # Per-workspace config / lock / sentinel / resource history
    # ------------------------------------------------------------------

    def _ws_key(self, workspace_id: str, filename: str) -> str:
        return f"{self._prefix}workspaces/{workspace_id}/{filename}"

    def get_workspace_config(self, workspace_id: str) -> Dict[str, Any]:
        return self._get_json(self._ws_key(workspace_id, "workspace_config.json")) or {}

    def set_workspace_config(self, workspace_id: str, config: Dict[str, Any]) -> None:
        self._put_json(self._ws_key(workspace_id, "workspace_config.json"), config)

    def get_execution_lock(self, workspace_id: str) -> Optional[Dict[str, Any]]:
        return self._get_json(self._ws_key(workspace_id, "execution_lock.json"))

    def set_execution_lock(self, workspace_id: str, lock_data: Dict[str, Any]) -> None:
        self._put_json(self._ws_key(workspace_id, "execution_lock.json"), lock_data)

    def clear_execution_lock(self, workspace_id: str) -> None:
        self._delete_key(self._ws_key(workspace_id, "execution_lock.json"))

    def get_sentinel_last_result(self, workspace_id: str) -> Optional[Dict[str, Any]]:
        return self._get_json(self._ws_key(workspace_id, "sentinel_last_result.json"))

    def set_sentinel_last_result(self, workspace_id: str, data: Dict[str, Any]) -> None:
        self._put_json(self._ws_key(workspace_id, "sentinel_last_result.json"), data)

    def get_resource_history(self, workspace_id: str) -> Dict[str, Any]:
        return self._get_json(self._ws_key(workspace_id, "resource_history.json")) or {}

    def set_resource_history(self, workspace_id: str, data: Dict[str, Any]) -> None:
        self._put_json(self._ws_key(workspace_id, "resource_history.json"), data)

    # ------------------------------------------------------------------
    # Variable Groups
    # ------------------------------------------------------------------

    def list_variable_groups(self) -> List[Dict[str, Any]]:
        prefix = f"{self._prefix}variable_groups/"
        results: List[Dict[str, Any]] = []
        try:
            for blob in self._container.list_blobs(name_starts_with=prefix):
                if blob.name.endswith(".json"):
                    data = self._get_json(blob.name)
                    if data:
                        results.append(data)
        except AzureError:
            pass
        return sorted(results, key=lambda g: (g.get("name") or "").lower())

    def get_variable_group(self, group_id: str) -> Optional[Dict[str, Any]]:
        return self._get_json(f"{self._prefix}variable_groups/{group_id}.json")

    def save_variable_group(self, group_id: str, data: Dict[str, Any]) -> None:
        self._put_json(f"{self._prefix}variable_groups/{group_id}.json", data)

    def delete_variable_group(self, group_id: str) -> None:
        self._delete_key(f"{self._prefix}variable_groups/{group_id}.json")

    # ------------------------------------------------------------------
    # Notification Channels
    # ------------------------------------------------------------------

    def list_notification_channels(self) -> List[Dict[str, Any]]:
        prefix = f"{self._prefix}notification_channels/"
        results: List[Dict[str, Any]] = []
        try:
            for blob in self._container.list_blobs(name_starts_with=prefix):
                if blob.name.endswith(".json"):
                    data = self._get_json(blob.name)
                    if data:
                        results.append(data)
        except AzureError:
            pass
        return sorted(results, key=lambda c: (c.get("name") or "").lower())

    def get_notification_channel(self, channel_id: str) -> Optional[Dict[str, Any]]:
        return self._get_json(f"{self._prefix}notification_channels/{channel_id}.json")

    def save_notification_channel(self, channel_id: str, data: Dict[str, Any]) -> None:
        self._put_json(f"{self._prefix}notification_channels/{channel_id}.json", data)

    def delete_notification_channel(self, channel_id: str) -> None:
        self._delete_key(f"{self._prefix}notification_channels/{channel_id}.json")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _execution_prefix(self, workspace_id: str, timestamp: str) -> str:
        safe_ts = timestamp.replace(":", "-").replace(".", "-")
        base = f"workspaces/{workspace_id}/runs/{safe_ts}/"
        return f"{self._prefix}{base}"

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
            "run_params": getattr(execution, "run_params", []),
            "state_resource_count": getattr(execution, "state_resource_count", None),
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

    def _delete_key(self, key: str) -> None:
        try:
            self._container.get_blob_client(key).delete_blob()
        except AzureError:
            pass
