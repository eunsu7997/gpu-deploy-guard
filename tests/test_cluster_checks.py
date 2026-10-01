"""Fake-only preflight tests: never connect to a real Kubernetes cluster."""

import json

import pytest

from gpu_guard.cluster_checks import CHECKS, cluster_check
from gpu_guard.kubectl import CommandResult


def node(ready="True", gpu="1"):
    status = {"conditions": [{"type": "Ready", "status": ready}], "allocatable": {}}
    if gpu is not None:
        status["allocatable"]["nvidia.com/gpu"] = gpu
    return {"metadata": {"name": "worker"}, "status": status}


def response(items):
    return CommandResult(0, json.dumps({"items": items}))


class FakeRunner:
    def __init__(self, available=True, context=None, nodes=None, pods=None):
        self.is_available = available
        self.results = {
            "context": context if context is not None else CommandResult(0, "dev-context\n"),
            "nodes": nodes if nodes is not None else response([node()]),
            "pods": pods if pods is not None else response([]),
        }
        self.calls = []

    def available(self):
        return self.is_available

    def run(self, query, *, context=None):
        self.calls.append((query, context))
        return self.results[query]


def report(runner):
    results = cluster_check(runner)
    assert [r["check"] for r in results] == list(CHECKS)
    for result in results:
        assert set(result) == {"check", "status", "evidence", "recommendation"}
        assert result["evidence"] and result["recommendation"]
    return {r["check"]: r for r in results}


def test_kubectl_missing_stops_all_commands():
    runner = FakeRunner(available=False)
    results = report(runner)
    assert results["kubectl_availability"]["status"] == "FAIL"
    assert "executable not found" in results["kubectl_availability"]["evidence"]
    assert all(r["status"] == "FAIL" for r in results.values())
    assert runner.calls == []


def test_context_and_api_success_and_context_pinning():
    runner = FakeRunner()
    results = report(runner)
    assert results["kubectl_availability"]["status"] == "PASS"
    assert results["current_context"]["status"] == "PASS"
    assert "dev-context" in results["current_context"]["evidence"]
    assert results["api_connection"]["status"] == "PASS"
    assert runner.calls == [("context", None), ("nodes", "dev-context"), ("pods", "dev-context")]


@pytest.mark.parametrize("context,evidence", [
    (CommandResult(0, " \n"), "empty"),
    (CommandResult(1, stderr="no context configured"), "no context configured"),
    (CommandResult(1, stderr="context timeout", timed_out=True), "timeout"),
])
def test_context_failure_blocks_api(context, evidence):
    runner = FakeRunner(context=context)
    results = report(runner)
    assert results["current_context"]["status"] == "FAIL"
    assert evidence in results["current_context"]["evidence"]
    assert "not executed" in results["api_connection"]["evidence"]
    assert runner.calls == [("context", None)]


@pytest.mark.parametrize("failure", [CommandResult(1, stderr="connection refused"),
    CommandResult(1, stderr="nodes timed out", timed_out=True), CommandResult(7)])
def test_api_failure_propagates_with_real_evidence(failure):
    results = report(FakeRunner(nodes=failure))
    assert results["api_connection"]["status"] == "FAIL"
    assert (failure.stderr or "exit code = 7") in results["api_connection"]["evidence"]
    assert results["node_ready"]["status"] == "FAIL"
    assert results["gpu_allocatable"]["status"] == "FAIL"
    assert "not executed" in results["node_ready"]["evidence"]
    assert results["nvidia_device_plugin"]["status"] == "WARN"


@pytest.mark.parametrize("nodes,status,count", [
    ([node(), node()], "PASS", 2), ([node(), node("False")], "WARN", 1),
    ([node("False")], "FAIL", 0), ([node("Unknown")], "FAIL", 0),
    ([{"status": {}}], "FAIL", 0), ([], "FAIL", 0),
])
def test_node_ready(nodes, status, count):
    result = report(FakeRunner(nodes=response(nodes)))["node_ready"]
    assert result["status"] == status
    assert f"nodes.total = {len(nodes)}" in result["evidence"]
    assert f"nodes.ready = {count}" in result["evidence"]


@pytest.mark.parametrize("gpu,status,total", [("1", "PASS", 1), (2, "PASS", 2),
    ("0", "WARN", 0), (None, "WARN", 0)])
def test_gpu_allocatable(gpu, status, total):
    results = report(FakeRunner(nodes=response([node(gpu=gpu)])))
    assert results["gpu_allocatable"]["status"] == status
    assert f"nvidia.com/gpu = {total}" in results["gpu_allocatable"]["evidence"]
    assert results["node_ready"]["status"] == "PASS"
    assert not any(r["status"] == "FAIL" for r in results.values())


def test_gpu_is_aggregate_allocatable_not_unused_capacity():
    results = report(FakeRunner(nodes=response([node(gpu="1"), node(gpu="2")])))
    assert "nvidia.com/gpu = 3" in results["gpu_allocatable"]["evidence"]


@pytest.mark.parametrize("gpu", ["bad", -1, True, None, {}, "1.5"])
def test_invalid_gpu_quantity(gpu):
    item = node()
    item["status"]["allocatable"]["nvidia.com/gpu"] = gpu
    result = report(FakeRunner(nodes=response([item])))["gpu_allocatable"]
    assert result["status"] == "FAIL"
    assert repr(gpu) in result["evidence"]


@pytest.mark.parametrize("metadata", [
    {"name": "nvidia-device-plugin-daemonset-random"},
    {"name": "custom", "labels": {"name": "nvidia-device-plugin-ds"}},
    {"name": "custom", "labels": {"app": "nvidia-device-plugin"}},
    {"name": "custom", "labels": {"app.kubernetes.io/name": "nvidia-device-plugin"}},
])
def test_device_plugin_by_name_or_labels(metadata):
    result = report(FakeRunner(pods=response([{"metadata": metadata}])))["nvidia_device_plugin"]
    assert result["status"] == "PASS"
    assert repr(metadata["name"]) in result["evidence"]


def test_device_plugin_missing_is_warn():
    result = report(FakeRunner(pods=response([{"metadata": {"name": "coredns"}}])))["nvidia_device_plugin"]
    assert result["status"] == "WARN"
    assert "not found" in result["evidence"]


@pytest.mark.parametrize("failure", [CommandResult(1, stderr="Forbidden: cannot list pods"),
    CommandResult(1, stderr="pods timeout", timed_out=True)])
def test_device_plugin_query_failure(failure):
    result = report(FakeRunner(pods=failure))["nvidia_device_plugin"]
    assert result["status"] == "FAIL"
    assert failure.stderr in result["evidence"]


@pytest.mark.parametrize("query", ["nodes", "pods"])
@pytest.mark.parametrize("raw", ["{", "", "null", "[]", '{}', '{"items": {}}', '{"items": [null]}'])
def test_malformed_json_and_list_shape(query, raw):
    results = report(FakeRunner(**{query: CommandResult(0, raw)}))
    targets = ("node_ready", "gpu_allocatable") if query == "nodes" else ("nvidia_device_plugin",)
    for target in targets:
        assert results[target]["status"] == "FAIL"
        assert "JSON" in results[target]["evidence"] or "expected" in results[target]["evidence"]


@pytest.mark.parametrize("item,check", [
    ({"status": None}, "node_ready"), ({"status": {"conditions": {}}}, "node_ready"),
    ({"status": {"conditions": [None]}}, "node_ready"),
    ({"status": {"allocatable": None}}, "gpu_allocatable"),
])
def test_malformed_node_fields(item, check):
    assert report(FakeRunner(nodes=response([item])))[check]["status"] == "FAIL"


def test_malformed_pod_metadata():
    result = report(FakeRunner(pods=response([{"metadata": None}])))["nvidia_device_plugin"]
    assert result["status"] == "FAIL"


def test_stderr_is_bounded_and_traceback_not_exposed():
    error = "Traceback (most recent call last):\n  internal stack details\nOSError: unavailable"
    result = report(FakeRunner(nodes=CommandResult(1, stderr=error)))["api_connection"]
    assert result["evidence"] == "OSError: unavailable"
    result = report(FakeRunner(nodes=CommandResult(1, stderr="x" * 1500)))["api_connection"]
    assert len(result["evidence"]) < 1300
    assert "truncated" in result["evidence"]
