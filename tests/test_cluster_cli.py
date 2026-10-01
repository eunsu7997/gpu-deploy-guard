"""CLI dispatch/exit tests use a fake runner; no live cluster dependency."""

import json

import pytest

from gpu_guard.cli import main
from gpu_guard.kubectl import CommandResult


class FakeRunner:
    def __init__(self, available=True, gpu="0", api_fail=False):
        self.is_available = available
        self.gpu = gpu
        self.api_fail = api_fail

    def available(self):
        return self.is_available

    def run(self, query, *, context=None):
        if query == "context":
            return CommandResult(0, "fake-context")
        if query == "nodes":
            if self.api_fail:
                return CommandResult(1, stderr="connection refused")
            return CommandResult(0, json.dumps({"items": [{"status": {
                "conditions": [{"type": "Ready", "status": "True"}],
                "allocatable": {"nvidia.com/gpu": self.gpu},
            }}]}))
        assert query == "pods"
        return CommandResult(0, '{"items": []}')


@pytest.mark.parametrize("runner,code", [
    (FakeRunner(), 0), (FakeRunner(gpu="1"), 0),
    (FakeRunner(available=False), 1), (FakeRunner(api_fail=True), 1),
])
def test_cluster_cli_dispatch_and_exit(monkeypatch, capsys, runner, code):
    monkeypatch.setattr("gpu_guard.cli.KubectlRunner", lambda: runner)
    assert main(["cluster-check"]) == code
    report = json.loads(capsys.readouterr().out)
    assert len(report) == 6
    assert all(set(r) == {"check", "status", "evidence", "recommendation"} for r in report)


def test_static_check_does_not_construct_live_runner(monkeypatch, capsys):
    def forbidden():
        raise AssertionError("Static validation must not create kubectl runner")
    monkeypatch.setattr("gpu_guard.cli.KubectlRunner", forbidden)
    assert main(["check", "examples/good/probes_complete.yaml"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 6


def test_cluster_help(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["cluster-check", "--help"])
    assert exc.value.code == 0
    assert "cluster-check" in capsys.readouterr().out
