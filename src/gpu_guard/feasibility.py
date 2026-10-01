"""Single-Pod GPU capacity comparison, without I/O or subprocess calls."""

from typing import Any

from gpu_guard.cluster_checks import ClusterSnapshot, node_gpu_capacity, node_is_ready
from gpu_guard.validators import deployment_containers, gpu_limit_amount


def evaluate_gpu_feasibility(
    manifest: Any, static_results: list[dict[str, str]], snapshot: ClusterSnapshot,
) -> dict[str, Any]:
    """Compare regular-container GPU sum against each single Ready node."""
    evidence: dict[str, Any] = {
        "workload_gpu_request": 0, "container_requests": [],
        "ready_nodes": {}, "excluded_not_ready_nodes": {}, "max_gpu_on_ready_node": None,
        "comparison": "Node allocatable GPU capacity; not free GPU inventory",
    }

    def result(status: str, recommendation: str) -> dict[str, Any]:
        return dict(check="gpu_feasibility", status=status, evidence=evidence, recommendation=recommendation)

    gpu_results = [r for r in static_results if r["check"] == "gpu_resource_limit"]
    containers = deployment_containers(manifest)
    if len(gpu_results) != len(containers):
        evidence["error"] = "GPU static results do not cover all containers"
        return result("FAIL", "Run GPU Static Validation before feasibility evaluation.")
    total = 0
    for index, container in enumerate(containers):
        resources = container.get("resources", {})
        limits = resources.get("limits", {}) if isinstance(resources, dict) else None
        item = {"index": index, "name": container.get("name", "<unnamed>")}
        if not isinstance(limits, dict):
            evidence["error"] = gpu_results[index]["evidence"]
            return result("FAIL", "Correct the resources/limits structure reported by Static Validation.")
        if "nvidia.com/gpu" not in limits:
            item.update(gpu_request=0, evidence="nvidia.com/gpu missing; interpreted as 0")
        else:
            value = limits["nvidia.com/gpu"]
            item["observed_value"] = value
            if gpu_results[index]["status"] == "FAIL":
                evidence["error"] = gpu_results[index]["evidence"]
                evidence["container_requests"].append(item)
                return result("FAIL", "Correct the GPU value reported by Static Validation.")
            amount = gpu_limit_amount(value)
            if amount is None or amount < 1 or amount != amount.to_integral_value():
                evidence["error"] = f"containers[{index}].nvidia.com/gpu = {value!r}; single-Pod feasibility requires whole GPUs"
                evidence["container_requests"].append(item)
                return result("FAIL", "Use a positive whole-number NVIDIA GPU limit; fractional GPU sharing is unsupported.")
            item["gpu_request"] = int(amount)
            total += int(amount)
        evidence["container_requests"].append(item)
    evidence["workload_gpu_request"] = total
    if snapshot.nodes is None:
        evidence["error"] = f"live feasibility not executed: {snapshot.node_error or 'node snapshot unavailable'}"
        return result("FAIL", "Resolve the cluster node query failure and rerun workload-check.")
    if total == 0:
        evidence["max_gpu_on_ready_node"] = 0
        evidence["scheduling"] = "GPU scheduling not required"
        return result("PASS", "No GPU capacity is required for this Pod; review independent Static/Preflight results.")
    try:
        for index, node in enumerate(snapshot.nodes):
            metadata = node.get("metadata")
            name = metadata.get("name") if isinstance(metadata, dict) else None
            if not isinstance(name, str) or not name.strip():
                raise ValueError(f"nodes[{index}].metadata.name = {name!r}; expected non-empty node name")
            if name in evidence["ready_nodes"] or name in evidence["excluded_not_ready_nodes"]:
                raise ValueError(f"duplicate node name = {name!r}")
            ready = node_is_ready(node, index)
            capacity, _ = node_gpu_capacity(node, index)
            target = "ready_nodes" if ready else "excluded_not_ready_nodes"
            evidence[target][name] = capacity
    except ValueError as exc:
        evidence["error"] = str(exc)
        return result("FAIL", "Correct or re-query the malformed node snapshot.")
    maximum = max(evidence["ready_nodes"].values(), default=0)
    evidence["max_gpu_on_ready_node"] = maximum
    evidence["matching_ready_nodes"] = [name for name, capacity in evidence["ready_nodes"].items() if capacity >= total]
    if maximum >= total:
        return result("PASS", "A single Ready node exposes enough allocatable NVIDIA GPU capacity; actual scheduling is not guaranteed.")
    return result("FAIL", "No single Ready node exposes enough allocatable NVIDIA GPUs for this Pod.")
