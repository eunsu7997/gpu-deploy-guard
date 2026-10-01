"""Deterministic rule tests including evidence and container coverage."""

from copy import deepcopy

import pytest

from gpu_guard.validators import validate_gpu_limits


def deployment(limits):
    return {"kind": "Deployment", "spec": {"template": {"spec": {"containers": [
        {"name": "llm", "resources": {"limits": limits}},
    ]}}}}


@pytest.mark.parametrize("value,status", [
    (1, "PASS"), (2, "PASS"), ("1", "PASS"), (1.5, "PASS"),
    (0, "FAIL"), ("0", "FAIL"), (-1, "FAIL"), (0.5, "FAIL"),
    (None, "FAIL"), (True, "FAIL"), ("invalid", "FAIL"),
    ("NaN", "FAIL"), ("Infinity", "FAIL"), ({}, "FAIL"),
])
def test_gpu_values(value, status):
    manifest = deployment({"nvidia.com/gpu": value})
    original = deepcopy(manifest)
    result = validate_gpu_limits(manifest)[0]
    assert result["status"] == status
    assert result["check"] == "gpu_resource_limit"
    assert "containers[0]" in result["evidence"]
    assert "llm" in result["evidence"]
    assert repr(value) in result["evidence"]
    assert result["recommendation"]
    assert manifest == original
    assert validate_gpu_limits(manifest)[0] == result


@pytest.mark.parametrize("resources", [{}, {"limits": {}}, {"limits": {"cpu": "1"}}, None,
    {"limits": None}, {"limits": []}, []])
def test_missing_gpu_limit(resources):
    manifest = deployment({})
    manifest["spec"]["template"]["spec"]["containers"][0]["resources"] = resources
    result = validate_gpu_limits(manifest)[0]
    assert result["status"] == "FAIL"
    assert "nvidia.com/gpu is missing" in result["evidence"]
    assert "observed limits=" in result["evidence"]


def test_each_container_is_checked():
    manifest = deployment({"nvidia.com/gpu": 1})
    manifest["spec"]["template"]["spec"]["containers"].append({"name": "second"})
    results = validate_gpu_limits(manifest)
    assert [result["status"] for result in results] == ["PASS", "FAIL"]
    assert "containers[1]" in results[1]["evidence"]
    assert "second" in results[1]["evidence"]


@pytest.mark.parametrize("manifest", [None, [], {"kind": "Pod"}, {"kind": "Deployment"},
    {"kind": "Deployment", "spec": {"template": {"spec": {"containers": []}}}},
    {"kind": "Deployment", "spec": {"template": {"spec": {"containers": [None]}}}},
])
def test_invalid_shapes(manifest):
    with pytest.raises(ValueError):
        validate_gpu_limits(manifest)
