"""Deterministic startup/readiness/liveness probe handler checks."""

import re
from typing import Any

from gpu_guard.validators import deployment_containers


PROBES = (
    ("startupProbe", "startup_probe", "Confirm that the application has finished starting."),
    ("readinessProbe", "readiness_probe", "Let Kubernetes determine when the Pod is ready to receive requests."),
    ("livenessProbe", "liveness_probe", "Let Kubernetes detect an unhealthy application and restart it."),
)
HANDLERS = ("httpGet", "tcpSocket", "exec")
MISSING = object()


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _port(value: Any) -> bool:
    if type(value) is int:
        return 1 <= value <= 65535
    # Named ports: <=15 lowercase alphanumeric/hyphen characters, at least
    # one letter, no leading/trailing or consecutive hyphens.
    return (
        isinstance(value, str) and len(value) <= 15
        and re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value) is not None
        and re.search(r"[a-z]", value) is not None
    )


def _handler_errors(name: str, handler: Any) -> list[str]:
    if not isinstance(handler, dict) or not handler:
        return [f"{name} = {handler!r}; expected non-empty mapping"]
    errors = []
    if name == "exec":
        command = handler.get("command", MISSING)
        if not isinstance(command, list) or not command or not all(_text(arg) for arg in command):
            observed = "missing" if command is MISSING else repr(command)
            errors.append(f"exec.command = {observed}; expected non-empty list of non-empty strings")
        return errors
    port = handler.get("port", MISSING)
    if not _port(port):
        observed = "missing" if port is MISSING else repr(port)
        errors.append(f"{name}.port = {observed}; expected integer 1..65535 or valid named port")
    if "host" in handler and not _text(handler["host"]):
        errors.append(f"{name}.host = {handler['host']!r}; expected non-empty string")
    if name == "httpGet":
        if "path" in handler and (not _text(handler["path"]) or not handler["path"].startswith("/")):
            errors.append(f"httpGet.path = {handler['path']!r}; expected non-empty path starting with /")
        if "scheme" in handler and handler["scheme"] not in ("HTTP", "HTTPS"):
            errors.append(f"httpGet.scheme = {handler['scheme']!r}; expected HTTP or HTTPS")
        if "httpHeaders" in handler:
            headers = handler["httpHeaders"]
            if (not isinstance(headers, list) or not headers or any(
                not isinstance(header, dict) or not _text(header.get("name"))
                or not _text(header.get("value")) for header in headers
            )):
                errors.append(f"httpGet.httpHeaders = {headers!r}; expected non-empty list of name/value strings")
    return errors


def validate_probes(manifest: Any) -> list[dict[str, str]]:
    """Return three independent checks for every regular Deployment container."""
    results = []
    for index, container in enumerate(deployment_containers(manifest)):
        location = f"spec.template.spec.containers[{index}] (name={container.get('name', '<unnamed>')!r})"
        for field, check, purpose in PROBES:
            probe = container.get(field, MISSING)
            if probe is MISSING:
                status = "WARN"
                evidence = f"{location}.{field} = missing"
                recommendation = f"Set {field} with httpGet, tcpSocket or exec. {purpose}"
            else:
                errors = []
                if not isinstance(probe, dict):
                    errors.append(f"expected mapping; observed {probe!r}")
                else:
                    handlers = [name for name in HANDLERS if name in probe]
                    if len(handlers) != 1:
                        errors.append(f"expected exactly one of httpGet, tcpSocket, exec; found {handlers!r}")
                    if "grpc" in probe:
                        errors.append("grpc handler is unsupported by this validator")
                    for name in handlers:
                        errors.extend(_handler_errors(name, probe[name]))
                status = "FAIL" if errors else "PASS"
                evidence = f"{location}.{field} = {probe!r}"
                if errors:
                    evidence += "; " + "; ".join(errors)
                recommendation = (
                    f"Configure exactly one non-empty httpGet, tcpSocket or exec handler in {field}; correct the fields identified in evidence."
                    if errors else f"No change required for {field} handler structure."
                )
            results.append({"check": check, "status": status,
                            "evidence": evidence, "recommendation": recommendation})
    return results
