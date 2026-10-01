"""Pure single-node feasibility tests; never execute kubectl."""

from copy import deepcopy

import pytest

from gpu_guard.cluster_checks import ClusterSnapshot, check_gpu_allocatable
from gpu_guard.feasibility import evaluate_gpu_feasibility
from gpu_guard.validators import validate_gpu_limits


def deployment(values):
    containers = []
    for index, value in enumerate(values):
        limits = {} if value == "missing" else {"nvidia.com/gpu": value}
        containers.append({"name": f"container-{index}", "resources": {"limits": limits}})
    return {"kind": "Deployment", "spec": {"template": {"spec": {"containers": containers}}}}


def node(name, gpu, ready=True):
    return {"metadata": {"name": name}, "status": {
        "conditions": [{"type": "Ready", "status": "True" if ready else "False"}],
        "allocatable": {} if gpu is None else {"nvidia.com/gpu": gpu},
    }}


def evaluate(values, nodes):
    manifest = deployment(values)
    return evaluate_gpu_feasibility(manifest, validate_gpu_limits(manifest), ClusterSnapshot([], nodes))


@pytest.mark.parametrize("values,nodes,status,gpu_request,maximum", [
    (["missing"], [], "PASS", 0, 0),
    ([1], [node("node-a", "1")], "PASS", 1, 1),
    ([2], [node("node-a", "2")], "PASS", 2, 2),
    ([2], [node("node-a", "1"), node("node-b", "1")], "FAIL", 2, 1),
    ([1], [node("node-a", None)], "FAIL", 1, 0),
    ([2], [node("node-a", "4", False), node("node-b", "1")], "FAIL", 2, 1),
    ([1], [node("node-a", "4", False), node("node-b", "1")], "PASS", 1, 1),
    ([1, 1], [node("node-a", "2")], "PASS", 2, 2),
    ([1, 1], [node("node-a", "1"), node("node-b", "1")], "FAIL", 2, 1),
    ([2], [node("node-a", "2"), node("node-b", "0")], "PASS", 2, 2),
    ([1], [node("node-a", "4", False)], "FAIL", 1, 0),
    ([1], [], "FAIL", 1, 0),
])
def test_single_ready_node_capacity(values, nodes, status, gpu_request, maximum):
    result = evaluate(values, nodes)
    assert result["status"] == status
    assert result["check"] == "gpu_feasibility"
    assert result["evidence"]["workload_gpu_request"] == gpu_request
    assert result["evidence"]["max_gpu_on_ready_node"] == maximum
    assert result["recommendation"]


def test_split_capacity_never_passes_on_cluster_total():
    result = evaluate([2], [node("node-a", "1"), node("node-b", "1")])
    assert result["status"] == "FAIL"
    assert result["evidence"]["ready_nodes"] == {"node-a": 1, "node-b": 1}
    assert result["evidence"]["matching_ready_nodes"] == []
    assert "No single Ready node" in result["recommendation"]


def test_notready_is_excluded_from_maximum():
    result = evaluate([2], [node("not-ready", "4", False), node("ready", "1")])
    assert result["evidence"]["ready_nodes"] == {"ready": 1}
    assert result["evidence"]["excluded_not_ready_nodes"] == {"not-ready": 4}
    assert result["evidence"]["max_gpu_on_ready_node"] == 1


def test_missing_request_zero_preserves_independent_static_fail():
    manifest = deployment(["missing"])
    static = validate_gpu_limits(manifest)
    assert static[0]["status"] == "FAIL"
    result = evaluate_gpu_feasibility(manifest, static, ClusterSnapshot([], []))
    assert result["status"] == "PASS"
    assert result["evidence"]["workload_gpu_request"] == 0
    assert "missing" in result["evidence"]["container_requests"][0]["evidence"]
    assert result["evidence"]["scheduling"] == "GPU scheduling not required"


@pytest.mark.parametrize("value", ["bad", None, True, -1, 0, "NaN", {}])
def test_invalid_gpu_reuses_static_failure(value):
    manifest = deployment([value])
    static = validate_gpu_limits(manifest)
    assert static[0]["status"] == "FAIL"
    result = evaluate_gpu_feasibility(manifest, static, ClusterSnapshot([], [node("a", "8")]))
    assert result["status"] == "FAIL"
    assert result["evidence"]["error"] == static[0]["evidence"]


def test_fractional_gpu_unsupported_without_changing_static_contract():
    manifest = deployment([1.5])
    static = validate_gpu_limits(manifest)
    assert static[0]["status"] == "PASS"
    result = evaluate_gpu_feasibility(manifest, static, ClusterSnapshot([], [node("a", "8")]))
    assert result["status"] == "FAIL"
    assert "whole GPUs" in result["evidence"]["error"]


def test_missing_live_snapshot_is_not_zero_gpu_inventory():
    for values in ([1], ["missing"]):
        manifest = deployment(values)
        result = evaluate_gpu_feasibility(manifest, validate_gpu_limits(manifest),
                                          ClusterSnapshot([], node_error="kubectl executable not found"))
        assert result["status"] == "FAIL"
        assert "not executed" in result["evidence"]["error"]


def test_cluster_gpu_warn_but_workload_gpu_fail():
    nodes = [node("cpu-node", None)]
    assert check_gpu_allocatable(nodes)["status"] == "WARN"
    assert evaluate([1], nodes)["status"] == "FAIL"


@pytest.mark.parametrize("nodes", [
    [{"status": {}}],
    [node("a", "bad")],
    [{"metadata": {"name": "a"}, "status": {"conditions": None}}],
    [node("a", "1"), node("a", "1")],
])
def test_malformed_nodes_fail(nodes):
    result = evaluate([1], nodes)
    assert result["status"] == "FAIL"
    assert result["evidence"]["error"]


def test_deterministic_and_no_mutation():
    manifest = deployment(["1", 1])
    snapshot = ClusterSnapshot([], [node("node-a", "2")])
    static = validate_gpu_limits(manifest)
    original = deepcopy((manifest, static, snapshot))
    result = evaluate_gpu_feasibility(manifest, static, snapshot)
    assert evaluate_gpu_feasibility(manifest, static, snapshot) == result
    assert (manifest, static, snapshot) == original


def test_static_coverage_required():
    result = evaluate_gpu_feasibility(deployment([1]), [], ClusterSnapshot([], []))
    assert result["status"] == "FAIL"
    assert "cover all containers" in result["evidence"]["error"]
