"""
Backend Configuration Manager — stores and retrieves cloud backend credentials
encrypted with the portal lock password via Fernet (same key derivation as
variable_groups.py / crypto.py).

Credentials are persisted in tfg.conf under a [backend_credentials] section
so they survive restarts without relying on environment variables.

All sensitive fields (AWS secret, Azure connection string, GCP JSON) are
stored encrypted.  Non-sensitive fields (bucket, region, etc.) are stored
plaintext inside the config.

Supported backends: aws, gcp, azure, local
"""
from __future__ import annotations

from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# Credential schema per backend type
# ---------------------------------------------------------------------------

# Fields that are stored encrypted  (value = Fernet token)
SENSITIVE_FIELDS: Dict[str, list] = {
    "aws": ["aws_secret_access_key"],
    "gcp": ["google_credentials_json"],
    "azure": ["azure_client_secret"],
}

# All fields (sensitive + plain) per backend type
BACKEND_FIELDS: Dict[str, list] = {
    "aws": [
        "bucket",
        "prefix",           # optional path inside bucket
        "aws_region",
        "aws_access_key_id",
        "aws_secret_access_key",  # sensitive
        "sts_role_arn",     # optional: assume role ARN
    ],
    "gcp": [
        "bucket",
        "prefix",
        "gcp_project",
        "google_credentials_json",  # sensitive: full service-account JSON
    ],
    "azure": [
        "container",
        "prefix",
        "azure_subscription_id",
        "azure_resource_group",
        "azure_storage_account",
        "azure_client_id",
        "azure_tenant_id",
        "azure_client_secret",      # sensitive
    ],
    "local": [
        "local_path",
    ],
}

# Config section where credentials are stored
_SECTION = "backend_credentials"
# Temporary section that holds the OLD backend credentials while migration is pending.
# Written by save_backend_config_api() when the type changes, cleared by the
# delete-source endpoint (or the next type change).
_MIGRATION_SOURCE_SECTION = "backend_migration_source"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_backend_config(config) -> Dict[str, Any]:
    """
    Return stored backend configuration from tfg.conf.
    Returns a dict like:
      {
        "type": "aws",
        "bucket": "my-tfg-bucket",
        "aws_region": "us-east-1",
        "aws_access_key_id": "AKIA...",
        "aws_secret_access_key": "<encrypted-fernet-token>",
        ...
      }
    All sensitive fields are returned as their encrypted Fernet tokens (not
    decrypted) — callers that need the plaintext must decrypt explicitly.
    """
    parser = config._parser
    if not parser.has_section(_SECTION):
        return {}
    return dict(parser.items(_SECTION))


def save_backend_config(config, data: Dict[str, Any]) -> None:
    """
    Persist backend configuration fields to tfg.conf.
    *data* may include both sensitive (encrypted) and plain fields.
    """
    updates = {f"{_SECTION}.{k}": v for k, v in data.items() if v is not None}
    config.save(updates)


def delete_backend_config(config) -> None:
    """Remove all backend credential entries from tfg.conf."""
    parser = config._parser
    if parser.has_section(_SECTION):
        parser.remove_section(_SECTION)
        with open(config.config_path, "w", encoding="utf-8") as fh:
            parser.write(fh)
        parser.read(config.config_path)


# ---------------------------------------------------------------------------
# Migration-source stash  (old backend credentials kept while migration runs)
# ---------------------------------------------------------------------------

def get_migration_source_config(config) -> Dict[str, Any]:
    """Return the stashed old-backend config (encrypted), or {} if none."""
    parser = config._parser
    if not parser.has_section(_MIGRATION_SOURCE_SECTION):
        return {}
    return dict(parser.items(_MIGRATION_SOURCE_SECTION))


def save_migration_source_config(config, data: Dict[str, Any]) -> None:
    """Stash *data* as the migration source, replacing any previous stash."""
    parser = config._parser
    if parser.has_section(_MIGRATION_SOURCE_SECTION):
        parser.remove_section(_MIGRATION_SOURCE_SECTION)
    parser.add_section(_MIGRATION_SOURCE_SECTION)
    for k, v in data.items():
        if v is not None:
            parser.set(_MIGRATION_SOURCE_SECTION, k, str(v))
    with open(config.config_path, "w", encoding="utf-8") as fh:
        parser.write(fh)
    parser.read(config.config_path)


def delete_migration_source_config(config) -> None:
    """Remove the migration-source stash after migration completes."""
    parser = config._parser
    if parser.has_section(_MIGRATION_SOURCE_SECTION):
        parser.remove_section(_MIGRATION_SOURCE_SECTION)
        with open(config.config_path, "w", encoding="utf-8") as fh:
            parser.write(fh)
        parser.read(config.config_path)


def encrypt_fields(data: Dict[str, Any], backend_type: str, password: str) -> Dict[str, Any]:
    """
    Return a copy of *data* where sensitive fields are Fernet-encrypted.
    Plain fields are passed through unchanged.
    """
    from app.crypto import encrypt
    result = dict(data)
    for field in SENSITIVE_FIELDS.get(backend_type, []):
        if result.get(field):
            result[field] = encrypt(result[field], password)
    return result


def decrypt_fields(data: Dict[str, Any], backend_type: str, password: str) -> Dict[str, Any]:
    """
    Return a copy of *data* where sensitive fields are decrypted to plaintext.
    Raises ValueError if decryption fails.
    """
    from app.crypto import decrypt
    result = dict(data)
    for field in SENSITIVE_FIELDS.get(backend_type, []):
        if result.get(field):
            result[field] = decrypt(result[field], password)
    return result


def mask_sensitive(data: Dict[str, Any], backend_type: str) -> Dict[str, Any]:
    """Return a copy of *data* with sensitive fields replaced by '••••••••'."""
    result = dict(data)
    for field in SENSITIVE_FIELDS.get(backend_type, []):
        if result.get(field):
            result[field] = "••••••••"
    return result


# ---------------------------------------------------------------------------
# Connectivity test (write + read + delete a probe object)
# ---------------------------------------------------------------------------

def test_connectivity(backend_type: str, credentials: Dict[str, Any]) -> Dict[str, Any]:
    """
    Attempt a write/read/delete probe against the given backend using fully
    decrypted credentials.  Returns {"ok": True} or {"ok": False, "error": "..."}.
    """
    try:
        if backend_type == "aws":
            return _test_aws(credentials)
        if backend_type == "gcp":
            return _test_gcp(credentials)
        if backend_type == "azure":
            return _test_azure(credentials)
        return {"ok": False, "error": f"Unknown backend type: {backend_type}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _test_aws(creds: Dict[str, Any]) -> Dict[str, Any]:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError

    bucket = creds.get("bucket", "")
    region = creds.get("aws_region", "us-east-1")
    access_key = creds.get("aws_access_key_id", "")
    secret_key = creds.get("aws_secret_access_key", "")
    role_arn = creds.get("sts_role_arn", "").strip()

    if not bucket:
        return {"ok": False, "error": "Bucket name is required"}

    client_kwargs: Dict[str, Any] = {
        "region_name": region,
    }
    if access_key and secret_key:
        client_kwargs["aws_access_key_id"] = access_key
        client_kwargs["aws_secret_access_key"] = secret_key

    sts_client = boto3.client("sts", **client_kwargs)

    if role_arn:
        try:
            assumed = sts_client.assume_role(
                RoleArn=role_arn,
                RoleSessionName="tfg-backend-test",
                DurationSeconds=900,
            )
            tmp = assumed["Credentials"]
            client_kwargs["aws_access_key_id"] = tmp["AccessKeyId"]
            client_kwargs["aws_secret_access_key"] = tmp["SecretAccessKey"]
            client_kwargs["aws_session_token"] = tmp["SessionToken"]
        except (BotoCoreError, ClientError) as exc:
            return {"ok": False, "error": f"STS assume-role failed: {exc}"}

    s3 = boto3.client("s3", **client_kwargs)
    probe_key = "_tfg_backend_probe.json"
    prefix = creds.get("prefix", "").strip().strip("/")
    if prefix:
        probe_key = f"{prefix}/{probe_key}"

    try:
        s3.put_object(
            Bucket=bucket,
            Key=probe_key,
            Body=b'{"tfg":"probe"}',
            ContentType="application/json",
        )
        s3.get_object(Bucket=bucket, Key=probe_key)
        s3.delete_object(Bucket=bucket, Key=probe_key)
    except (BotoCoreError, ClientError) as exc:
        return {"ok": False, "error": str(exc)}

    return {"ok": True}


def _test_gcp(creds: Dict[str, Any]) -> Dict[str, Any]:
    import json as _json
    from google.cloud import storage as gcs
    from google.oauth2 import service_account

    bucket_name = creds.get("bucket", "")
    project = creds.get("gcp_project", "")
    creds_json = creds.get("google_credentials_json", "")

    if not bucket_name:
        return {"ok": False, "error": "Bucket name is required"}

    if creds_json:
        try:
            info = _json.loads(creds_json)
        except _json.JSONDecodeError as exc:
            return {"ok": False, "error": f"Invalid service account JSON: {exc}"}
        sa_creds = service_account.Credentials.from_service_account_info(info)
        client = gcs.Client(project=project or info.get("project_id"), credentials=sa_creds)
    else:
        client = gcs.Client(project=project or None)

    prefix = creds.get("prefix", "").strip().strip("/")
    probe_blob_name = "_tfg_backend_probe.json"
    if prefix:
        probe_blob_name = f"{prefix}/{probe_blob_name}"

    try:
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(probe_blob_name)
        blob.upload_from_string('{"tfg":"probe"}', content_type="application/json")
        blob.download_as_text()
        blob.delete()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    return {"ok": True}


def _test_azure(creds: Dict[str, Any]) -> Dict[str, Any]:
    from azure.storage.blob import BlobServiceClient
    from azure.identity import ClientSecretCredential
    from azure.core.exceptions import AzureError

    storage_account = creds.get("azure_storage_account", "")
    container = creds.get("container", "")
    client_id = creds.get("azure_client_id", "")
    tenant_id = creds.get("azure_tenant_id", "")
    client_secret = creds.get("azure_client_secret", "")

    if not storage_account or not container:
        return {"ok": False, "error": "Storage account and container are required"}

    account_url = f"https://{storage_account}.blob.core.windows.net"

    try:
        if client_id and tenant_id and client_secret:
            credential = ClientSecretCredential(
                tenant_id=tenant_id,
                client_id=client_id,
                client_secret=client_secret,
            )
            service = BlobServiceClient(account_url=account_url, credential=credential)
        else:
            return {"ok": False, "error": "Client ID, Tenant ID and Client Secret are required"}

        container_client = service.get_container_client(container)
        # Create container if it doesn't exist
        try:
            container_client.create_container()
        except AzureError:
            pass  # already exists

        prefix = creds.get("prefix", "").strip().strip("/")
        probe_name = "_tfg_backend_probe.json"
        if prefix:
            probe_name = f"{prefix}/{probe_name}"

        blob_client = container_client.get_blob_client(probe_name)
        blob_client.upload_blob(b'{"tfg":"probe"}', overwrite=True)
        blob_client.download_blob().readall()
        blob_client.delete_blob()
    except AzureError as exc:
        return {"ok": False, "error": str(exc)}

    return {"ok": True}


# ---------------------------------------------------------------------------
# Migration helper
# ---------------------------------------------------------------------------

def migrate_backend(
    source_type: str,
    source_creds: Dict[str, Any],
    dest_type: str,
    dest_creds: Dict[str, Any],
    progress_callback=None,
) -> Dict[str, Any]:
    """
    Copy all data from *source* backend to *dest* backend.
    Returns {"ok": True, "count": N, "skipped": M} even if some objects
    could not be written (per-key errors are logged but don't abort).
    *progress_callback* is called with (copied, total) if provided.
    """
    try:
        src_backend = _build_backend_from_creds(source_type, source_creds)
        dst_backend = _build_backend_from_creds(dest_type, dest_creds)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    keys = _list_all_keys(source_type, src_backend)
    total = len(keys)
    copied = 0
    skipped = 0
    skip_reasons: list = []

    # Extensions to skip: compiled Terraform plan binaries can cause
    # SignatureDoesNotMatch on some S3 configurations; plan.json + logs
    # contain all the useful information for history purposes.
    _SKIP_EXTENSIONS = {".binary"}

    for key in keys:
        if any(key.endswith(ext) for ext in _SKIP_EXTENSIONS):
            skipped += 1
            continue
        try:
            data = _read_key(source_type, src_backend, key)
            if data is None or len(data) == 0:
                # Skip truly empty/unreadable files.
                skipped += 1
                continue
            _write_key(dest_type, dst_backend, key, data)
            copied += 1
            if progress_callback:
                progress_callback(copied, total)
        except Exception as exc:
            skipped += 1
            skip_reasons.append(f"{key}: {exc}")

    result: Dict[str, Any] = {"ok": True, "count": copied, "skipped": skipped}
    if skip_reasons:
        # Surface the first few errors so the UI can display them.
        result["errors"] = skip_reasons[:5]
        result["warning"] = skip_reasons[0]
    return result


def delete_backend_data(backend_type: str, creds: Dict[str, Any]) -> Dict[str, Any]:
    """Delete all TFG-managed data from a backend. Used after successful migration."""
    try:
        backend = _build_backend_from_creds(backend_type, creds)
        keys = _list_all_keys(backend_type, backend)
        deleted = 0
        for key in keys:
            _delete_key(backend_type, backend, key)
            deleted += 1
        return {"ok": True, "count": deleted}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Internal helpers for migration (operate on raw storage APIs)
# ---------------------------------------------------------------------------

def _build_backend_from_creds(backend_type: str, creds: Dict[str, Any]):
    """Build a storage-backend instance wired to the provided credentials
    without reading environment variables."""
    if backend_type == "local":
        import os
        from app.storage.local_backend import LocalBackend
        lp = creds.get("local_path") or os.path.join(os.getcwd(), "TERRAFORM_GRAPHICAL_BACKEND")
        b = LocalBackend.__new__(LocalBackend)
        b._root = lp
        return b

    if backend_type == "aws":
        import boto3
        from app.storage.aws_backend import S3Backend

        client_kwargs: Dict[str, Any] = {
            "region_name": creds.get("aws_region", "us-east-1"),
        }
        ak = creds.get("aws_access_key_id", "")
        sk = creds.get("aws_secret_access_key", "")
        if ak and sk:
            client_kwargs["aws_access_key_id"] = ak
            client_kwargs["aws_secret_access_key"] = sk

        role_arn = creds.get("sts_role_arn", "").strip()
        if role_arn:
            sts = boto3.client("sts", **client_kwargs)
            assumed = sts.assume_role(
                RoleArn=role_arn,
                RoleSessionName="tfg-backend-migrate",
                DurationSeconds=3600,
            )
            tmp = assumed["Credentials"]
            client_kwargs["aws_access_key_id"] = tmp["AccessKeyId"]
            client_kwargs["aws_secret_access_key"] = tmp["SecretAccessKey"]
            client_kwargs["aws_session_token"] = tmp["SessionToken"]

        b = S3Backend.__new__(S3Backend)
        b._bucket = creds["bucket"]
        prefix = creds.get("prefix", "").strip().strip("/")
        b._prefix = (prefix + "/") if prefix else ""
        b._client = boto3.client("s3", **client_kwargs)
        return b

    if backend_type == "gcp":
        import json as _json
        from google.cloud import storage as gcs
        from google.oauth2 import service_account
        from app.storage.gcp_backend import GCSBackend

        creds_json = creds.get("google_credentials_json", "")
        project = creds.get("gcp_project", "")
        if creds_json:
            info = _json.loads(creds_json)
            sa = service_account.Credentials.from_service_account_info(info)
            client = gcs.Client(project=project or info.get("project_id"), credentials=sa)
        else:
            client = gcs.Client(project=project or None)

        b = GCSBackend.__new__(GCSBackend)
        b._bucket_name = creds["bucket"]
        b._client = client
        b._bucket = client.bucket(creds["bucket"])
        prefix = creds.get("prefix", "").strip().strip("/")
        b._prefix = (prefix + "/") if prefix else ""
        return b

    if backend_type == "azure":
        import json as _json
        from azure.storage.blob import BlobServiceClient
        from azure.identity import ClientSecretCredential
        from app.storage.azure_backend import AzureBackend

        storage_account = creds["azure_storage_account"]
        account_url = f"https://{storage_account}.blob.core.windows.net"
        credential = ClientSecretCredential(
            tenant_id=creds["azure_tenant_id"],
            client_id=creds["azure_client_id"],
            client_secret=creds["azure_client_secret"],
        )
        service = BlobServiceClient(account_url=account_url, credential=credential)
        container_name = creds["container"]

        b = AzureBackend.__new__(AzureBackend)
        b._container_name = container_name
        b._client = service
        b._container = service.get_container_client(container_name)
        prefix = creds.get("prefix", "").strip().strip("/")
        b._prefix = (prefix + "/") if prefix else ""
        return b

    raise ValueError(f"Unsupported backend type: {backend_type}")


def _list_all_keys(backend_type: str, backend) -> list:
    """Return all storage keys managed by TFG in this backend."""
    keys = []
    if backend_type == "local":
        import os
        root = backend._root
        if not os.path.isdir(root):
            return []
        for dirpath, _, filenames in os.walk(root):
            for fname in filenames:
                full = os.path.join(dirpath, fname)
                rel = os.path.relpath(full, root).replace(os.sep, "/")
                keys.append(rel)
        return keys

    if backend_type == "aws":
        from botocore.exceptions import BotoCoreError, ClientError
        prefix = backend._prefix
        try:
            paginator = backend._client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=backend._bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    keys.append(obj["Key"])
        except (BotoCoreError, ClientError):
            pass
        return keys

    if backend_type == "gcp":
        prefix = getattr(backend, "_prefix", "")
        for blob in backend._client.list_blobs(backend._bucket_name, prefix=prefix):
            keys.append(blob.name)
        return keys

    if backend_type == "azure":
        prefix = getattr(backend, "_prefix", "")
        for blob in backend._container.list_blobs(name_starts_with=prefix):
            keys.append(blob.name)
        return keys

    return []


def _read_key(backend_type: str, backend, key: str) -> Optional[bytes]:
    """Read raw bytes for a given storage key."""
    if backend_type == "local":
        import os
        full = os.path.join(backend._root, key.replace("/", os.sep))
        try:
            with open(full, "rb") as fh:
                return fh.read()
        except OSError:
            return None

    if backend_type == "aws":
        from botocore.exceptions import BotoCoreError, ClientError
        try:
            resp = backend._client.get_object(Bucket=backend._bucket, Key=key)
            return resp["Body"].read()
        except (BotoCoreError, ClientError):
            return None

    if backend_type == "gcp":
        try:
            blob = backend._bucket.blob(key)
            return blob.download_as_bytes()
        except Exception:
            return None

    if backend_type == "azure":
        try:
            bc = backend._container.get_blob_client(key)
            return bc.download_blob().readall()
        except Exception:
            return None

    return None


def _write_key(backend_type: str, backend, key: str, data: bytes) -> None:
    """Write raw bytes to a given storage key."""
    if backend_type == "local":
        import os
        full = os.path.join(backend._root, key.replace("/", os.sep))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as fh:
            fh.write(data)
        return

    if backend_type == "aws":
        import mimetypes
        ct = mimetypes.guess_type(key)[0] or "application/octet-stream"
        backend._client.put_object(Bucket=backend._bucket, Key=key, Body=data, ContentType=ct)
        return

    if backend_type == "gcp":
        import mimetypes
        ct = mimetypes.guess_type(key)[0] or "application/octet-stream"
        blob = backend._bucket.blob(key)
        blob.upload_from_string(data, content_type=ct)
        return

    if backend_type == "azure":
        import mimetypes
        from azure.storage.blob import ContentSettings
        ct = mimetypes.guess_type(key)[0] or "application/octet-stream"
        bc = backend._container.get_blob_client(key)
        bc.upload_blob(data, overwrite=True, content_settings=ContentSettings(content_type=ct))
        return


def _delete_key(backend_type: str, backend, key: str) -> None:
    """Delete a storage key."""
    if backend_type == "local":
        import os
        full = os.path.join(backend._root, key.replace("/", os.sep))
        try:
            os.remove(full)
        except OSError:
            pass
        return

    if backend_type == "aws":
        backend._client.delete_object(Bucket=backend._bucket, Key=key)
        return

    if backend_type == "gcp":
        try:
            backend._bucket.blob(key).delete()
        except Exception:
            pass
        return

    if backend_type == "azure":
        try:
            backend._container.get_blob_client(key).delete_blob()
        except Exception:
            pass
        return
