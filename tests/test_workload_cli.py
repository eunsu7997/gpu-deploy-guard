"""Fake live reads and actual CLI dispatch, without a Kubernetes dependency."""

import json
from pathlib import Path

import pytest
import yaml

from gpu_guard.cli import main
from gpu_guard.kubectl import CommandResult


ROOT = Path(__file__).resolve().parents[1]


class FakeRunner:
    def __init__(self, available=True, error=None, raw=None, nodes=None):
        self.is_available = available
        self.error = error
        self.raw = raw
        self.nodes = nodes
        self.calls = []

    def available(self):
        return self.is_available

    def run(self, query, *, context=None):
        self.calls.append((query, context))
        if query == "context":
            return CommandResult(0, "fake-context")
        if query == "nodes":
            if self.error:
                return CommandResult(1, stderr=self.error)
            if self.raw is not None:
                return CommandResult(0, self.raw)
            if self.nodes is not None:
                return CommandResult(0, json.dumps({"items": self.nodes}))
            return CommandResult(0, json.dumps({"items": [{"metadata": {"name": "node-a"}, "status": {
                "conditions": [{"type": "Ready", "status": "True"}], "allocatable": {"nvidia.com/gpu": "2"},
            }}]}))
        assert query == "pods"
        return CommandResult(0, '{"items": []}')


@pytest.mark.parametrize("runner,code", [
    (FakeRunner(), 0), (FakeRunner(available=False), 1),
    (FakeRunner(error="connection refused"), 1), (FakeRunner(raw="{"), 1),
])
def test_workload_cli_order_node_query_reuse_and_errors(monkeypatch, capsys, runner, code):
    monkeypatch.setattr("gpu_guard.cli.KubectlRunner", lambda: runner)
    assert main(["workload-check", "examples/good/probes_complete.yaml"]) == code
    results = json.loads(capsys.readouterr().out)
    assert len(results) == 14
    assert results[0]["check"] == "gpu_resource_limit"
    assert results[6]["check"] == "kubectl_availability"
    assert results[-2]["check"] == "node_eligibility"
    assert results[-1]["check"] == "gpu_feasibility"
    assert results[-1]["status"] == ("PASS" if code == 0 else "FAIL")
    assert sum(query == "nodes" for query, _ in runner.calls) == (1 if runner.is_available else 0)


def test_invalid_file_does_not_start_live_queries(monkeypatch, capsys, tmp_path):
    def forbidden():
        raise AssertionError("No live runner for invalid input")
    monkeypatch.setattr("gpu_guard.cli.KubectlRunner", forbidden)
    assert main(["workload-check", str(tmp_path / "absent.yaml")]) == 2
    assert json.loads(capsys.readouterr().out)[0]["check"] == "manifest_input"


@pytest.mark.parametrize("filename", ["workload_two_gpu.yaml", "workload_multi_container.yaml"])
@pytest.mark.parametrize("capacities,code", [([1, 1], 1), ([2, 0], 0)])
def test_two_gpu_examples_need_one_node(monkeypatch, capsys, filename, capacities, code):
    nodes = [{"metadata": {"name": f"node-{index}"}, "status": {
        "conditions": [{"type": "Ready", "status": "True"}],
        "allocatable": {"nvidia.com/gpu": str(capacity)},
    }} for index, capacity in enumerate(capacities)]
    runner = FakeRunner(nodes=nodes)
    monkeypatch.setattr("gpu_guard.cli.KubectlRunner", lambda: runner)
    assert main(["workload-check", f"examples/good/{filename}"]) == code
    feasibility = json.loads(capsys.readouterr().out)[-1]
    assert feasibility["evidence"]["workload_gpu_request"] == 2
    assert feasibility["status"] == ("PASS" if code == 0 else "FAIL")
    assert sum(query == "nodes" for query, _ in runner.calls) == 1


@pytest.mark.parametrize("filename,labels,taints,code", [
    ("good/node_selector_match.yaml", {"gpu": "a100", "region": "kr"}, [], 0),
    ("good/toleration_match.yaml", {}, [{"key": "nvidia.com/gpu", "value": "true", "effect": "NoSchedule"}], 0),
    ("bad/node_selector_mismatch.yaml", {"gpu": "a100"}, [], 1),
    ("bad/missing_toleration.yaml", {}, [{"key": "nvidia.com/gpu", "value": "true", "effect": "NoSchedule"}], 1),
    ("bad/fixed_node_missing.yaml", {}, [], 1),
])
def test_scheduling_examples(monkeypatch, capsys, filename, labels, taints, code):
    nodes = [{"metadata": {"name": "node-a", "labels": labels}, "spec": {"taints": taints},
              "status": {"conditions": [{"type": "Ready", "status": "True"}],
                         "allocatable": {"nvidia.com/gpu": "2"}}}]
    runner = FakeRunner(nodes=nodes)
    monkeypatch.setattr("gpu_guard.cli.KubectlRunner", lambda: runner)
    assert main(["workload-check", f"examples/{filename}"]) == code
    report = json.loads(capsys.readouterr().out)
    assert report[-2]["check"] == "node_eligibility"
    assert report[-2]["status"] == report[-1]["status"] == ("PASS" if code == 0 else "FAIL")
    assert sum(query == "nodes" for query, _ in runner.calls) == 1


@pytest.mark.parametrize("filename,labels,capacities,code", [
    ("good/affinity_in_match.yaml", [{"gpu": "a100"}], [4], 0),
    ("good/affinity_or_terms.yaml", [{"gpu": "h100"}], [4], 0),
    ("bad/affinity_in_mismatch.yaml", [{"gpu": "a100"}], [4], 1),
    ("bad/affinity_numeric_invalid.yaml", [{"gpu-memory-gb": "80"}], [4], 1),
    ("bad/affinity_no_eligible_gpu_node.yaml", [{"gpu": "rtx4090"}, {"gpu": "a100"}], [8, 1], 1),
])
def test_affinity_examples_and_node_query_reuse(monkeypatch, capsys, filename, labels, capacities, code):
    nodes = [{"metadata": {"name": f"node-{i}", "labels": label},
              "status": {"conditions": [{"type": "Ready", "status": "True"}],
                         "allocatable": {"nvidia.com/gpu": str(capacities[i])}}}
             for i, label in enumerate(labels)]
    runner = FakeRunner(nodes=nodes)
    monkeypatch.setattr("gpu_guard.cli.KubectlRunner", lambda: runner)
    assert main(["workload-check", f"examples/{filename}"]) == code
    report = json.loads(capsys.readouterr().out)
    assert report[-2]["status"] == report[-1]["status"] == ("PASS" if code == 0 else "FAIL")
    assert sum(query == "nodes" for query, _ in runner.calls) == 1


def test_static_gpu_error_preserved_even_with_live_capacity(monkeypatch, capsys, tmp_path):
    manifest = yaml.safe_load((ROOT / "examples/good/probes_complete.yaml").read_text(encoding="utf-8"))
    manifest["spec"]["template"]["spec"]["containers"][0]["resources"]["limits"]["nvidia.com/gpu"] = "bad"
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    monkeypatch.setattr("gpu_guard.cli.KubectlRunner", FakeRunner)
    assert main(["workload-check", str(path)]) == 1
    results = json.loads(capsys.readouterr().out)
    assert results[0]["status"] == results[-1]["status"] == "FAIL"
    assert results[-1]["evidence"]["error"] == results[0]["evidence"]


def test_help_and_missing_argument(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["workload-check", "--help"])
    assert exc.value.code == 0
    with pytest.raises(SystemExit) as exc:
        main(["workload-check"])
    assert exc.value.code == 2
