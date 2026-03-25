"""
GCP Cloud Storage Backend — stores execution metadata and logs in a GCS bucket.

Required environment variables:
    TERRAFORM_GRAPHICAL_BACKEND_BUCKET
    TERRAFORM_GRAPHICAL_BACKEND_GOOGLE_CREDENTIALS   (service account JSON string)
"""
import json
import os
from typing import Any, Dict, List, Optional

from google.cloud import storage as gcs
from google.oauth2 import service_account


class GCSBackend:
    def __init__(self):
        self._bucket_name = os.environ["TERRAFORM_GRAPHICAL_BACKEND_BUCKET"]
        credentials_json = os.environ.get("TERRAFORM_GRAPHICAL_BACKEND_GOOGLE_CREDENTIALS")

        if credentials_json:
            creds_info = json.loads(credentials_json)
            creds = service_account.Credentials.from_service_account_info(creds_info)
            self._client = gcs.Client(credentials=creds)
        else:
            # Fall back to Application Default Credentials
            self._client = gcs.Client()

        self._bucket = self._client.bucket(self._bucket_name)

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
            blob = self._bucket.blob(f"{prefix}tfplan.binary")
            blob.upload_from_filename(execution.plan_binary_path)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def list_executions(self, workspace_id: str) -> List[Dict[str, Any]]:
        prefix = f"workspaces/{workspace_id}/runs/"
        results: List[Dict[str, Any]] = []
        try:
            blobs = self._client.list_blobs(
                self._bucket_name, prefix=prefix, delimiter="/"
            )
            # Consume the iterator to populate blobs.prefixes
            for _ in blobs:
                pass
            for run_prefix in blobs.prefixes:
                meta = self._get_json(f"{run_prefix}metadata.json")
                if meta:
                    results.append(meta)
        except Exception:
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
            blob = self._bucket.blob(key)
            blob.upload_from_string(
                json.dumps(data, indent=2, default=str),
                content_type="application/json",
            )
        except Exception:
            pass

    def _put_text(self, key: str, text: str) -> None:
        try:
            blob = self._bucket.blob(key)
            blob.upload_from_string(text, content_type="text/plain")
        except Exception:
            pass

    def _get_json(self, key: str) -> Optional[Dict]:
        try:
            blob = self._bucket.blob(key)
            return json.loads(blob.download_as_text())
        except Exception:
            return None

    def _get_text(self, key: str) -> Optional[str]:
        try:
            blob = self._bucket.blob(key)
            return blob.download_as_text()
        except Exception:
            return None
