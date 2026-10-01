"""Deterministic GPU resource limit validation for Deployments."""

from decimal import Decimal, InvalidOperation
from typing import Any


def deployment_containers(manifest: Any) -> list[dict[str, Any]]:
    """Validate the shared Deployment shape and return regular containers."""
    if not isinstance(manifest, dict) or manifest.get("kind") != "Deployment":
        raise ValueError(f"Expected kind Deployment; received {manifest!r}")
    node = manifest
    path = ""
    for key in ("spec", "template", "spec"):
        path = f"{path}.{key}" if path else key
        node = node.get(key)
        if not isinstance(node, dict):
            raise ValueError(f"{path}: expected mapping; received {node!r}")
    containers = node.get("containers")
    if not isinstance(containers, list) or not containers:
        raise ValueError(f"{path}.containers: expected non-empty list; received {containers!r}")
    for index, container in enumerate(containers):
        if not isinstance(container, dict):
            raise ValueError(f"{path}.containers[{index}]: expected mapping; received {container!r}")
    return containers


def gpu_limit_amount(value: Any) -> Decimal | None:
    """Shared numeric GPU limit interpretation used by static and workload checks."""
    if isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value))
        return amount if amount.is_finite() else None
    except InvalidOperation:
        return None


def validate_gpu_limits(manifest: Any) -> list[dict[str, str]]:
    """Return an evidence-backed result for each regular container."""
    containers = deployment_containers(manifest)
    results = []
    for index, container in enumerate(containers):
        location = f"spec.template.spec.containers[{index}]"
        resources = container.get("resources", {})
        limits = resources.get("limits", {}) if isinstance(resources, dict) else None
        location += f" (name={container.get('name', '<unnamed>')!r}).resources.limits"
        if not isinstance(limits, dict) or "nvidia.com/gpu" not in limits:
            status = "FAIL"
            evidence = f"{location}: nvidia.com/gpu is missing; observed limits={limits!r}"
        else:
            value = limits["nvidia.com/gpu"]
            amount = gpu_limit_amount(value)
            valid = amount is not None and amount >= 1
            status = "PASS" if valid else "FAIL"
            evidence = f"{location}['nvidia.com/gpu'] = {value!r}; required numeric value >= 1"
        results.append({
            "check": "gpu_resource_limit", "status": status, "evidence": evidence,
            "recommendation": "No change required for this GPU limit." if status == "PASS"
            else "Set resources.limits['nvidia.com/gpu'] to at least 1 for this container.",
        })
    return results
