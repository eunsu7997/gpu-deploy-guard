"""Independent CPU and memory presence/quantity-format checks."""

import re
from typing import Any

from gpu_guard.validators import deployment_containers


# Kubernetes quantity syntax: decimal number with an optional SI, binary SI,
# or decimal exponent suffix. This check does not normalize or compare amounts.
# Reference: kubernetes/apimachinery pkg/api/resource/quantity.go.
QUANTITY = re.compile(
    r"\+?(?P<number>(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+))"
    r"(?:[numkMGTPE]|[KMGTPE]i|[eE][+-]?[0-9]+)?"
)
MISSING = object()


def valid_quantity(value: Any) -> bool:
    """Accept non-negative quantity syntax; reject empty/non-scalar values."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return False
    return QUANTITY.fullmatch(str(value)) is not None


def _field(resources: Any, section: str, resource: str) -> tuple[Any, str | None]:
    """Distinguish absent fields from malformed parent mappings and values."""
    if resources is MISSING:
        return MISSING, None
    if not isinstance(resources, dict):
        return MISSING, f"resources = {resources!r}; expected mapping"
    mapping = resources.get(section, MISSING)
    if mapping is MISSING:
        return MISSING, None
    if not isinstance(mapping, dict):
        return MISSING, f"{section} = {mapping!r}; expected mapping"
    value = mapping.get(resource, MISSING)
    if value is not MISSING and not valid_quantity(value):
        return value, f"{section}.{resource} = {value!r}; expected non-negative Kubernetes quantity"
    return value, None


def _display(value: Any) -> str:
    return "missing" if value is MISSING else repr(value)


def validate_cpu_memory_resources(manifest: Any) -> list[dict[str, str]]:
    """Return separate CPU and memory results per container; FAIL overrides WARN."""
    results = []
    for index, container in enumerate(deployment_containers(manifest)):
        location = f"spec.template.spec.containers[{index}] (name={container.get('name', '<unnamed>')!r})"
        resources = container.get("resources", MISSING)
        for resource in ("cpu", "memory"):
            request, request_error = _field(resources, "requests", resource)
            limit, limit_error = _field(resources, "limits", resource)
            errors = [error for error in (request_error, limit_error) if error]
            label = "CPU" if resource == "cpu" else "Memory"
            if errors:
                status = "FAIL"
                recommendation = f"Correct {resource} requests/limits to non-empty, non-negative Kubernetes quantities and use mapping objects."
            elif request is MISSING or limit is MISSING:
                status = "WARN"
                suggestions = []
                if request is MISSING:
                    suggestions.append(f"Set requests.{resource} so the scheduler knows the workload's minimum {resource} requirement.")
                if limit is MISSING:
                    suggestions.append(f"Set limits.{resource} to define the workload's maximum {resource} usage.")
                recommendation = " ".join(suggestions)
            else:
                status = "PASS"
                recommendation = f"No change required for {label} resource presence and format."
            evidence = (
                f"{location}.resources: requests.{resource} = {_display(request)}; "
                f"limits.{resource} = {_display(limit)}"
            )
            if errors:
                evidence += "; " + "; ".join(errors)
            results.append({
                "check": f"{resource}_resources", "status": status,
                "evidence": evidence, "recommendation": recommendation,
            })
    return results
