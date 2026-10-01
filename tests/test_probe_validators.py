"""Probe presence, handler structure, evidence and independence tests."""

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from gpu_guard.probe_validators import validate_probes
from gpu_guard.resource_validators import validate_cpu_memory_resources
from gpu_guard.validators import validate_gpu_limits


ROOT = Path(__file__).resolve().parents[1]
FIELDS = ("startupProbe", "readinessProbe", "livenessProbe")
CHECKS = ("startup_probe", "readiness_probe", "liveness_probe")


def deployment(container):
    return {"kind": "Deployment", "spec": {"template": {"spec": {"containers": [container]}}}}


@pytest.mark.parametrize("filename,statuses", [
    ("good/probes_complete.yaml", ["PASS", "PASS", "PASS"]),
    ("bad/missing_startup_probe.yaml", ["WARN", "PASS", "PASS"]),
    ("bad/missing_readiness_probe.yaml", ["PASS", "WARN", "PASS"]),
    ("bad/missing_liveness_probe.yaml", ["PASS", "PASS", "WARN"]),
    ("bad/invalid_probe_handler.yaml", ["FAIL", "FAIL", "FAIL"]),
])
def test_examples_and_existing_validators(filename, statuses):
    manifest = yaml.safe_load((ROOT / "examples" / filename).read_text(encoding="utf-8"))
    original = deepcopy(manifest)
    results = validate_probes(manifest)
    assert [r["check"] for r in results] == list(CHECKS)
    assert [r["status"] for r in results] == statuses
    for result in results:
        assert set(result) == {"check", "status", "evidence", "recommendation"}
        assert "containers[0]" in result["evidence"]
        assert "workload" in result["evidence"]
        assert result["recommendation"]
    assert validate_gpu_limits(manifest)[0]["status"] == "PASS"
    assert [r["status"] for r in validate_cpu_memory_resources(manifest)] == ["PASS", "PASS"]
    assert manifest == original
    assert validate_probes(manifest) == results


@pytest.mark.parametrize("field,index", list(zip(FIELDS, range(3))))
def test_missing_probe_is_warn(field, index):
    result = validate_probes(deployment({"name": "llm"}))[index]
    assert result["status"] == "WARN"
    assert f"{field} = missing" in result["evidence"]
    assert field in result["recommendation"]


@pytest.mark.parametrize("field,index", list(zip(FIELDS, range(3))))
@pytest.mark.parametrize("handler", [
    {"httpGet": {"port": 8080, "path": "/health"}},
    {"httpGet": {"port": "http"}},
    {"httpGet": {"port": 65535, "scheme": "HTTPS", "host": "localhost",
                 "httpHeaders": [{"name": "X-Health", "value": "yes"}]}},
    {"tcpSocket": {"port": 1}},
    {"tcpSocket": {"port": "health-port", "host": "localhost"}},
    {"exec": {"command": ["sh", "-c", "true"]}},
])
def test_valid_handlers(field, index, handler):
    result = validate_probes(deployment({"name": "llm", field: handler}))[index]
    assert result["status"] == "PASS"
    assert repr(handler) in result["evidence"]


@pytest.mark.parametrize("field,index", list(zip(FIELDS, range(3))))
@pytest.mark.parametrize("probe,reason", [
    (None, "expected mapping"), ([], "expected mapping"), ("", "expected mapping"),
    ({}, "expected exactly one"), ({"initialDelaySeconds": 10}, "expected exactly one"),
    ({"httpGet": None}, "expected non-empty mapping"),
    ({"tcpSocket": []}, "expected non-empty mapping"),
    ({"exec": {}}, "expected non-empty mapping"),
    ({"httpGet": {"path": "/health"}}, "httpGet.port = missing"),
    ({"tcpSocket": {"host": "localhost"}}, "tcpSocket.port = missing"),
    ({"exec": {"command": []}}, "exec.command = []"),
    ({"exec": {"command": "true"}}, "exec.command = 'true'"),
    ({"exec": {"command": ["sh", ""]}}, "exec.command"),
    ({"exec": {"command": [True]}}, "exec.command"),
    ({"httpGet": {"port": 8080, "path": ""}}, "httpGet.path = ''"),
    ({"httpGet": {"port": 8080, "path": "health"}}, "httpGet.path = 'health'"),
    ({"httpGet": {"port": 8080, "path": []}}, "httpGet.path = []"),
    ({"httpGet": {"port": 8080, "scheme": "ftp"}}, "httpGet.scheme = 'ftp'"),
    ({"tcpSocket": {"port": 8080, "host": ""}}, "tcpSocket.host = ''"),
    ({"httpGet": {"port": 8080, "httpHeaders": {}}}, "httpGet.httpHeaders = {}"),
    ({"httpGet": {"port": 8080, "httpHeaders": [{"name": "X"}]}}, "httpGet.httpHeaders"),
    ({"httpGet": {"port": 8080}, "exec": {"command": ["true"]}}, "expected exactly one"),
    ({"grpc": {"port": 8080}}, "grpc handler is unsupported"),
])
def test_invalid_probe_structure_and_values(field, index, probe, reason):
    result = validate_probes(deployment({"name": "llm", field: probe}))[index]
    assert result["status"] == "FAIL"
    assert reason in result["evidence"]
    assert repr(probe) in result["evidence"]
    assert field in result["recommendation"]


@pytest.mark.parametrize("handler", ["httpGet", "tcpSocket"])
@pytest.mark.parametrize("port", [None, "", " ", 0, -1, 65536, True, 1.5, "8080", "HTTP", "bad--port", "-http", "http-", "a" * 16, [], {}])
def test_invalid_ports(handler, port):
    result = validate_probes(deployment({"startupProbe": {handler: {"port": port}}}))[0]
    assert result["status"] == "FAIL"
    assert f"{handler}.port = {port!r}" in result["evidence"]


def test_containers_and_checks_are_independent():
    manifest = deployment({"name": "first", "startupProbe": {"exec": {"command": ["true"]}},
                           "readinessProbe": {}, "livenessProbe": {"tcpSocket": {"port": 8080}}})
    manifest["spec"]["template"]["spec"]["containers"].append({"name": "second"})
    results = validate_probes(manifest)
    assert [r["status"] for r in results] == ["PASS", "FAIL", "PASS", "WARN", "WARN", "WARN"]
    assert "containers[1]" in results[3]["evidence"]
    assert "second" in results[3]["evidence"]
    assert [r["status"] for r in validate_gpu_limits(manifest)] == ["FAIL", "FAIL"]
    assert all(r["status"] == "WARN" for r in validate_cpu_memory_resources(manifest))


@pytest.mark.parametrize("manifest", [None, [], {"kind": "Pod"}, {"kind": "Deployment"},
    {"kind": "Deployment", "spec": {"template": {"spec": {"containers": []}}}},
    {"kind": "Deployment", "spec": {"template": {"spec": {"containers": [None]}}}},
])
def test_invalid_deployment_shape(manifest):
    with pytest.raises(ValueError):
        validate_probes(manifest)
