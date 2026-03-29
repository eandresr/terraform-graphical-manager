"""
State Parser — parses the JSON output of `terraform state pull` to produce
a structured representation suitable for the state viewer UI.
"""
from typing import Any, Dict, List

_SENSITIVE_KEY_PATTERNS = (
    "password", "secret", "key", "token", "credential", "private",
    "cert", "auth", "pass", "pwd",
)


def parse_state(state_json: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parse raw state JSON (from `terraform state pull`) and return a
    structured summary.
    """
    if not state_json:
        return _empty_state()

    version = state_json.get("version")
    serial = state_json.get("serial")
    terraform_version = state_json.get("terraform_version")
    lineage = state_json.get("lineage")
    raw_resources = state_json.get("resources", [])

    resources: List[Dict[str, Any]] = []
    modules: Dict[str, List[Dict[str, Any]]] = {}

    for raw in raw_resources:
        res = _parse_resource(raw)
        resources.append(res)

        module = res["module"] or "root"
        modules.setdefault(module, []).append(res)

    return {
        "version": version,
        "serial": serial,
        "terraform_version": terraform_version,
        "lineage": lineage,
        "resource_count": len(resources),
        "resources": resources,
        "modules": modules,
        "module_names": sorted(modules.keys()),
    }


def _parse_resource(raw: Dict[str, Any]) -> Dict[str, Any]:
    resource_type = raw.get("type", "")
    name = raw.get("name", "")
    module = raw.get("module", None)
    mode = raw.get("mode", "managed")
    provider = raw.get("provider", "")

    instances: List[Dict[str, Any]] = []
    for inst in raw.get("instances", []):
        attrs = inst.get("attributes", {}) or {}
        sanitised = _sanitise_attributes(attrs)
        instances.append(
            {
                "index_key": inst.get("index_key"),
                "attributes": sanitised,
                "attribute_count": len(attrs),
            }
        )

    # Derive a display address
    prefix = f"{module}." if module else ""
    if mode == "data":
        address = f"{prefix}data.{resource_type}.{name}"
    else:
        address = f"{prefix}{resource_type}.{name}"

    return {
        "address": address,
        "module": module,
        "type": resource_type,
        "name": name,
        "mode": mode,
        "provider": _shorten_provider(provider),
        "instances": instances,
        "instance_count": len(instances),
    }


def _sanitise_attributes(attrs: Dict[str, Any]) -> Dict[str, Any]:
    """Mask attribute values whose keys suggest sensitive data."""
    result: Dict[str, Any] = {}
    for key, value in attrs.items():
        if _is_sensitive_key(key):
            result[key] = "****** (sensitive)"
        elif isinstance(value, dict):
            result[key] = _sanitise_attributes(value)
        else:
            result[key] = value
    return result


def _is_sensitive_key(key: str) -> bool:
    lower = key.lower()
    return any(pattern in lower for pattern in _SENSITIVE_KEY_PATTERNS)


def _shorten_provider(provider: str) -> str:
    """Convert 'registry.terraform.io/hashicorp/aws' → 'hashicorp/aws'."""
    if provider.startswith("registry.terraform.io/"):
        return provider[len("registry.terraform.io/"):]
    return provider


def _empty_state() -> Dict[str, Any]:
    return {
        "version": None,
        "serial": None,
        "terraform_version": None,
        "lineage": None,
        "resource_count": 0,
        "resources": [],
        "modules": {},
        "module_names": [],
    }
