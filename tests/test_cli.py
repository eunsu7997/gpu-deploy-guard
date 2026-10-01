"""CLI execution and regression tests."""

import os
import json
from pathlib import Path
import subprocess
import sys

import pytest

from gpu_guard.cli import main


ROOT = Path(__file__).resolve().parents[1]


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    return subprocess.run(
        [sys.executable, "-m", "gpu_guard", *args],
        cwd=ROOT, env=env, capture_output=True, text=True, check=False,
    )


@pytest.mark.parametrize("path,status,code", [
    ("examples/good/gpu_limit.yaml", "PASS", 0),
    ("examples/bad/missing_gpu.yaml", "FAIL", 1),
    ("examples/bad/zero_gpu.yaml", "FAIL", 1),
])
def test_module_check(path, status, code):
    result = run_cli("check", path)
    assert result.returncode == code
    report = json.loads(result.stdout)
    assert report[0]["status"] == status
    assert set(report[0]) == {"check", "status", "evidence", "recommendation"}
    assert report[0]["evidence"]
    assert report[0]["recommendation"]
    assert result.stderr == ""


@pytest.mark.parametrize("filename,statuses,code", [
    ("good/resources_complete.yaml", ["PASS", "PASS", "PASS"], 0),
    ("bad/missing_cpu_limit.yaml", ["PASS", "WARN", "PASS"], 0),
    ("bad/missing_memory_request.yaml", ["PASS", "PASS", "WARN"], 0),
    ("bad/missing_resources.yaml", ["FAIL", "WARN", "WARN"], 1),
    ("bad/invalid_resource_value.yaml", ["PASS", "FAIL", "FAIL"], 1),
])
def test_combined_resource_cli(filename, statuses, code):
    result = run_cli("check", f"examples/{filename}")
    assert result.returncode == code
    report = json.loads(result.stdout)
    assert [r["check"] for r in report[:3]] == ["gpu_resource_limit", "cpu_resources", "memory_resources"]
    assert [r["status"] for r in report[:3]] == statuses
    assert [r["check"] for r in report[3:]] == ["startup_probe", "readiness_probe", "liveness_probe"]
    assert [r["status"] for r in report[3:]] == ["WARN", "WARN", "WARN"]
    assert all(set(r) == {"check", "status", "evidence", "recommendation"} for r in report)
    assert result.stderr == ""


@pytest.mark.parametrize("filename,statuses,code", [
    ("good/probes_complete.yaml", ["PASS", "PASS", "PASS"], 0),
    ("bad/missing_startup_probe.yaml", ["WARN", "PASS", "PASS"], 0),
    ("bad/missing_readiness_probe.yaml", ["PASS", "WARN", "PASS"], 0),
    ("bad/missing_liveness_probe.yaml", ["PASS", "PASS", "WARN"], 0),
    ("bad/invalid_probe_handler.yaml", ["FAIL", "FAIL", "FAIL"], 1),
])
def test_combined_probe_cli(filename, statuses, code):
    result = run_cli("check", f"examples/{filename}")
    assert result.returncode == code
    report = json.loads(result.stdout)
    assert [r["check"] for r in report] == ["gpu_resource_limit", "cpu_resources", "memory_resources",
                                           "startup_probe", "readiness_probe", "liveness_probe"]
    assert [r["status"] for r in report] == ["PASS", "PASS", "PASS"] + statuses
    assert all(set(r) == {"check", "status", "evidence", "recommendation"} for r in report)
    assert result.stderr == ""


def test_help_is_successful():
    result = run_cli("--help")
    assert result.returncode == 0
    assert "check" in result.stdout


@pytest.mark.parametrize("args", [[], ["check"], ["unknown"]])
def test_invalid_arguments_are_rejected(args):
    result = run_cli(*args)
    assert result.returncode == 2
    assert "error:" in result.stderr
    assert "NOT IMPLEMENTED" not in result.stdout


def test_console_entry_function(capsys):
    assert main(["check", "examples/good/gpu_limit.yaml"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["status"] == "PASS"


@pytest.mark.parametrize("content", ["kind: [", "", "kind: Pod", "kind: Deployment\nspec: {}"])
def test_invalid_manifest(tmp_path, capsys, content):
    path = tmp_path / "invalid.yaml"
    path.write_text(content, encoding="utf-8")
    assert main(["check", str(path)]) == 2
    result = json.loads(capsys.readouterr().out)[0]
    assert result["check"] == "manifest_input"
    assert result["status"] == "FAIL"
    assert str(path) in result["evidence"]
    assert result["recommendation"]


def test_missing_file(tmp_path, capsys):
    path = tmp_path / "absent.yaml"
    assert main(["check", str(path)]) == 2
    assert str(path) in json.loads(capsys.readouterr().out)[0]["evidence"]


def test_mixed_containers_exit_failure(tmp_path, capsys):
    path = tmp_path / "mixed.yaml"
    path.write_text("""kind: Deployment
spec:
  template:
    spec:
      containers:
        - name: first
          resources:
            limits:
              nvidia.com/gpu: 1
        - name: second
""", encoding="utf-8")
    assert main(["check", str(path)]) == 1
    report = json.loads(capsys.readouterr().out)
    assert [r["status"] for r in report if r["check"] == "gpu_resource_limit"] == ["PASS", "FAIL"]
    assert [r["status"] for r in report if r["check"] == "cpu_resources"] == ["WARN", "WARN"]
    assert [r["status"] for r in report if r["check"] == "memory_resources"] == ["WARN", "WARN"]
