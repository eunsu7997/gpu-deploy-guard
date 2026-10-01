"""CPU/memory presence, malformed-value, evidence and independence tests."""

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from gpu_guard.resource_validators import validate_cpu_memory_resources
from gpu_guard.validators import validate_gpu_limits


ROOT = Path(__file__).resolve().parents[1]


def deployment(resources):
    return {"kind": "Deployment", "spec": {"template": {"spec": {"containers": [
        {"name": "llm", "resources": resources},
    ]}}}}


@pytest.mark.parametrize("filename,expected,gpu_status", [
    ("good/resources_complete.yaml", ["PASS", "PASS"], "PASS"),
    ("bad/missing_cpu_limit.yaml", ["WARN", "PASS"], "PASS"),
    ("bad/missing_memory_request.yaml", ["PASS", "WARN"], "PASS"),
    ("bad/missing_resources.yaml", ["WARN", "WARN"], "FAIL"),
    ("bad/invalid_resource_value.yaml", ["FAIL", "FAIL"], "PASS"),
])
def test_examples(filename, expected, gpu_status):
    manifest = yaml.safe_load((ROOT / "examples" / filename).read_text(encoding="utf-8"))
    original = deepcopy(manifest)
    results = validate_cpu_memory_resources(manifest)
    assert [r["status"] for r in results] == expected
    assert [r["check"] for r in results] == ["cpu_resources", "memory_resources"]
    for result in results:
        assert set(result) == {"check", "status", "evidence", "recommendation"}
        assert "containers[0]" in result["evidence"]
        assert "workload" in result["evidence"]
        assert result["recommendation"]
    assert validate_gpu_limits(manifest)[0]["status"] == gpu_status
    assert manifest == original
    assert validate_cpu_memory_resources(manifest) == results


@pytest.mark.parametrize("resource,value", [("cpu", "500m"), ("memory", "4Gi")])
@pytest.mark.parametrize("has_request,has_limit,status", [
    (True, True, "PASS"), (True, False, "WARN"),
    (False, True, "WARN"), (False, False, "WARN"),
])
def test_presence_combinations(resource, value, has_request, has_limit, status):
    resources = {"requests": {}, "limits": {}}
    if has_request:
        resources["requests"][resource] = value
    if has_limit:
        resources["limits"][resource] = value
    result = next(r for r in validate_cpu_memory_resources(deployment(resources))
                  if r["check"] == f"{resource}_resources")
    assert result["status"] == status
    assert f"requests.{resource} = {repr(value) if has_request else 'missing'}" in result["evidence"]
    assert f"limits.{resource} = {repr(value) if has_limit else 'missing'}" in result["evidence"]
    if not has_request:
        assert "scheduler" in result["recommendation"]
    if not has_limit:
        assert f"limits.{resource}" in result["recommendation"]


@pytest.mark.parametrize("resource", ["cpu", "memory"])
@pytest.mark.parametrize("section", ["requests", "limits"])
@pytest.mark.parametrize("value", [None, "", " ", "five", "500mm", "8GB", -1, True, [], {}, "NaN", "Infinity"])
def test_invalid_values_fail_even_when_other_field_missing(resource, section, value):
    result = next(r for r in validate_cpu_memory_resources(deployment({section: {resource: value}}))
                  if r["check"] == f"{resource}_resources")
    assert result["status"] == "FAIL"
    assert f"{section}.{resource} = {value!r}" in result["evidence"]
    assert resource in result["recommendation"]
    assert "requests/limits" in result["recommendation"]


@pytest.mark.parametrize("value", [0, 2, 0.5, "500m", "128Mi", "8Gi", "1G", "1e3", "1E+3", "100u", ".5", "+1", "1."])
def test_supported_quantities(value):
    results = validate_cpu_memory_resources(deployment({
        "requests": {"cpu": value, "memory": value},
        "limits": {"cpu": value, "memory": value},
    }))
    assert [r["status"] for r in results] == ["PASS", "PASS"]


@pytest.mark.parametrize("resources,observed", [
    (None, "resources = None"), ([], "resources = []"),
    ({"requests": None}, "requests = None"), ({"limits": "wrong"}, "limits = 'wrong'"),
])
def test_malformed_parent_mapping(resources, observed):
    results = validate_cpu_memory_resources(deployment(resources))
    assert [r["status"] for r in results] == ["FAIL", "FAIL"]
    assert all(observed in r["evidence"] for r in results)


def test_multiple_containers_and_independent_checks():
    manifest = deployment({"requests": {"cpu": "bad", "memory": "1Gi"},
                           "limits": {"cpu": 2, "memory": "2Gi", "nvidia.com/gpu": 1}})
    manifest["spec"]["template"]["spec"]["containers"].append({"name": "sidecar"})
    results = validate_cpu_memory_resources(manifest)
    assert [r["status"] for r in results] == ["FAIL", "PASS", "WARN", "WARN"]
    assert "containers[1]" in results[2]["evidence"]
    assert "sidecar" in results[2]["evidence"]
    assert [r["status"] for r in validate_gpu_limits(manifest)] == ["PASS", "FAIL"]


@pytest.mark.parametrize("manifest", [None, [], {"kind": "Pod"}, {"kind": "Deployment"},
    {"kind": "Deployment", "spec": {"template": {"spec": {"containers": []}}}},
    {"kind": "Deployment", "spec": {"template": {"spec": {"containers": [None]}}}},
])
def test_invalid_deployment_shape(manifest):
    with pytest.raises(ValueError):
        validate_cpu_memory_resources(manifest)
