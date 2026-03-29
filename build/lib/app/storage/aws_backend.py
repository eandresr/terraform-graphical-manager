"""
AWS S3 Storage Backend — stores execution metadata and logs in an S3 bucket.

Required environment variables:
    TERRAFORM_GRAPHICAL_BACKEND_BUCKET
    TERRAFORM_GRAPHICAL_BACKEND_AWS_ACCESS_KEY_ID
    TERRAFORM_GRAPHICAL_BACKEND_AWS_SECRET_ACCESS_KEY
    TERRAFORM_GRAPHICAL_BACKEND_AWS_REGION            (default: us-east-1)
"""
import json
import os
from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError


class S3Backend:
    def __init__(self):
        self._bucket = os.environ["TERRAFORM_GRAPHICAL_BACKEND_BUCKET"]
        self._client = boto3.client(
            "s3",
            aws_access_key_id=os.environ.get(
                "TERRAFORM_GRAPHICAL_BACKEND_AWS_ACCESS_KEY_ID"
            ),
            aws_secret_access_key=os.environ.get(
                "TERRAFORM_GRAPHICAL_BACKEND_AWS_SECRET_ACCESS_KEY"
            ),
            region_name=os.environ.get(
                "TERRAFORM_GRAPHICAL_BACKEND_AWS_REGION", "us-east-1"
            ),
        )

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def store_execution(self, execution) -> None:
        """Persist metadata, logs, and plan artefacts for an execution."""
        prefix = self._execution_prefix(execution.workspace_id, execution.timestamp)

        # metadata.json
        metadata = self._build_metadata(execution)
        self._put_json(f"{prefix}metadata.json", metadata)

        # logs
        log_text = "\n".join(execution.logs)
        if execution.command == "plan":
            self._put_text(f"{prefix}plan.log", log_text)
        else:
            self._put_text(f"{prefix}apply.log", log_text)

        # plan.json
        if execution.plan_json:
            self._put_json(f"{prefix}plan.json", execution.plan_json)

        # tfplan.binary
        if execution.plan_binary_path and os.path.isfile(execution.plan_binary_path):
            self._put_file(f"{prefix}tfplan.binary", execution.plan_binary_path)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def list_executions(self, workspace_id: str) -> List[Dict[str, Any]]:
        """Return a list of metadata dicts for all runs of a workspace."""
        prefix = f"workspaces/{workspace_id}/runs/"
        results: List[Dict[str, Any]] = []
        try:
            paginator = self._client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix, Delimiter="/"):
                for cp in page.get("CommonPrefixes", []):
                    meta_key = cp["Prefix"] + "metadata.json"
                    meta = self._get_json(meta_key)
                    if meta:
                        results.append(meta)
        except (BotoCoreError, ClientError):
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
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=json.dumps(data, indent=2, default=str).encode(),
                ContentType="application/json",
            )
        except (BotoCoreError, ClientError):
            pass

    def _put_text(self, key: str, text: str) -> None:
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=text.encode(),
                ContentType="text/plain",
            )
        except (BotoCoreError, ClientError):
            pass

    def _put_file(self, key: str, file_path: str) -> None:
        try:
            self._client.upload_file(file_path, self._bucket, key)
        except (BotoCoreError, ClientError):
            pass

    def _get_json(self, key: str) -> Optional[Dict]:
        try:
            resp = self._client.get_object(Bucket=self._bucket, Key=key)
            return json.loads(resp["Body"].read())
        except (BotoCoreError, ClientError, json.JSONDecodeError):
            return None

    def _get_text(self, key: str) -> Optional[str]:
        try:
            resp = self._client.get_object(Bucket=self._bucket, Key=key)
            return resp["Body"].read().decode()
        except (BotoCoreError, ClientError):
            return None
