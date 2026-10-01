"""Required affinity syntax/semantics and eligibility integration with fake nodes."""

from copy import deepcopy

import pytest

from gpu_guard.affinity import REQUIRED, parse_required_node_affinity, evaluate_required_node_affinity
from gpu_guard.cluster_checks import ClusterSnapshot
from gpu_guard.eligibility import evaluate_node_eligibility, constrain_gpu_feasibility
from gpu_guard.feasibility import evaluate_gpu_feasibility
from gpu_guard.validators import validate_gpu_limits


def expr(operator="In", values=None, key="gpu"):
    expression = {"key": key, "operator": operator}
    if values is not None:
        expression["values"] = values
    return expression


def pod_affinity(terms):
    return {"affinity": {"nodeAffinity": {REQUIRED: {"nodeSelectorTerms": terms}}}}


def term(*expressions):
    return {"matchExpressions": list(expressions)}


def node(name="node-a", labels=None, gpu=4):
    return {"metadata": {"name": name, "labels": labels or {}}, "status": {
        "conditions": [{"type": "Ready", "status": "True"}], "allocatable": {"nvidia.com/gpu": str(gpu)}}}


def integrated(fields, nodes):
    pod = {"containers": [{"name": "llm", "resources": {"limits": {"nvidia.com/gpu": 2}}}], **fields}
    manifest = {"kind": "Deployment", "spec": {"template": {"spec": pod}}}
    snapshot = ClusterSnapshot([], nodes)
    gpu = evaluate_gpu_feasibility(manifest, validate_gpu_limits(manifest), snapshot)
    eligibility = evaluate_node_eligibility(manifest, snapshot, gpu)
    return eligibility, constrain_gpu_feasibility(gpu, eligibility)


@pytest.mark.parametrize("operator,values,labels,matched", [
    ("In", ["a100", "h100"], {"gpu": "a100"}, True),
    ("In", ["a100"], {"gpu": "rtx4090"}, False),
    ("In", ["a100"], {}, False),
    ("NotIn", ["rtx4090"], {"gpu": "a100"}, True),
    ("NotIn", ["rtx4090"], {"gpu": "rtx4090"}, False),
    ("NotIn", ["rtx4090"], {}, True),
    ("Exists", None, {"gpu": "a100"}, True), ("Exists", None, {}, False),
    ("DoesNotExist", None, {}, True), ("DoesNotExist", None, {"gpu": "a100"}, False),
    ("Gt", ["40"], {"gpu": "80"}, True), ("Gt", ["40"], {"gpu": "20"}, False),
    ("Gt", ["40"], {"gpu": "40"}, False), ("Gt", ["40"], {}, False),
    ("Lt", ["80"], {"gpu": "40"}, True), ("Lt", ["80"], {"gpu": "100"}, False),
    ("Lt", ["80"], {"gpu": "80"}, False), ("Lt", ["80"], {}, False),
    ("Gt", ["40"], {"gpu": "not-number"}, False),
    ("Lt", ["80"], {"gpu": "40.5"}, False),
    ("Gt", ["0"], {"gpu": "1_000"}, False),
    ("Gt", ["0"], {"gpu": " 1"}, False),
    ("Gt", ["0"], {"gpu": str(2**63)}, False),
    ("Gt", ["-2"], {"gpu": "-1"}, True),
    ("In", [""], {"gpu": ""}, True),
    ("Exists", [], {"gpu": ""}, True), ("DoesNotExist", [], {}, True),
])
def test_operator_semantics(operator, values, labels, matched):
    fields = pod_affinity([term(expr(operator, values))])
    rule = parse_required_node_affinity(fields)
    detail = evaluate_required_node_affinity(rule, labels)
    assert detail["matched"] == matched
    eligibility, gpu = integrated(fields, [node(labels=labels)])
    assert eligibility["status"] == gpu["status"] == ("PASS" if matched else "FAIL")
    if not matched:
        assert detail["evaluated_terms"][0]["reasons"]
        assert eligibility["evidence"]["nodes"]["node-a"]["reasons"]


def test_and_inside_term():
    fields = pod_affinity([term(expr(values=["a100"]), expr(values=["kr"], key="region"))])
    eligibility, _ = integrated(fields, [node(labels={"gpu": "a100", "region": "us"})])
    assert eligibility["status"] == "FAIL"
    detail = eligibility["evidence"]["nodes"]["node-a"]["required_node_affinity"]
    assert not detail["matched"]
    assert "region" in detail["evaluated_terms"][0]["reasons"][0]


@pytest.mark.parametrize("labels,matched,matched_term", [
    ({"gpu": "a100"}, True, 0), ({"gpu": "h100"}, True, 1),
    ({"gpu": "rtx4090"}, False, None),
])
def test_or_between_terms(labels, matched, matched_term):
    fields = pod_affinity([term(expr(values=["a100"])), term(expr(values=["h100"]))])
    detail = evaluate_required_node_affinity(parse_required_node_affinity(fields), labels)
    assert detail["matched"] == matched
    assert detail["matched_term"] == matched_term
    assert len(detail["evaluated_terms"]) == 2


@pytest.mark.parametrize("labels,status", [
    ({"region": "kr", "gpu": "a100"}, "PASS"),
    ({"region": "us", "gpu": "a100"}, "FAIL"),
    ({"region": "kr", "gpu": "rtx4090"}, "FAIL"),
])
def test_selector_and_affinity(labels, status):
    fields = {**pod_affinity([term(expr(values=["a100"]))]), "nodeSelector": {"region": "kr"}}
    eligibility, gpu = integrated(fields, [node(labels=labels)])
    assert eligibility["status"] == gpu["status"] == status


def test_node_name_no_fallback_when_affinity_mismatches():
    fields = {**pod_affinity([term(expr(values=["a100"]))]), "nodeName": "fixed"}
    eligibility, gpu = integrated(fields, [node("fixed", {"gpu": "rtx4090"}, 8), node("other", {"gpu": "a100"}, 4)])
    assert eligibility["status"] == gpu["status"] == "FAIL"
    assert "nodeName mismatch" in eligibility["evidence"]["nodes"]["other"]["reasons"][0]


def test_large_mismatching_node_never_supplies_capacity():
    fields = pod_affinity([term(expr(values=["a100"]))])
    eligibility, gpu = integrated(fields, [node("wrong", {"gpu": "rtx4090"}, 8), node("match", {"gpu": "a100"}, 1)])
    assert eligibility["status"] == gpu["status"] == "FAIL"
    assert eligibility["evidence"]["constraint_candidates"] == ["match"]
    assert gpu["evidence"]["max_gpu_on_eligible_node"] == 1


@pytest.mark.parametrize("terms", [
    None, [], {}, [None], [{"matchExpressions": None}],
    [term({"operator": "Exists"})], [term({"key": "gpu"})],
    [term(expr("Unknown"))], [term(expr("In"))], [term(expr("NotIn", []))],
    [term(expr("In", "a100"))], [term(expr("In", [True]))],
    [term(expr("Exists", ["a100"]))], [term(expr("DoesNotExist", ["a100"]))],
    [term(expr("Gt", []))], [term(expr("Lt", ["1", "2"]))],
    [term(expr("Gt", ["forty"]))], [term(expr("Lt", ["1.5"]))],
    [term(expr("Gt", [str(2**63)]))], [term(expr("Gt", ["1_000"]))],
    [{"matchFields": []}], [term(expr(values=["a100"])), term(expr("Unknown"))],
])
def test_invalid_rules_fail_in_eligibility_without_crash(terms):
    fields = pod_affinity(terms)
    with pytest.raises(ValueError):
        parse_required_node_affinity(fields)
    eligibility, gpu = integrated(fields, [node(labels={"gpu": "a100"})])
    assert eligibility["status"] == gpu["status"] == "FAIL"
    assert "affinity" in eligibility["evidence"]["error"]


@pytest.mark.parametrize("fields", [
    {"affinity": None}, {"affinity": []}, {"affinity": {"nodeAffinity": None}},
    {"affinity": {"nodeAffinity": {REQUIRED: None}}},
    {"affinity": {"nodeAffinity": {REQUIRED: {}}}},
])
def test_malformed_parent_structures(fields):
    eligibility, gpu = integrated(fields, [node()])
    assert eligibility["status"] == gpu["status"] == "FAIL"
    assert eligibility["evidence"]["error"]


def test_empty_term_matches_nothing_not_everything():
    rule = parse_required_node_affinity(pod_affinity([{}]))
    detail = evaluate_required_node_affinity(rule, {})
    assert not detail["matched"]
    assert "empty" in detail["evaluated_terms"][0]["reasons"][0]


@pytest.mark.parametrize("fields", [{}, {"affinity": {}}, {"affinity": {"nodeAffinity": {}}},
    {"affinity": {"nodeAffinity": {"preferredDuringSchedulingIgnoredDuringExecution": []}}}])
def test_no_required_affinity_preserves_existing_behavior(fields):
    assert parse_required_node_affinity(fields) is None
    eligibility, gpu = integrated(fields, [node()])
    assert eligibility["status"] == gpu["status"] == "PASS"


def test_no_mutation_and_deterministic_evaluation():
    fields = pod_affinity([term(expr(values=["a100"]))])
    original = deepcopy(fields)
    labels = {"gpu": "a100"}
    rule = parse_required_node_affinity(fields)
    detail = evaluate_required_node_affinity(rule, labels)
    assert evaluate_required_node_affinity(rule, labels) == detail
    assert fields == original and labels == {"gpu": "a100"}
