"""Pure single-Pod scheduling constraint evaluation; no live command execution."""

from copy import deepcopy
from typing import Any

from gpu_guard.cluster_checks import ClusterSnapshot
from gpu_guard.node_snapshot import EFFECTS
from gpu_guard.affinity import parse_required_node_affinity, evaluate_required_node_affinity


def _tolerations(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError(f"tolerations = {value!r}; expected list")
    for item in value:
        if not isinstance(item, dict):
            raise ValueError(f"toleration = {item!r}; expected mapping")
        key, val = item.get("key", ""), item.get("value", "")
        operator, effect = item.get("operator", "Equal"), item.get("effect", "")
        if (not isinstance(key, str) or not isinstance(val, str)
                or operator not in ("Equal", "Exists") or effect not in (*EFFECTS, "")
                or (operator == "Equal" and not key) or (operator == "Exists" and val)):
            raise ValueError(f"toleration = {item!r}; invalid key/value/operator/effect")
        if "tolerationSeconds" in item and (type(item["tolerationSeconds"]) is not int or item["tolerationSeconds"] < 0):
            raise ValueError(f"toleration = {item!r}; tolerationSeconds must be a non-negative integer")
    return value


def tolerates(taint: dict[str, Any], toleration: dict[str, Any]) -> bool:
    """Omitted effect matches all; empty key with Exists matches all keys."""
    if toleration.get("effect", "") not in ("", taint["effect"]):
        return False
    key = toleration.get("key", "")
    if key and key != taint["key"]:
        return False
    return (toleration.get("operator", "Equal") == "Exists"
            or (key == taint["key"] and toleration.get("value", "") == taint.get("value", "")))


def evaluate_node_eligibility(
    manifest: dict[str, Any], snapshot: ClusterSnapshot, gpu_feasibility: dict[str, Any],
) -> dict[str, Any]:
    """Filter nodes in the documented order, retaining first exclusion reasons."""
    pod = manifest["spec"]["template"]["spec"]
    selector = pod.get("nodeSelector", {})
    node_name = pod.get("nodeName")
    required = gpu_feasibility["evidence"]["workload_gpu_request"]
    evidence: dict[str, Any] = {
        "workload": {"node_selector": selector, "node_name": node_name, "gpu_request": required,
                     "tolerations": pod.get("tolerations", [])},
        "nodes": {}, "constraint_candidates": [], "eligible_nodes": [],
        "prefer_no_schedule_policy": "non-blocking information",
    }

    def result(status: str, recommendation: str) -> dict[str, Any]:
        return dict(check="node_eligibility", status=status, evidence=evidence, recommendation=recommendation)

    try:
        if not isinstance(selector, dict) or any(not isinstance(k, str) or not k or not isinstance(v, str) for k, v in selector.items()):
            raise ValueError(f"nodeSelector = {selector!r}; expected string mapping")
        if "nodeName" in pod and (not isinstance(node_name, str) or not node_name.strip()):
            raise ValueError(f"nodeName = {node_name!r}; expected non-empty string")
        tolerations = _tolerations(pod.get("tolerations", []))
        affinity = parse_required_node_affinity(pod)
        if affinity is not None:
            evidence["workload"]["required_node_affinity"] = pod["affinity"]["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"]
        if "error" in gpu_feasibility["evidence"]:
            raise ValueError(gpu_feasibility["evidence"]["error"])
        nodes = snapshot.scheduling_nodes
    except ValueError as exc:
        evidence["error"] = str(exc)
        return result("FAIL", "Resolve the workload or node snapshot error reported in evidence.")
    if node_name is not None and not any(node.name == node_name for node in nodes):
        evidence["error"] = f"requested nodeName = {node_name!r} does not exist"
    for node in nodes:
        reasons, information = [], []
        affinity_evidence = None
        if not node.ready:
            reasons.append("Ready != True")
        elif node_name is not None and node.name != node_name:
            reasons.append(f"nodeName mismatch: expected {node_name!r}, observed {node.name!r}")
        else:
            for key, value in selector.items():
                if node.labels.get(key) != value:
                    reasons.append(f"label mismatch: {key} expected={value!r}, observed={node.labels.get(key, '<missing>')!r}")
            if not reasons and affinity is not None:
                affinity_evidence = evaluate_required_node_affinity(affinity, node.labels)
                if not affinity_evidence["matched"]:
                    for term in affinity_evidence["evaluated_terms"]:
                        reasons.extend(f"term[{term['term']}]: {reason}" for reason in term["reasons"])
            if not reasons and node.unschedulable:
                reasons.append("unschedulable=true")
            if not reasons:
                for taint in node.taints:
                    if any(tolerates(taint, t) for t in tolerations):
                        continue
                    if taint["effect"] == "PreferNoSchedule":
                        information.append(f"untolerated PreferNoSchedule (non-blocking): {taint!r}")
                    else:
                        reasons.append(f"untolerated {taint['effect']} taint: {taint!r}")
        if not reasons:
            evidence["constraint_candidates"].append(node.name)
            if node.allocatable_gpu < required:
                reasons.append(f"GPU capacity insufficient: allocatable={node.allocatable_gpu}, required={required}")
        detail = node.evidence()
        detail.update(eligible=not reasons, reasons=reasons, information=information)
        if affinity_evidence is not None:
            detail["required_node_affinity"] = affinity_evidence
        evidence["nodes"][node.name] = detail
        if not reasons:
            evidence["eligible_nodes"].append(node.name)
    if evidence["eligible_nodes"]:
        return result("PASS", "A Ready node meets the supported scheduling constraints and GPU capacity; placement is not guaranteed.")
    return result("FAIL", "No Ready node satisfies the workload scheduling constraints and GPU capacity.")


def constrain_gpu_feasibility(gpu: dict[str, Any], eligibility: dict[str, Any]) -> dict[str, Any]:
    """Apply the eligibility candidate set to the already extracted GPU request."""
    result = deepcopy(gpu)
    evidence = result["evidence"]
    detail = eligibility["evidence"]
    capacities = {name: detail["nodes"][name]["allocatable_gpu"] for name in detail["constraint_candidates"]}
    evidence["eligible_node_capacities"] = capacities
    evidence["max_gpu_on_eligible_node"] = max(capacities.values(), default=0) if "error" not in detail else None
    evidence["matching_ready_nodes"] = detail["eligible_nodes"].copy()
    if "error" in evidence:
        return result
    if "error" in detail:
        result["status"] = "FAIL"
        evidence["error"] = detail["error"]
        result["recommendation"] = "Resolve the node eligibility error before GPU feasibility can pass."
    elif evidence["workload_gpu_request"] >= 1 and not detail["eligible_nodes"]:
        result["status"] = "FAIL"
        result["recommendation"] = "No single eligible Ready node exposes enough allocatable NVIDIA GPUs for this Pod."
    return result
