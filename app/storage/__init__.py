"""
Storage Backend Factory — selects and returns the correct storage backend
based on the TERRAFORM_GRAPHICAL_BACKEND environment variable.

Values:
  aws    → S3 bucket
  gcp    → GCS bucket
  azure  → Azure Blob container
  local  → local filesystem (explicit)
  (unset)→ local filesystem (default)

The local backend stores data in a directory named
'TERRAFORM_GRAPHICAL_BACKEND' in the working directory, or the path
given by TERRAFORM_GRAPHICAL_BACKEND_LOCAL_PATH.  The on-disk layout is
identical to the cloud backends so migrating is just moving files and
pointing the environment variables at the new destination.
"""
import os


def get_backend():
    """
    Return an initialised storage backend instance.
    Falls back to the local filesystem backend when no cloud backend is
    configured, so execution history is always persisted.
    """
    backend_type = os.environ.get("TERRAFORM_GRAPHICAL_BACKEND", "local").lower().strip()

    if backend_type == "aws":
        from app.storage.aws_backend import S3Backend
        return S3Backend()
    if backend_type == "gcp":
        from app.storage.gcp_backend import GCSBackend
        return GCSBackend()
    if backend_type == "azure":
        from app.storage.azure_backend import AzureBackend
        return AzureBackend()

    # "local" or any unrecognised value → local filesystem
    from app.storage.local_backend import LocalBackend
    return LocalBackend()
