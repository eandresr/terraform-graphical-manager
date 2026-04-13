"""
Workflow Runner — executes post-run automation workflows per workspace.

Supported built-in types
------------------------
  script    — Run an inline shell/Python/etc. script
  api       — HTTP call with optional Bearer-token authentication
  rundeck   — Trigger a Rundeck job via the REST API v1
  jenkins   — Trigger a Jenkins build via the REST API

Plugin architecture
-------------------
  Register custom plugins by subclassing WorkflowPlugin and calling
  ``register_plugin(MyPlugin)``.  A plugin must define:
    type_id:         str   — unique snake_case identifier
    display_name:    str   — label shown in the UI
    sensitive_fields: List[str] — config keys encrypted at rest
    execute(config, context, enc_key="") -> WorkflowResult

Sensitive fields per type
-------------------------
  api       : token
  rundeck   : api_token
  jenkins   : api_token

  Fields are stored Fernet-encrypted (identical algorithm to workspace
  variables / notification channels) when a portal password is active.
  Stored ciphertext is prefixed with "enc:".
  Vault references ("vault:<path>") are also supported transparently.

Variable templates
------------------
  {{ var.NAME }}   — workspace variable (plaintext) or "***" if sensitive
  {{ env.NAME }}   — OS environment variable
  {{ run.NAME }}   — execution context  (id, command, status, workspace_id,
                     workspace_name, duration, timestamp, terraform_version)

Triggers (stored as a list on each workflow)
--------------------------------------------
  plan:success    plan:failed    plan:any
  apply:success   apply:failed   apply:any
  destroy:success destroy:failed destroy:any
"""
import json as _json
import os
import re
import subprocess
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class WorkflowResult:
    workflow_id: str
    workflow_name: str
    workflow_type: str
    triggered_at: str
    status: str           # "success" | "failed" | "skipped"
    output: str = ""
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "workflow_name": self.workflow_name,
            "workflow_type": self.workflow_type,
            "triggered_at": self.triggered_at,
            "status": self.status,
            "output": self.output,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# Sensitive fields registry
# ---------------------------------------------------------------------------

_SENSITIVE: Dict[str, List[str]] = {
    "api":      ["token"],
    "rundeck":  ["api_token"],
    "jenkins":  ["api_token"],
}

_ENC_PREFIX = "enc:"


def _sensitive_fields_for(wf_type: str) -> List[str]:
    return _SENSITIVE.get(wf_type) or []


# ---------------------------------------------------------------------------
# Encryption helpers  (mirrors notification_manager.py pattern)
# ---------------------------------------------------------------------------

def _get_enc_key() -> Optional[str]:
    try:
        from flask import session
        return session.get("tgm_enc_key") or None
    except RuntimeError:
        return None


def _encrypt_field(value: str, password: str) -> str:
    from app.crypto import encrypt
    return _ENC_PREFIX + encrypt(value, password)


def _decrypt_field(value: str, password: str) -> str:
    if value.startswith(_ENC_PREFIX):
        from app.crypto import decrypt
        return decrypt(value[len(_ENC_PREFIX):], password)
    return value


def encrypt_workflow_secrets(workflow: Dict[str, Any], password: str) -> Dict[str, Any]:
    """Return a deep-copy of *workflow* with sensitive config fields encrypted."""
    workflow = _json.loads(_json.dumps(workflow))
    cfg = workflow.get("config") or {}
    wf_type = (workflow.get("type") or "").lower()
    fields = _sensitive_fields_for(wf_type)
    wf_id = workflow.get("id") or "unknown"

    _vault_cfg = None
    try:
        from flask import current_app
        _vault_cfg = current_app.config.get("TFG_CONFIG")
    except RuntimeError:
        pass
    vault_enabled = _vault_cfg and getattr(_vault_cfg, "vault_enabled", False)

    for field_name in fields:
        raw = (cfg.get(field_name) or "").strip()
        if not raw:
            continue
        if raw.startswith(_ENC_PREFIX) or raw.startswith("vault:"):
            continue
        if vault_enabled and password:
            try:
                from app import vault_manager as _vm
                path = (
                    f"{_vault_cfg.vault_path_prefix}/workflows/"
                    f"{wf_id}/{field_name}"
                )
                cfg[field_name] = _vm.store_secret(
                    _vault_cfg, password, path, raw
                )
            except Exception:
                cfg[field_name] = _encrypt_field(raw, password)
        else:
            cfg[field_name] = _encrypt_field(raw, password)

    workflow["config"] = cfg
    return workflow


def decrypt_workflow_secrets(workflow: Dict[str, Any], password: str) -> Dict[str, Any]:
    """Return a deep-copy of *workflow* with sensitive config fields decrypted."""
    workflow = _json.loads(_json.dumps(workflow))
    cfg = workflow.get("config") or {}
    wf_type = (workflow.get("type") or "").lower()
    fields = _sensitive_fields_for(wf_type)

    _vault_cfg = None
    try:
        from flask import current_app
        _vault_cfg = current_app.config.get("TFG_CONFIG")
    except RuntimeError:
        pass

    for field_name in fields:
        val = cfg.get(field_name) or ""
        if val.startswith("vault:"):
            if _vault_cfg and password:
                try:
                    from app import vault_manager as _vm
                    cfg[field_name] = _vm.resolve_secret(_vault_cfg, password, val)
                except Exception:
                    cfg[field_name] = ""
            else:
                cfg[field_name] = ""
        elif val.startswith(_ENC_PREFIX):
            try:
                cfg[field_name] = _decrypt_field(val, password)
            except Exception:
                cfg[field_name] = ""

    workflow["config"] = cfg
    return workflow


def mask_workflow_secrets(workflow: Dict[str, Any]) -> Dict[str, Any]:
    """Return a deep-copy with sensitive field values replaced by '***'."""
    workflow = _json.loads(_json.dumps(workflow))
    cfg = workflow.get("config") or {}
    wf_type = (workflow.get("type") or "").lower()
    fields = _sensitive_fields_for(wf_type)

    for field_name in fields:
        if cfg.get(field_name):
            cfg[field_name] = "***"

    workflow["config"] = cfg
    return workflow


# ---------------------------------------------------------------------------
# Variable template resolver
# ---------------------------------------------------------------------------

_TEMPLATE_RE = re.compile(r"\{\{\s*(var|env|run)\.([A-Za-z0-9_]+)\s*\}\}")


def resolve_template(text: str, context: Dict[str, Any]) -> str:
    """
    Replace ``{{ var.NAME }}``, ``{{ env.NAME }}``, ``{{ run.NAME }}`` in
    *text* using *context*.  Sensitive workspace vars are substituted with
    '***'.  Unresolved placeholders are left as-is.
    """
    if not text:
        return text

    def _replace(m: re.Match) -> str:
        ns = m.group(1)
        key = m.group(2)
        bucket = context.get(ns) or {}
        if ns == "var":
            entry = bucket.get(key)
            if entry is None:
                return m.group(0)
            if isinstance(entry, dict):
                if entry.get("sensitive"):
                    return "***"
                return str(entry.get("value") or "")
            return str(entry)
        val = bucket.get(key)
        if val is None:
            return m.group(0)
        return str(val)

    return _TEMPLATE_RE.sub(_replace, text)


def build_run_context(
    execution_dict: Dict[str, Any],
    workspace_name: str,
    enc_key: str = "",
) -> Dict[str, Any]:
    """Build the template resolution context for a given execution dict."""
    ws_id = execution_dict.get("workspace_id") or ""

    # --- var namespace: workspace variables (workspace_config.json) ---
    var_map: Dict[str, Any] = {}
    try:
        from app.storage import get_backend
        backend = get_backend(enc_key)
        ws_cfg = backend.get_workspace_config(ws_id)
        for v in ws_cfg.get("variables", []):
            name = v.get("name") or ""
            if name:
                var_map[name] = {
                    "value": v.get("value", ""),
                    "sensitive": v.get("sensitive", False),
                }
        var_map.setdefault(
            "workspace_name", {"value": workspace_name, "sensitive": False}
        )
        var_map.setdefault(
            "workspace_id", {"value": ws_id, "sensitive": False}
        )
    except Exception:
        pass

    # --- env namespace ---
    env_map = dict(os.environ)

    # --- run namespace ---
    run_map = {
        "id": execution_dict.get("id") or "",
        "command": execution_dict.get("command") or "",
        "status": execution_dict.get("status") or "",
        "workspace_id": ws_id,
        "workspace_name": workspace_name,
        "duration": str(execution_dict.get("duration_seconds") or ""),
        "timestamp": execution_dict.get("timestamp") or "",
        "terraform_version": execution_dict.get("terraform_version") or "",
    }

    return {"var": var_map, "env": env_map, "run": run_map}


# ---------------------------------------------------------------------------
# Plugin base & registry
# ---------------------------------------------------------------------------

class WorkflowPlugin:
    type_id: str = ""
    display_name: str = ""
    sensitive_fields: List[str] = []

    def execute(
        self,
        config: Dict[str, Any],
        context: Dict[str, Any],
        enc_key: str = "",
    ) -> WorkflowResult:
        raise NotImplementedError


WORKFLOW_REGISTRY: Dict[str, type] = {}


def register_plugin(cls: type) -> type:
    """Decorator / callable to register a WorkflowPlugin subclass."""
    WORKFLOW_REGISTRY[cls.type_id] = cls
    return cls


def plugin_info() -> List[Dict[str, Any]]:
    """Return a list of plugin metadata dicts for the UI."""
    return [
        {
            "type_id": cls.type_id,
            "display_name": cls.display_name,
            "sensitive_fields": cls.sensitive_fields,
        }
        for cls in WORKFLOW_REGISTRY.values()
    ]


# ---------------------------------------------------------------------------
# Built-in plugin: Script
# ---------------------------------------------------------------------------

@register_plugin
class ScriptPlugin(WorkflowPlugin):
    type_id = "script"
    display_name = "Script"
    sensitive_fields = []

    def execute(
        self,
        config: Dict[str, Any],
        context: Dict[str, Any],
        enc_key: str = "",
    ) -> WorkflowResult:
        import datetime
        import tempfile

        wf_id = config.get("_workflow_id", "")
        wf_name = config.get("_workflow_name", "")
        triggered_at = datetime.datetime.utcnow().isoformat()

        script_body = resolve_template(config.get("script") or "", context)
        interpreter = config.get("interpreter") or "bash"
        timeout = int(config.get("timeout") or 60)
        working_dir = config.get("working_dir") or None

        if not script_body.strip():
            return WorkflowResult(
                workflow_id=wf_id, workflow_name=wf_name,
                workflow_type=self.type_id, triggered_at=triggered_at,
                status="skipped", error="Empty script body",
            )

        suffix = ".py" if "python" in interpreter else ".sh"
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=suffix, delete=False, encoding="utf-8"
            ) as tmp:
                tmp.write(script_body)
                tmp_path = tmp.name
            os.chmod(tmp_path, 0o700)

            run_env = dict(os.environ)
            for k, v in (context.get("run") or {}).items():
                run_env[f"TGM_{k.upper()}"] = str(v)

            result = subprocess.run(
                [interpreter, tmp_path],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=working_dir,
                env=run_env,
            )
            output = ((result.stdout or "") + (result.stderr or ""))[:4096]
            if result.returncode == 0:
                return WorkflowResult(
                    workflow_id=wf_id, workflow_name=wf_name,
                    workflow_type=self.type_id, triggered_at=triggered_at,
                    status="success", output=output,
                )
            return WorkflowResult(
                workflow_id=wf_id, workflow_name=wf_name,
                workflow_type=self.type_id, triggered_at=triggered_at,
                status="failed", output=output,
                error=f"Exit code: {result.returncode}",
            )
        except subprocess.TimeoutExpired:
            return WorkflowResult(
                workflow_id=wf_id, workflow_name=wf_name,
                workflow_type=self.type_id, triggered_at=triggered_at,
                status="failed", error=f"Timeout after {timeout}s",
            )
        except Exception as exc:
            return WorkflowResult(
                workflow_id=wf_id, workflow_name=wf_name,
                workflow_type=self.type_id, triggered_at=triggered_at,
                status="failed", error=str(exc),
            )
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# Built-in plugin: API (Token)
# ---------------------------------------------------------------------------

@register_plugin
class ApiPlugin(WorkflowPlugin):
    type_id = "api"
    display_name = "API (Token)"
    sensitive_fields = ["token"]

    def execute(
        self,
        config: Dict[str, Any],
        context: Dict[str, Any],
        enc_key: str = "",
    ) -> WorkflowResult:
        import datetime
        import ssl
        from urllib.error import URLError
        from urllib.request import Request, urlopen

        wf_id = config.get("_workflow_id", "")
        wf_name = config.get("_workflow_name", "")
        triggered_at = datetime.datetime.utcnow().isoformat()

        url = resolve_template(config.get("url") or "", context)
        method = (config.get("method") or "POST").upper()
        token = config.get("token") or ""        # already decrypted by caller
        token_header = config.get("token_header") or "Authorization"
        token_prefix = config.get("token_prefix") or "Bearer"
        headers_raw = resolve_template(config.get("headers") or "{}", context)
        body_raw = resolve_template(config.get("body") or "", context)
        verify_ssl = config.get("verify_ssl", True)
        timeout = int(config.get("timeout") or 30)

        if not url:
            return WorkflowResult(
                workflow_id=wf_id, workflow_name=wf_name,
                workflow_type=self.type_id, triggered_at=triggered_at,
                status="skipped", error="No URL configured",
            )

        try:
            headers: Dict[str, str] = (
                _json.loads(headers_raw) if headers_raw.strip() else {}
            )
        except Exception:
            headers = {}

        if token:
            header_value = f"{token_prefix} {token}".strip()
            headers[token_header] = header_value

        body_bytes = body_raw.encode("utf-8") if body_raw else None
        req = Request(url, data=body_bytes, headers=headers, method=method)

        try:
            ssl_ctx = ssl.create_default_context()
            if not verify_ssl:
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode = ssl.CERT_NONE
            with urlopen(req, timeout=timeout, context=ssl_ctx) as resp:
                raw_out = resp.read().decode("utf-8", errors="replace")[:4096]
                status_code = resp.status
            if 200 <= status_code < 300:
                return WorkflowResult(
                    workflow_id=wf_id, workflow_name=wf_name,
                    workflow_type=self.type_id, triggered_at=triggered_at,
                    status="success", output=f"HTTP {status_code}: {raw_out}",
                )
            return WorkflowResult(
                workflow_id=wf_id, workflow_name=wf_name,
                workflow_type=self.type_id, triggered_at=triggered_at,
                status="failed", output=f"HTTP {status_code}: {raw_out}",
                error=f"Non-2xx response: {status_code}",
            )
        except URLError as exc:
            return WorkflowResult(
                workflow_id=wf_id, workflow_name=wf_name,
                workflow_type=self.type_id, triggered_at=triggered_at,
                status="failed", error=str(exc),
            )
        except Exception as exc:
            return WorkflowResult(
                workflow_id=wf_id, workflow_name=wf_name,
                workflow_type=self.type_id, triggered_at=triggered_at,
                status="failed", error=str(exc),
            )


# ---------------------------------------------------------------------------
# Built-in plugin: Rundeck Job
# ---------------------------------------------------------------------------

@register_plugin
class RundeckPlugin(WorkflowPlugin):
    type_id = "rundeck"
    display_name = "Rundeck Job"
    sensitive_fields = ["api_token"]

    def execute(
        self,
        config: Dict[str, Any],
        context: Dict[str, Any],
        enc_key: str = "",
    ) -> WorkflowResult:
        import datetime
        from urllib.error import URLError
        from urllib.request import Request, urlopen

        wf_id = config.get("_workflow_id", "")
        wf_name = config.get("_workflow_name", "")
        triggered_at = datetime.datetime.utcnow().isoformat()

        base_url = (config.get("url") or "").rstrip("/")
        api_token = config.get("api_token") or ""
        project = resolve_template(config.get("project") or "", context)
        job_id = resolve_template(config.get("job_id") or "", context)
        args = resolve_template(config.get("args") or "", context)
        timeout = int(config.get("timeout") or 120)

        if not base_url or not api_token or not job_id:
            return WorkflowResult(
                workflow_id=wf_id, workflow_name=wf_name,
                workflow_type=self.type_id, triggered_at=triggered_at,
                status="skipped",
                error="Missing Rundeck URL, API token, or Job ID",
            )

        # Rundeck API v1: POST /api/42/job/{id}/run
        url = f"{base_url}/api/42/job/{job_id}/run"
        payload: Dict[str, Any] = {}
        if project:
            payload["project"] = project
        if args:
            payload["argString"] = args

        body = _json.dumps(payload).encode("utf-8")
        headers = {
            "X-Rundeck-Auth-Token": api_token,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        req = Request(url, data=body, headers=headers, method="POST")

        try:
            with urlopen(req, timeout=timeout) as resp:
                raw_out = resp.read().decode("utf-8", errors="replace")[:4096]
                status_code = resp.status
            if 200 <= status_code < 300:
                return WorkflowResult(
                    workflow_id=wf_id, workflow_name=wf_name,
                    workflow_type=self.type_id, triggered_at=triggered_at,
                    status="success",
                    output=f"HTTP {status_code}: {raw_out}",
                )
            return WorkflowResult(
                workflow_id=wf_id, workflow_name=wf_name,
                workflow_type=self.type_id, triggered_at=triggered_at,
                status="failed", output=f"HTTP {status_code}: {raw_out}",
                error=f"Non-2xx response: {status_code}",
            )
        except URLError as exc:
            return WorkflowResult(
                workflow_id=wf_id, workflow_name=wf_name,
                workflow_type=self.type_id, triggered_at=triggered_at,
                status="failed", error=str(exc),
            )
        except Exception as exc:
            return WorkflowResult(
                workflow_id=wf_id, workflow_name=wf_name,
                workflow_type=self.type_id, triggered_at=triggered_at,
                status="failed", error=str(exc),
            )


# ---------------------------------------------------------------------------
# Built-in plugin: Jenkins Job
# ---------------------------------------------------------------------------

@register_plugin
class JenkinsPlugin(WorkflowPlugin):
    type_id = "jenkins"
    display_name = "Jenkins Job"
    sensitive_fields = ["api_token"]

    def execute(
        self,
        config: Dict[str, Any],
        context: Dict[str, Any],
        enc_key: str = "",
    ) -> WorkflowResult:
        import base64
        import datetime
        from urllib.error import URLError, HTTPError
        from urllib.parse import urlencode
        from urllib.request import Request, urlopen

        wf_id = config.get("_workflow_id", "")
        wf_name = config.get("_workflow_name", "")
        triggered_at = datetime.datetime.utcnow().isoformat()

        base_url = (config.get("url") or "").rstrip("/")
        username = config.get("username") or ""
        api_token = config.get("api_token") or ""
        job_path = resolve_template(
            (config.get("job_path") or "").strip("/"), context
        )
        params_raw = resolve_template(
            config.get("parameters") or "{}", context
        )
        timeout = int(config.get("timeout") or 120)

        if not base_url or not api_token or not job_path:
            return WorkflowResult(
                workflow_id=wf_id, workflow_name=wf_name,
                workflow_type=self.type_id, triggered_at=triggered_at,
                status="skipped",
                error="Missing Jenkins URL, API token, or job path",
            )

        try:
            params: Dict[str, str] = (
                _json.loads(params_raw) if params_raw.strip() else {}
            )
        except Exception:
            params = {}

        if params:
            url = (
                f"{base_url}/job/{job_path}/buildWithParameters"
                f"?{urlencode(params)}"
            )
        else:
            url = f"{base_url}/job/{job_path}/build"

        creds = base64.b64encode(
            f"{username}:{api_token}".encode()
        ).decode()
        headers = {"Authorization": f"Basic {creds}"}
        req = Request(url, data=b"", headers=headers, method="POST")

        try:
            with urlopen(req, timeout=timeout) as resp:
                raw_out = resp.read().decode("utf-8", errors="replace")[:2048]
                status_code = resp.status
            # Jenkins typically returns 201 for a queued build
            if 200 <= status_code < 303:
                return WorkflowResult(
                    workflow_id=wf_id, workflow_name=wf_name,
                    workflow_type=self.type_id, triggered_at=triggered_at,
                    status="success",
                    output=f"HTTP {status_code}: {raw_out or 'Build queued'}",
                )
            return WorkflowResult(
                workflow_id=wf_id, workflow_name=wf_name,
                workflow_type=self.type_id, triggered_at=triggered_at,
                status="failed", output=f"HTTP {status_code}: {raw_out}",
                error=f"Non-2xx response: {status_code}",
            )
        except HTTPError as exc:
            # 201 Created is expected for queue jobs; treat as success
            if exc.code == 201:
                return WorkflowResult(
                    workflow_id=wf_id, workflow_name=wf_name,
                    workflow_type=self.type_id, triggered_at=triggered_at,
                    status="success",
                    output="HTTP 201: Build queued",
                )
            return WorkflowResult(
                workflow_id=wf_id, workflow_name=wf_name,
                workflow_type=self.type_id, triggered_at=triggered_at,
                status="failed", error=f"HTTP {exc.code}: {exc.reason}",
            )
        except URLError as exc:
            return WorkflowResult(
                workflow_id=wf_id, workflow_name=wf_name,
                workflow_type=self.type_id, triggered_at=triggered_at,
                status="failed", error=str(exc),
            )
        except Exception as exc:
            return WorkflowResult(
                workflow_id=wf_id, workflow_name=wf_name,
                workflow_type=self.type_id, triggered_at=triggered_at,
                status="failed", error=str(exc),
            )


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------

def _backend(enc_key: str = ""):
    from app.storage import get_backend
    return get_backend(enc_key)


def list_workflows(workspace_id: str) -> List[Dict[str, Any]]:
    try:
        return _backend().list_workspace_workflows(workspace_id)
    except AttributeError:
        return []


def get_workflow(workspace_id: str, workflow_id: str) -> Optional[Dict[str, Any]]:
    for wf in list_workflows(workspace_id):
        if wf.get("id") == workflow_id:
            return wf
    return None


def save_workflow(
    workspace_id: str, workflow: Dict[str, Any]
) -> Dict[str, Any]:
    if not workflow.get("id"):
        workflow = {**workflow, "id": str(uuid.uuid4())}
    try:
        _backend().save_workspace_workflow(workspace_id, workflow)
    except AttributeError:
        pass
    return workflow


def delete_workflow(workspace_id: str, workflow_id: str) -> None:
    try:
        _backend().delete_workspace_workflow(workspace_id, workflow_id)
    except AttributeError:
        pass


# ---------------------------------------------------------------------------
# Trigger matching
# ---------------------------------------------------------------------------

def _matches_trigger(trigger: str, command: str, status: str) -> bool:
    """Return True if the run (command, status) satisfies *trigger*."""
    parts = trigger.split(":", 1)
    if len(parts) != 2:
        return False
    t_cmd, t_status = parts
    if t_cmd not in ("plan", "apply", "destroy") or t_cmd != command:
        return False
    if t_status == "any":
        return True
    return t_status == status


# ---------------------------------------------------------------------------
# Dispatch — called after each execution completes (from worker thread)
# ---------------------------------------------------------------------------

def dispatch_workflows(
    execution_dict: Dict[str, Any],
    workspace_name: str,
    enc_key: str = "",
) -> List[WorkflowResult]:
    """
    Evaluate all workflows for the workspace and execute those whose
    triggers match the completed execution.  Best-effort — never raises.
    Returns a list of WorkflowResult (may be empty).
    """
    import datetime

    ws_id = execution_dict.get("workspace_id") or ""
    command = execution_dict.get("command") or ""
    status = execution_dict.get("status") or ""

    workflows = list_workflows(ws_id)
    if not workflows:
        return []

    context = build_run_context(execution_dict, workspace_name, enc_key=enc_key)
    results: List[WorkflowResult] = []

    for wf in workflows:
        if not wf.get("enabled", True):
            continue
        triggers = wf.get("triggers") or []
        if not any(_matches_trigger(t, command, status) for t in triggers):
            continue

        wf_type = (wf.get("type") or "").lower()
        plugin_cls = WORKFLOW_REGISTRY.get(wf_type)
        if plugin_cls is None:
            results.append(WorkflowResult(
                workflow_id=wf.get("id", ""),
                workflow_name=wf.get("name", ""),
                workflow_type=wf_type,
                triggered_at=datetime.datetime.utcnow().isoformat(),
                status="failed",
                error=f"Unknown workflow type: '{wf_type}'",
            ))
            continue

        # Decrypt secrets before execution
        cfg = _json.loads(_json.dumps(wf.get("config") or {}))
        if enc_key:
            try:
                wf_dec = decrypt_workflow_secrets(wf, enc_key)
                cfg = wf_dec.get("config") or {}
            except Exception:
                pass

        cfg["_workflow_id"] = wf.get("id", "")
        cfg["_workflow_name"] = wf.get("name", "")

        plugin = plugin_cls()
        try:
            result = plugin.execute(cfg, context, enc_key=enc_key)
        except Exception as exc:
            result = WorkflowResult(
                workflow_id=wf.get("id", ""),
                workflow_name=wf.get("name", ""),
                workflow_type=wf_type,
                triggered_at=datetime.datetime.utcnow().isoformat(),
                status="failed",
                error=str(exc),
            )
        results.append(result)

    return results


# ---------------------------------------------------------------------------
# Re-encryption (called when portal password changes)
# ---------------------------------------------------------------------------

def reencrypt_workspace_secrets(
    workspace_id: str,
    old_password: str,
    new_password: str,
) -> int:
    """
    Re-encrypt every workflow secret in *workspace_id* with *new_password*.
    Returns the count of workflows updated.
    """
    from app.crypto import decrypt, encrypt

    workflows = list_workflows(workspace_id)
    count = 0
    for wf in workflows:
        cfg = wf.get("config") or {}
        wf_type = (wf.get("type") or "").lower()
        fields = _sensitive_fields_for(wf_type)
        changed = False
        for field_name in fields:
            val = cfg.get(field_name) or ""
            if val.startswith(_ENC_PREFIX):
                try:
                    plain = decrypt(val[len(_ENC_PREFIX):], old_password)
                    cfg[field_name] = _ENC_PREFIX + encrypt(plain, new_password)
                    changed = True
                except Exception:
                    pass
        if changed:
            wf["config"] = cfg
            try:
                _backend().save_workspace_workflow(workspace_id, wf)
            except AttributeError:
                pass
            count += 1
    return count
