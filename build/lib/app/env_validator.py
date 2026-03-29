"""
Environment Validator — detects and validates credentials required by each
Terraform provider before a plan/apply execution is launched.
"""
import os
from typing import Any, Dict, List


# ---------------------------------------------------------------------------
# Credential specs per provider
# ---------------------------------------------------------------------------

AWS_VARS = [
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_DEFAULT_REGION",
    "AWS_SESSION_TOKEN",   # optional
]

GCP_VARS = [
    "GOOGLE_CREDENTIALS",
    "GOOGLE_PROJECT",      # optional but common
]

AZURE_VARS = [
    "ARM_SUBSCRIPTION_ID",
    "ARM_TENANT_ID",
    "ARM_CLIENT_ID",
    "ARM_CLIENT_SECRET",
]

_PROVIDER_VARS: Dict[str, List[str]] = {
    "aws": AWS_VARS,
    "google": GCP_VARS,
    "azurerm": AZURE_VARS,
}

_REQUIRED_VARS: Dict[str, List[str]] = {
    "aws": ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"],
    "google": ["GOOGLE_CREDENTIALS"],
    "azurerm": ["ARM_SUBSCRIPTION_ID", "ARM_TENANT_ID", "ARM_CLIENT_ID", "ARM_CLIENT_SECRET"],
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_credentials(providers: List[str]) -> Dict[str, Any]:
    """
    For each provider in `providers` return a dict describing the status
    of each required environment variable.

    Returns:
        {
            "aws": {
                "AWS_ACCESS_KEY_ID":     {"status": "configured"},
                "AWS_SECRET_ACCESS_KEY": {"status": "missing"},
                ...
            },
            ...
        }
    """
    result: Dict[str, Any] = {}
    for provider in providers:
        if provider not in _PROVIDER_VARS:
            continue
        vars_status: Dict[str, Any] = {}
        for var in _PROVIDER_VARS[provider]:
            value = os.environ.get(var)
            if value:
                vars_status[var] = {"status": "configured", "value_hint": _mask(value)}
            else:
                # Also check ~/.aws/credentials for AWS
                if var in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
                    if _check_aws_credentials_file(var):
                        vars_status[var] = {
                            "status": "configured_via_file",
                            "value_hint": "~/.aws/credentials",
                        }
                        continue
                vars_status[var] = {"status": "missing", "value_hint": None}
        result[provider] = vars_status
    return result


def build_execution_env(
    providers: List[str],
    user_supplied: Dict[str, str],
) -> Dict[str, str]:
    """
    Build a clean, isolated environment dictionary for a Terraform execution.

    Precedence (highest first):
      1. user_supplied overrides
      2. current process environment (only provider-relevant vars)

    The result is intentionally minimal — it does NOT inherit the full
    host environment to prevent credential leakage between executions.
    """
    env: Dict[str, str] = {
        # Always include PATH and HOME so Terraform binary can be found
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", os.path.expanduser("~")),
        "TF_INPUT": "false",
        "TF_IN_AUTOMATION": "true",
    }

    # Pull relevant env vars from current environment
    relevant_vars = set()
    for provider in providers:
        for var in _PROVIDER_VARS.get(provider, []):
            relevant_vars.add(var)

    for var in relevant_vars:
        if var in os.environ:
            env[var] = os.environ[var]

    # Apply user-supplied overrides (values from the credential modal)
    for key, value in user_supplied.items():
        if value:  # ignore empty strings
            env[key] = value

    return env


def get_missing_required_vars(
    providers: List[str], env: Dict[str, str]
) -> Dict[str, List[str]]:
    """Return dict of provider → list of missing required vars."""
    missing: Dict[str, List[str]] = {}
    for provider in providers:
        required = _REQUIRED_VARS.get(provider, [])
        absent = [v for v in required if not env.get(v)]
        if absent:
            missing[provider] = absent
    return missing


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mask(value: str) -> str:
    """Return a masked representation of a credential value."""
    if len(value) <= 8:
        return "****"
    return value[:4] + "****" + value[-2:]


def _check_aws_credentials_file(var: str) -> bool:
    """Check if ~/.aws/credentials file exists and contains a default profile."""
    creds_file = os.path.expanduser("~/.aws/credentials")
    if not os.path.isfile(creds_file):
        return False
    try:
        with open(creds_file, "r") as fh:
            content = fh.read()
        mapping = {
            "AWS_ACCESS_KEY_ID": "aws_access_key_id",
            "AWS_SECRET_ACCESS_KEY": "aws_secret_access_key",
        }
        return mapping.get(var, "") in content
    except OSError:
        return False
