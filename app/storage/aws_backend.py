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


def _get_aws_creds(enc_key: str = "") -> Dict[str, Any]:
    """Resolve AWS credentials: env vars take precedence over stored config.

    *enc_key* is the plaintext portal password.  When not supplied (empty
    string), the function falls back to ``flask.session["tgm_enc_key"]``
    which is only available inside a Flask request context.
    """
    if os.environ.get("TERRAFORM_GRAPHICAL_BACKEND_BUCKET"):
        return {
            "bucket": os.environ["TERRAFORM_GRAPHICAL_BACKEND_BUCKET"],
            "aws_access_key_id": os.environ.get(
                "TERRAFORM_GRAPHICAL_BACKEND_AWS_ACCESS_KEY_ID"),
            "aws_secret_access_key": os.environ.get(
                "TERRAFORM_GRAPHICAL_BACKEND_AWS_SECRET_ACCESS_KEY"),
            "aws_region": os.environ.get("TERRAFORM_GRAPHICAL_BACKEND_AWS_REGION", "us-east-1"),
            "sts_role_arn": "",
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
            bc = decrypt_fields(bc, "aws", key)
        return bc
    except Exception:
        return {}


class S3Backend:
    def __init__(self, enc_key: str = ""):
        creds = _get_aws_creds(enc_key)
        bucket = creds.get("bucket") or ""
        if not bucket:
            raise RuntimeError(
                "S3 backend: bucket not configured. Set TERRAFORM_GRAPHICAL_BACKEND_BUCKET "
                "or configure the backend via the Settings page."
            )
        self._bucket = bucket
        self._prefix = (creds.get("prefix") or "").strip().strip("/")
        if self._prefix:
            self._prefix += "/"

        client_kwargs: Dict[str, Any] = {
            "region_name": creds.get("aws_region") or "us-east-1",
        }
        ak = creds.get("aws_access_key_id") or ""
        sk = creds.get("aws_secret_access_key") or ""
        if ak and sk:
            client_kwargs["aws_access_key_id"] = ak
            client_kwargs["aws_secret_access_key"] = sk

        role_arn = (creds.get("sts_role_arn") or "").strip()
        if role_arn:
            sts = boto3.client("sts", **client_kwargs)
            assumed = sts.assume_role(
                RoleArn=role_arn,
                RoleSessionName="tfg-backend",
                DurationSeconds=3600,
            )
            tmp = assumed["Credentials"]
            client_kwargs["aws_access_key_id"] = tmp["AccessKeyId"]
            client_kwargs["aws_secret_access_key"] = tmp["SecretAccessKey"]
            client_kwargs["aws_session_token"] = tmp["SessionToken"]

        self._client = boto3.client("s3", **client_kwargs)

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
        prefix = f"{self._prefix}workspaces/{workspace_id}/runs/"
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

    def list_all_executions(self) -> List[Dict[str, Any]]:
        """Return metadata for every run across all workspaces."""
        prefix = f"{self._prefix}workspaces/"
        results: List[Dict[str, Any]] = []
        try:
            paginator = self._client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    if obj["Key"].endswith("/metadata.json") and "/runs/" in obj["Key"]:
                        meta = self._get_json(obj["Key"])
                        if meta:
                            results.append(meta)
        except (BotoCoreError, ClientError):
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
            paginator = self._client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    if obj["Key"].endswith(".json"):
                        data = self._get_json(obj["Key"])
                        if data:
                            results.append(data)
        except (BotoCoreError, ClientError):
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
            paginator = self._client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    if obj["Key"].endswith(".json"):
                        data = self._get_json(obj["Key"])
                        if data:
                            results.append(data)
        except (BotoCoreError, ClientError):
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

    def _delete_key(self, key: str) -> None:
        try:
            self._client.delete_object(Bucket=self._bucket, Key=key)
        except (BotoCoreError, ClientError):
            pass
