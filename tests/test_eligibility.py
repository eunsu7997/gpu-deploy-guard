"""Fake snapshots test scheduling filters and GPU consistency without kubectl."""

from copy import deepcopy

import pytest

from gpu_guard.cluster_checks import ClusterSnapshot
from gpu_guard.eligibility import constrain_gpu_feasibility, evaluate_node_eligibility
from gpu_guard.feasibility import evaluate_gpu_feasibility
from gpu_guard.validators import validate_gpu_limits


def node(name="node-a", gpu=2, ready=True, labels=None, unschedulable=False, taints=None):
    return {"metadata": {"name": name, "labels": labels or {}},
            "spec": {"unschedulable": unschedulable, "taints": taints or []},
            "status": {"conditions": [{"type": "Ready", "status": "True" if ready else "False"}],
                       "allocatable": {"nvidia.com/gpu": str(gpu)}}}


TAINT = {"key": "nvidia.com/gpu", "value": "true", "effect": "NoSchedule"}
EQUAL = {"key": "nvidia.com/gpu", "value": "true", "operator": "Equal", "effect": "NoSchedule"}
EXISTS = {"key": "nvidia.com/gpu", "operator": "Exists", "effect": "NoSchedule"}


def evaluate(nodes, fields=None, gpu=2):
    pod = {"containers": [{"name": "llm", "resources": {"limits": {"nvidia.com/gpu": gpu}}}]}
    pod.update(fields or {})
    manifest = {"kind": "Deployment", "spec": {"template": {"spec": pod}}}
    snapshot = ClusterSnapshot([], nodes)
    static = validate_gpu_limits(manifest)
    capacity = evaluate_gpu_feasibility(manifest, static, snapshot)
    eligibility = evaluate_node_eligibility(manifest, snapshot, capacity)
    constrained = constrain_gpu_feasibility(capacity, eligibility)
    return eligibility, constrained


@pytest.mark.parametrize("nodes,fields,status", [
    ([node()], {}, "PASS"),
    ([node(labels={"gpu": "a100"})], {"nodeSelector": {"gpu": "a100"}}, "PASS"),
    ([node(labels={"gpu": "rtx4090"})], {"nodeSelector": {"gpu": "a100"}}, "FAIL"),
    ([node(labels={"gpu": "a100", "region": "us"})], {"nodeSelector": {"gpu": "a100", "region": "kr"}}, "FAIL"),
    ([node()], {"nodeName": "node-a"}, "PASS"),
    ([node()], {"nodeName": "missing-node"}, "FAIL"),
    ([node(ready=False)], {"nodeName": "node-a"}, "FAIL"),
    ([node(gpu=4, unschedulable=True), node("node-b", gpu=1)], {}, "FAIL"),
    ([node(taints=[TAINT])], {}, "FAIL"),
    ([node(taints=[TAINT])], {"tolerations": [EQUAL]}, "PASS"),
    ([node(taints=[TAINT])], {"tolerations": [EXISTS]}, "PASS"),
    ([node(taints=[TAINT])], {"tolerations": [{**EQUAL, "key": "other"}]}, "FAIL"),
    ([node(taints=[TAINT])], {"tolerations": [{**EQUAL, "value": "false"}]}, "FAIL"),
    ([node(taints=[{**TAINT, "effect": "NoExecute"}])], {}, "FAIL"),
    ([node(taints=[{**TAINT, "effect": "PreferNoSchedule"}])], {}, "PASS"),
    ([node(gpu=4, labels={"gpu": "rtx4090"}), node("node-b", gpu=1, labels={"gpu": "a100"})], {"nodeSelector": {"gpu": "a100"}}, "FAIL"),
    ([node(gpu=2, labels={"gpu": "a100"}), node("node-b", gpu=4, labels={"gpu": "rtx4090"})], {"nodeSelector": {"gpu": "a100"}}, "PASS"),
    ([node(gpu=4, ready=False), node("node-b", gpu=1)], {}, "FAIL"),
    ([node(unschedulable=True)], {"nodeName": "node-a"}, "FAIL"),
    ([node(labels={"gpu": "rtx4090"}), node("node-b", labels={"gpu": "a100"})], {"nodeName": "node-a", "nodeSelector": {"gpu": "a100"}}, "FAIL"),
])
def test_required_constraints(nodes, fields, status):
    eligibility, feasibility = evaluate(nodes, fields)
    assert eligibility["status"] == status
    assert feasibility["status"] == status
    assert eligibility["check"] == "node_eligibility"
    assert set(eligibility) == {"check", "status", "evidence", "recommendation"}
    for detail in eligibility["evidence"]["nodes"].values():
        assert set(detail) >= {"name", "ready", "labels", "unschedulable", "taints", "allocatable_gpu", "eligible", "reasons"}
        if not detail["eligible"]:
            assert detail["reasons"]


def test_selector_mismatch_excludes_large_gpu_node_in_both_checks():
    eligibility, feasibility = evaluate([node(gpu=4, labels={"gpu": "rtx4090"}),
                                       node("node-b", gpu=1, labels={"gpu": "a100"})],
                                      {"nodeSelector": {"gpu": "a100"}})
    assert eligibility["evidence"]["constraint_candidates"] == ["node-b"]
    assert "label mismatch" in eligibility["evidence"]["nodes"]["node-a"]["reasons"][0]
    assert feasibility["evidence"]["max_gpu_on_eligible_node"] == 1
    assert feasibility["evidence"]["matching_ready_nodes"] == []
    assert feasibility["status"] == "FAIL"


def test_filter_order_and_prefer_no_schedule_non_blocking():
    eligibility, _ = evaluate([node(taints=[{**TAINT, "effect": "PreferNoSchedule"}])])
    assert eligibility["evidence"]["nodes"]["node-a"]["information"]
    eligibility, _ = evaluate([node(ready=False, unschedulable=True, taints=[TAINT])])
    assert eligibility["evidence"]["nodes"]["node-a"]["reasons"] == ["Ready != True"]


@pytest.mark.parametrize("effect", ["NoSchedule", "NoExecute"])
@pytest.mark.parametrize("operator", ["Equal", "Exists"])
def test_omitted_effect_matches_both_hard_effects(effect, operator):
    tol = {"key": "nvidia.com/gpu", "operator": operator}
    if operator == "Equal":
        tol["value"] = "true"
    eligibility, _ = evaluate([node(taints=[{**TAINT, "effect": effect}])], {"tolerations": [tol]})
    assert eligibility["status"] == "PASS"


def test_effect_mismatch_and_multiple_taints_require_all_matches():
    eligibility, _ = evaluate([node(taints=[{**TAINT, "effect": "NoExecute"}])], {"tolerations": [EQUAL]})
    assert eligibility["status"] == "FAIL"
    eligibility, _ = evaluate([node(taints=[TAINT, {"key": "dedicated", "effect": "NoSchedule"}])], {"tolerations": [EXISTS]})
    assert eligibility["status"] == "FAIL"


def test_empty_key_exists_is_wildcard_and_default_operator_equal():
    eligibility, _ = evaluate([node(taints=[TAINT])], {"tolerations": [{"operator": "Exists"}]})
    assert eligibility["status"] == "PASS"
    eligibility, _ = evaluate([node(taints=[TAINT])], {"tolerations": [{"key": "nvidia.com/gpu", "value": "true"}]})
    assert eligibility["status"] == "PASS"


@pytest.mark.parametrize("fields", [
    {"nodeSelector": None}, {"nodeSelector": {"gpu": True}}, {"nodeName": ""}, {"nodeName": None},
    {"tolerations": None}, {"tolerations": [None]},
    {"tolerations": [{"operator": "Wrong"}]}, {"tolerations": [{"operator": "Equal"}]},
    {"tolerations": [{"operator": "Exists", "value": "bad"}]},
    {"tolerations": [{"operator": "Exists", "effect": "wrong"}]},
    {"tolerations": [{"operator": "Exists", "tolerationSeconds": -1}]},
])
def test_invalid_workload_constraints_fail(fields):
    eligibility, feasibility = evaluate([node()], fields)
    assert eligibility["status"] == feasibility["status"] == "FAIL"
    assert eligibility["evidence"]["error"]


@pytest.mark.parametrize("path,value", [("labels", None), ("unschedulable", "true"), ("taints", None),
    ("taints", [{"key": "gpu", "effect": "wrong"}])])
def test_invalid_node_fields_fail(path, value):
    item = node()
    target = item["metadata"] if path == "labels" else item["spec"]
    target[path] = value
    eligibility, feasibility = evaluate([item])
    assert eligibility["status"] == feasibility["status"] == "FAIL"
    assert eligibility["evidence"]["error"]


def test_snapshot_normalizes_all_fields_without_mutating_raw_data():
    raw = [node(labels={"gpu": "a100"}, taints=[TAINT])]
    original = deepcopy(raw)
    snapshot = ClusterSnapshot([], raw)
    normalized = snapshot.scheduling_nodes[0].evidence()
    assert normalized == {"name": "node-a", "ready": True, "labels": {"gpu": "a100"},
                          "unschedulable": False, "taints": [TAINT], "allocatable_gpu": 2}
    normalized["labels"]["gpu"] = "changed"
    assert raw == original


def test_zero_gpu_still_needs_an_eligible_node():
    # Feasibility's zero-request rule stays independent, eligibility must fail.
    pod = {"containers": [{"name": "cpu"}], "nodeName": "missing"}
    manifest = {"kind": "Deployment", "spec": {"template": {"spec": pod}}}
    snapshot = ClusterSnapshot([], [node()])
    gpu = evaluate_gpu_feasibility(manifest, validate_gpu_limits(manifest), snapshot)
    eligibility = evaluate_node_eligibility(manifest, snapshot, gpu)
    assert gpu["status"] == "PASS"
    assert eligibility["status"] == "FAIL"
