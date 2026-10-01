"""Live cluster preflight orchestration and deterministic JSON checks."""

import json
import re
from dataclasses import dataclass
from typing import Any

from gpu_guard.kubectl import CommandResult, CommandRunner


CHECKS = ("kubectl_availability", "current_context", "api_connection",
          "node_ready", "gpu_allocatable", "nvidia_device_plugin")


@dataclass
class ClusterSnapshot:
    results: list[dict[str, str]]
    nodes: list[dict[str, Any]] | None = None
    node_error: str | None = None

    @property
    def scheduling_nodes(self):
        """Expose normalized scheduling fields without changing preflight reports."""
        from gpu_guard.node_snapshot import normalize_nodes

        if self.nodes is None:
            raise ValueError(self.node_error or "node snapshot unavailable")
        return normalize_nodes(self.nodes)


def node_is_ready(node: dict[str, Any], index: int) -> bool:
    """Shared Ready interpretation; malformed conditions remain errors."""
    status = node.get("status", {})
    if not isinstance(status, dict):
        raise ValueError(f"nodes[{index}].status = {status!r}; expected object")
    conditions = status.get("conditions", [])
    if not isinstance(conditions, list) or any(not isinstance(c, dict) for c in conditions):
        raise ValueError(f"nodes[{index}].status.conditions = {conditions!r}; expected object list")
    return any(c.get("type") == "Ready" and c.get("status") == "True" for c in conditions)


def node_gpu_capacity(node: dict[str, Any], index: int) -> tuple[int, str]:
    """Shared allocatable parser; return capacity and evidence."""
    status = node.get("status", {})
    allocatable = status.get("allocatable", {}) if isinstance(status, dict) else None
    if not isinstance(allocatable, dict):
        raise ValueError(f"nodes[{index}].status.allocatable = {allocatable!r}; expected object")
    value = allocatable.get("nvidia.com/gpu", "0")
    if type(value) not in (str, int) or re.fullmatch(r"[0-9]+", str(value)) is None:
        raise ValueError(f"nodes[{index}].nvidia.com/gpu = {value!r}; expected non-negative integer")
    suffix = " (key missing)" if "nvidia.com/gpu" not in allocatable else ""
    return int(value), f"nodes[{index}].nvidia.com/gpu = {value!r}{suffix}"


def _result(check: str, status: str, evidence: str, recommendation: str) -> dict[str, str]:
    return dict(check=check, status=status, evidence=evidence, recommendation=recommendation)


def _error(result: CommandResult) -> str:
    # Keep real stderr, bounded for CLI output; do not print Python tracebacks.
    message = result.stderr.strip() or f"kubectl exit code = {result.returncode}"
    if "Traceback (most recent call last):" in message:
        message = message.splitlines()[-1]
    return message[:1200] + (" [truncated]" if len(message) > 1200 else "")


def _items(raw: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed JSON: {exc.msg} at line {exc.lineno}, column {exc.colno}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ValueError(f"expected JSON object with items list; observed {data!r}"[:1200])
    for index, item in enumerate(data["items"]):
        if not isinstance(item, dict):
            raise ValueError(f"items[{index}]: expected object; observed {item!r}")
    return data["items"]


def check_node_ready(nodes: list[dict[str, Any]]) -> dict[str, str]:
    ready = 0
    observed = []
    for index, node in enumerate(nodes):
        try:
            is_ready = node_is_ready(node, index)
        except ValueError as exc:
            return _result("node_ready", "FAIL", str(exc), "Check the node conditions response.")
        ready += int(is_ready)
        observed.append(f"nodes[{index}].Ready = {is_ready}")
    verdict = "FAIL" if ready == 0 else "PASS" if ready == len(nodes) else "WARN"
    return _result("node_ready", verdict,
                   f"nodes.total = {len(nodes)}; nodes.ready = {ready}; " + "; ".join(observed),
                   "No change required." if verdict == "PASS" else "Inspect nodes without a Ready=True condition.")


def check_gpu_allocatable(nodes: list[dict[str, Any]]) -> dict[str, str]:
    total = 0
    observed = []
    for index, node in enumerate(nodes):
        try:
            capacity, evidence = node_gpu_capacity(node, index)
        except ValueError as exc:
            return _result("gpu_allocatable", "FAIL", str(exc), "Check the reported GPU quantity.")
        total += capacity
        observed.append(evidence)
    verdict = "PASS" if total >= 1 else "WARN"
    return _result("gpu_allocatable", verdict, f"nvidia.com/gpu = {total}; " + "; ".join(observed),
                   "No change required." if total else "No NVIDIA GPU allocatable reported; a GPU-free development cluster is supported.")


def check_device_plugin(pods: list[dict[str, Any]]) -> dict[str, str]:
    matches = []
    for index, pod in enumerate(pods):
        metadata = pod.get("metadata", {})
        if not isinstance(metadata, dict) or not isinstance(metadata.get("labels", {}), dict):
            return _result("nvidia_device_plugin", "FAIL", f"pods[{index}].metadata = {metadata!r}; expected metadata/labels objects", "Check the kube-system Pod response.")
        name = metadata.get("name", "")
        labels = metadata.get("labels", {})
        candidates = [name, *(labels.get(key, "") for key in ("name", "app", "app.kubernetes.io/name"))]
        if any(isinstance(value, str) and re.search(r"(?:^|[-/])nvidia-device-plugin(?:$|[-/])", value) for value in candidates):
            matches.append(f"pods[{index}]: name={name!r}; labels={labels!r}")
    return _result("nvidia_device_plugin", "PASS" if matches else "WARN",
                   "kube-system NVIDIA device plugin Pods: " + "; ".join(matches) if matches
                   else f"kube-system pods.total = {len(pods)}; NVIDIA device plugin not found by name/labels",
                   "Pod presence confirmed; runtime health is not checked." if matches
                   else "Verify NVIDIA device plugin deployment and its namespace/name/labels if GPU support is expected.")


def _blocked(checks: tuple[str, ...], reason: str) -> list[dict[str, str]]:
    return [_result(check, "FAIL", f"not executed: {reason}", "Resolve the prerequisite failure and rerun cluster-check.") for check in checks]


def collect_cluster_snapshot(runner: CommandRunner) -> ClusterSnapshot:
    """Collect preflight results and retain the same nodes response for reuse."""
    nodes = None
    node_error = None
    if not runner.available():
        return ClusterSnapshot([_result(CHECKS[0], "FAIL", "kubectl executable not found", "Provide kubectl on PATH to run live checks.")
                ] + _blocked(CHECKS[1:], "kubectl executable not found"), node_error="kubectl executable not found")
    results = [_result(CHECKS[0], "PASS", "kubectl client available", "No change required.")]
    context = runner.run("context")
    name = context.stdout.strip()
    if context.returncode != 0 or context.timed_out or not name:
        reason = _error(context) if context.returncode != 0 or context.timed_out else "current context is empty"
        return ClusterSnapshot(results + [_result(CHECKS[1], "FAIL", reason, "Select a valid Kubernetes current context before rerunning.")
                          ] + _blocked(CHECKS[2:], f"current context unavailable: {reason}"), node_error=reason)
    results.append(_result(CHECKS[1], "PASS", f"current-context = {name}", "No change required."))
    response = runner.run("nodes", context=name)
    if response.returncode != 0 or response.timed_out:
        reason = _error(response)
        node_error = reason
        results.append(_result(CHECKS[2], "FAIL", reason, "Check API connectivity and permissions to list nodes."))
        results.extend(_blocked(CHECKS[3:5], f"node query failed: {reason}"))
    else:
        results.append(_result(CHECKS[2], "PASS", "kubectl get nodes succeeded", "No change required."))
        try:
            nodes = _items(response.stdout)
        except ValueError as exc:
            node_error = str(exc)
            results.extend([_result(check, "FAIL", str(exc), "Check the node JSON response.") for check in CHECKS[3:5]])
        else:
            results.extend([check_node_ready(nodes), check_gpu_allocatable(nodes)])
    response = runner.run("pods", context=name)
    if response.returncode != 0 or response.timed_out:
        results.append(_result(CHECKS[5], "FAIL", _error(response), "Check API connectivity and permissions to list kube-system Pods."))
    else:
        try:
            results.append(check_device_plugin(_items(response.stdout)))
        except ValueError as exc:
            results.append(_result(CHECKS[5], "FAIL", str(exc), "Check the Pod JSON response."))
    return ClusterSnapshot(results, nodes, node_error)


def cluster_check(runner: CommandRunner) -> list[dict[str, str]]:
    """Preserve the stage-5 public CLI/report contract."""
    return collect_cluster_snapshot(runner).results
