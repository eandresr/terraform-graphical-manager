"""
Metrics Exporter — sends execution metrics to InfluxDB v2, Prometheus
Pushgateway, or Graphite after each workspace run completes.

Supported backends:
    influxdb    — InfluxDB v2 line-protocol via HTTP (built-in urllib)
    prometheus  — Prometheus Pushgateway (built-in urllib)
    graphite    — Graphite plaintext TCP/UDP socket

Only stdlib + the bundled Flask/requests-free stack is used so that no
extra dependency is required.  Each send is best-effort and all errors
are silently logged to stderr only.
"""
import datetime
import socket
import sys
from typing import Any, Dict, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def export_execution_metrics(
    execution_meta: Dict[str, Any],
    config,
    workspace_name: str = "",
) -> None:
    """
    Send metrics for a single completed execution.

    Args:
        execution_meta: the metadata dict (id, command, status,
                        duration_seconds, resource_counts, …)
        config:         the TFG_CONFIG instance (app.config.Config)
        workspace_name: human-readable name for tags / metric paths
    """
    if not getattr(config, "metrics_enabled", False):
        return
    backend = (getattr(config, "metrics_backend", "") or "").strip().lower()
    if not backend:
        return

    # Check per-workspace opt-out flag
    ws_id = execution_meta.get("workspace_id", "")
    try:
        from app.storage import get_backend as _gb
        ws_cfg = _gb().get_workspace_config(ws_id)
        if not ws_cfg.get("metrics_enabled", True):
            return
    except Exception:
        pass

    try:
        if backend == "influxdb":
            _send_influxdb(execution_meta, config, workspace_name)
        elif backend == "prometheus":
            _send_prometheus(execution_meta, config, workspace_name)
        elif backend == "graphite":
            _send_graphite(execution_meta, config, workspace_name)
    except Exception as exc:
        print(f"[TGM][metrics] Error exporting to {backend}: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Helpers: build common fields
# ---------------------------------------------------------------------------

def _resource_counts(meta: Dict[str, Any]) -> Dict[str, int]:
    rc = meta.get("resource_counts") or {}
    return {
        "add":     rc.get("create", 0),
        "change":  rc.get("update", 0) + rc.get("replace", 0),
        "destroy": rc.get("delete", 0),
        "no_op":   rc.get("no-op", 0),
    }


def _ts_ns(meta: Dict[str, Any]) -> int:
    """Return execution timestamp as nanoseconds since Unix epoch."""
    ts = meta.get("timestamp", "")
    try:
        dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1_000_000_000)
    except Exception:
        return int(datetime.datetime.utcnow().timestamp() * 1_000_000_000)


def _safe_tag(value: str) -> str:
    """Escape InfluxDB line-protocol tag values."""
    return value.replace(" ", r"\ ").replace(",", r"\,").replace("=", r"\=")


# ---------------------------------------------------------------------------
# InfluxDB v2 — line protocol
# ---------------------------------------------------------------------------

def _send_influxdb(
    meta: Dict[str, Any],
    config,
    workspace_name: str,
) -> None:
    url = (config.metrics_influxdb_url or "").rstrip("/")
    token = config.metrics_influxdb_token or ""
    org = config.metrics_influxdb_org or ""
    bucket = config.metrics_influxdb_bucket or "tgm"
    verify_ssl = getattr(config, "metrics_influxdb_verify_ssl", True)
    prefix = (config.metrics_prefix or "tgm").strip("_. ")

    if not url or not token:
        return

    measurement = f"{prefix}_execution"
    ws_id = _safe_tag(meta.get("workspace_id", "unknown"))
    ws_name = _safe_tag(workspace_name or ws_id)
    command = _safe_tag(meta.get("command", "plan"))
    status = _safe_tag(meta.get("status", "unknown"))

    tags = f"workspace_id={ws_id},workspace={ws_name},command={command},status={status}"

    duration = meta.get("duration_seconds")
    rc = _resource_counts(meta)
    fields_parts = []
    if duration is not None:
        fields_parts.append(f"duration_seconds={int(duration)}i")
    fields_parts += [
        f"resources_add={rc['add']}i",
        f"resources_change={rc['change']}i",
        f"resources_destroy={rc['destroy']}i",
        f"resources_no_op={rc['no_op']}i",
    ]
    if not fields_parts:
        return
    fields = ",".join(fields_parts)

    ts = _ts_ns(meta)
    line = f"{measurement},{tags} {fields} {ts}"

    write_url = f"{url}/api/v2/write?org={org}&bucket={bucket}&precision=ns"
    req = Request(
        write_url,
        data=line.encode(),
        headers={
            "Authorization": f"Token {token}",
            "Content-Type": "text/plain; charset=utf-8",
        },
        method="POST",
    )
    _do_http(req, verify_ssl=verify_ssl)


# ---------------------------------------------------------------------------
# Prometheus Pushgateway
# ---------------------------------------------------------------------------

def _send_prometheus(
    meta: Dict[str, Any],
    config,
    workspace_name: str,
) -> None:
    url = (config.metrics_prometheus_url or "").rstrip("/")
    if not url:
        return
    job = config.metrics_prometheus_job or "tgm"
    username = config.metrics_prometheus_username or ""
    password = config.metrics_prometheus_password or ""
    verify_ssl = getattr(config, "metrics_prometheus_verify_ssl", True)
    prefix = (config.metrics_prefix or "tgm").strip("_. ")

    ws_id = meta.get("workspace_id", "unknown")
    ws_name = workspace_name or ws_id
    command = meta.get("command", "plan")
    status = meta.get("status", "unknown")
    rc = _resource_counts(meta)
    duration = meta.get("duration_seconds")

    def _metric(name: str, value: float, help_text: str, mtype: str = "gauge") -> str:
        full = f"{prefix}_{name}"
        labels = (
            f'workspace_id="{ws_id}",workspace="{ws_name}",'
            f'command="{command}",status="{status}"'
        )
        lines = [
            f"# HELP {full} {help_text}",
            f"# TYPE {full} {mtype}",
            f"{full}{{{labels}}} {value}",
        ]
        return "\n".join(lines)

    parts = []
    if duration is not None:
        parts.append(_metric(
            "execution_duration_seconds", float(duration),
            "Duration of the Terraform execution in seconds",
        ))
    parts.append(_metric(
        "execution_resources_add", float(rc["add"]),
        "Number of resources to add in the plan",
    ))
    parts.append(_metric(
        "execution_resources_change", float(rc["change"]),
        "Number of resources to change in the plan",
    ))
    parts.append(_metric(
        "execution_resources_destroy", float(rc["destroy"]),
        "Number of resources to destroy in the plan",
    ))

    body = "\n\n".join(parts) + "\n"

    # Sanitize workspace_id for the URL path (no slashes/spaces)
    safe_ws = ws_id.replace("/", "_").replace(" ", "_")
    push_url = f"{url}/metrics/job/{job}/instance/{safe_ws}"

    headers: Dict[str, str] = {"Content-Type": "text/plain; version=0.0.4"}
    if username:
        import base64
        creds = base64.b64encode(f"{username}:{password}".encode()).decode()
        headers["Authorization"] = f"Basic {creds}"

    req = Request(push_url, data=body.encode(), headers=headers, method="POST")
    _do_http(req, verify_ssl=verify_ssl)


# ---------------------------------------------------------------------------
# Graphite (plaintext protocol over TCP or UDP)
# ---------------------------------------------------------------------------

def _send_graphite(
    meta: Dict[str, Any],
    config,
    workspace_name: str,
) -> None:
    host = config.metrics_graphite_host or ""
    if not host:
        return
    port = int(getattr(config, "metrics_graphite_port", 2003) or 2003)
    protocol = (getattr(config, "metrics_graphite_protocol", "tcp") or "tcp").lower()
    prefix = (config.metrics_prefix or "tgm").strip(". ").replace(" ", "_")

    ws_id = (meta.get("workspace_id", "unknown") or "").replace(".", "_").replace(" ", "_")
    ws_id = _sanitize_graphite_path(ws_id)
    command = _sanitize_graphite_path(meta.get("command", "plan"))
    status = _sanitize_graphite_path(meta.get("status", "unknown"))

    ts = int(_ts_ns(meta) / 1_000_000_000)

    rc = _resource_counts(meta)
    duration = meta.get("duration_seconds")

    base = f"{prefix}.workspaces.{ws_id}.{command}"
    lines = []
    if duration is not None:
        lines.append(f"{base}.duration_seconds {int(duration)} {ts}")
    lines += [
        f"{base}.resources_add {rc['add']} {ts}",
        f"{base}.resources_change {rc['change']} {ts}",
        f"{base}.resources_destroy {rc['destroy']} {ts}",
        f"{base}.status.{status} 1 {ts}",
    ]
    payload = "\n".join(lines) + "\n"

    if protocol == "udp":
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.sendto(payload.encode(), (host, port))
    else:
        with socket.create_connection((host, port), timeout=5) as s:
            s.sendall(payload.encode())


def _sanitize_graphite_path(value: str) -> str:
    """Replace characters that are illegal in Graphite metric paths."""
    return (value or "unknown").replace(".", "_").replace(" ", "_").replace("/", "_")


# ---------------------------------------------------------------------------
# Internal HTTP helper
# ---------------------------------------------------------------------------

def _do_http(req: Request, verify_ssl: bool = True) -> Optional[bytes]:
    import ssl
    ctx: Optional[ssl.SSLContext] = None
    if not verify_ssl:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    try:
        with urlopen(req, context=ctx, timeout=5) as resp:
            return resp.read()
    except URLError as exc:
        print(f"[TGM][metrics] HTTP error: {exc}", file=sys.stderr)
        return None
