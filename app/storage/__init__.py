"""
Storage Backend Factory — selects and returns the correct storage backend.

Resolution order (first match wins):
  1. TERRAFORM_GRAPHICAL_BACKEND env var  (legacy / container deployments)
  2. [backend_credentials] type field in tfg.conf  (configured via Settings UI)
  3. local filesystem (default)

Values:
  aws    → S3 bucket
  gcp    → GCS bucket
  azure  → Azure Blob container
  local  → local filesystem (explicit)
  (unset)→ local filesystem (default)
"""
import os
from typing import Optional


def _config_backend_type() -> Optional[str]:
    """Read backend type from tfg.conf [backend_credentials] section if available."""
    try:
        from flask import current_app
        cfg = current_app.config.get("TFG_CONFIG")
        if cfg is None:
            return None
        from app.backend_config import get_backend_config
        bc = get_backend_config(cfg)
        t = (bc.get("type") or "").strip().lower()
        return t if t else None
    except Exception:
        return None


def _resolve_type() -> str:
    env_type = os.environ.get("TERRAFORM_GRAPHICAL_BACKEND", "").lower().strip()
    if env_type:
        return env_type
    cfg_type = _config_backend_type()
    if cfg_type:
        return cfg_type
    return "local"


def get_backend(enc_key: str = ""):
    """
    Return an initialised storage backend instance.

    *enc_key* is the plaintext portal password used to decrypt stored cloud
    credentials.  Pass it explicitly when calling from a background thread
    (no Flask request context).  When omitted, each backend falls back to
    ``flask.session["tgm_enc_key"]`` if a request context is available.

    Falls back to the local filesystem backend when no cloud backend is
    configured, so execution history is always persisted.
    """
    backend_type = _resolve_type()

    if backend_type == "aws":
        from app.storage.aws_backend import S3Backend
        return S3Backend(enc_key=enc_key)
    if backend_type == "gcp":
        from app.storage.gcp_backend import GCSBackend
        return GCSBackend(enc_key=enc_key)
    if backend_type == "azure":
        from app.storage.azure_backend import AzureBackend
        return AzureBackend(enc_key=enc_key)

    # "local" or any unrecognised value → local filesystem
    from app.storage.local_backend import LocalBackend
    return LocalBackend()
