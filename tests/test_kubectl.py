"""Mock the process boundary: no real kubectl execution in tests."""

import subprocess
from unittest.mock import Mock

import pytest

from gpu_guard.kubectl import KubectlRunner


def runner(monkeypatch):
    monkeypatch.setattr("gpu_guard.kubectl.shutil.which", lambda name: "kubectl.exe")
    return KubectlRunner()


def test_missing_executable(monkeypatch):
    monkeypatch.setattr("gpu_guard.kubectl.shutil.which", lambda name: None)
    process = Mock()
    monkeypatch.setattr("gpu_guard.kubectl.subprocess.run", process)
    client = KubectlRunner()
    assert not client.available()
    assert client.run("context").returncode == 1
    process.assert_not_called()


@pytest.mark.parametrize("query,expected", [
    ("context", ["config", "current-context"]),
    ("nodes", ["get", "nodes", "-o", "json"]),
    ("pods", ["get", "pods", "-n", "kube-system", "-o", "json"]),
])
def test_only_exact_read_commands_no_shell(monkeypatch, query, expected):
    client = runner(monkeypatch)
    process = Mock(return_value=subprocess.CompletedProcess([], 0, "output", "diagnostic"))
    monkeypatch.setattr("gpu_guard.kubectl.subprocess.run", process)
    result = client.run(query, context="dev")
    command = ["kubectl.exe", *expected]
    if query != "context":
        command += ["--context=dev", "--request-timeout=10s"]
    assert process.call_args.args[0] == command
    assert process.call_args.kwargs["shell"] is False
    assert process.call_args.kwargs["timeout"] == 10
    assert result.stdout == "output" and result.stderr == "diagnostic"


@pytest.mark.parametrize("query", ["apply", "delete", "patch", "edit", "exec", "scale", "get secrets"])
def test_unapproved_queries_never_run(monkeypatch, query):
    client = runner(monkeypatch)
    process = Mock()
    monkeypatch.setattr("gpu_guard.kubectl.subprocess.run", process)
    with pytest.raises(ValueError):
        client.run(query, context="dev")
    process.assert_not_called()


def test_api_query_needs_context(monkeypatch):
    client = runner(monkeypatch)
    process = Mock()
    monkeypatch.setattr("gpu_guard.kubectl.subprocess.run", process)
    with pytest.raises(ValueError):
        client.run("nodes")
    process.assert_not_called()


def test_timeout_is_result_not_traceback(monkeypatch):
    client = runner(monkeypatch)
    process = Mock(side_effect=subprocess.TimeoutExpired("kubectl", 10))
    monkeypatch.setattr("gpu_guard.kubectl.subprocess.run", process)
    result = client.run("nodes", context="dev")
    assert result.timed_out and result.returncode == 1
    assert "10s" in result.stderr


def test_oserror_is_result(monkeypatch):
    client = runner(monkeypatch)
    monkeypatch.setattr("gpu_guard.kubectl.subprocess.run", Mock(side_effect=OSError("executable unavailable")))
    assert "executable unavailable" in client.run("context").stderr
