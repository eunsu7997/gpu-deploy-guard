"""Command-line interface for independent Deployment resource and probe checks."""

import argparse
import json
from pathlib import Path
from typing import Sequence

import yaml

from gpu_guard.validators import validate_gpu_limits
from gpu_guard.resource_validators import validate_cpu_memory_resources
from gpu_guard.probe_validators import validate_probes
from gpu_guard.cluster_checks import cluster_check, collect_cluster_snapshot
from gpu_guard.feasibility import evaluate_gpu_feasibility
from gpu_guard.eligibility import evaluate_node_eligibility, constrain_gpu_feasibility
from gpu_guard.kubectl import KubectlRunner


def main(argv: Sequence[str] | None = None) -> int:
    """Exit 0 for PASS/WARN, 1 for rule FAIL, and 2 for input errors."""
    parser = argparse.ArgumentParser(prog="gpu-guard", description="GPUDeploy Guard")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="Check Deployment GPU, CPU, memory and health probes")
    check.add_argument("manifest", type=Path, help="Path to a Kubernetes YAML manifest")
    commands.add_parser("cluster-check", help="Read-only live Kubernetes cluster preflight")
    workload = commands.add_parser("workload-check", help="Compare one Pod's GPU request with Ready node allocatable capacity")
    workload.add_argument("manifest", type=Path, help="Path to a Deployment YAML manifest")
    args = parser.parse_args(argv)

    if args.command == "cluster-check":
        results = cluster_check(KubectlRunner())
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 1 if any(result["status"] == "FAIL" for result in results) else 0

    try:
        with args.manifest.open(encoding="utf-8") as stream:
            manifest = yaml.safe_load(stream)
        results = validate_gpu_limits(manifest)
        results.extend(validate_cpu_memory_resources(manifest))
        results.extend(validate_probes(manifest))
    except (OSError, UnicodeError, yaml.YAMLError, ValueError) as exc:
        print(json.dumps([{
            "check": "manifest_input", "status": "FAIL",
            "evidence": f"{args.manifest}: {exc}",
            "recommendation": "Provide a readable single Deployment YAML with a non-empty containers list.",
        }], ensure_ascii=False, indent=2))
        return 2
    if args.command == "workload-check":
        snapshot = collect_cluster_snapshot(KubectlRunner())
        feasibility = evaluate_gpu_feasibility(manifest, results, snapshot)
        eligibility = evaluate_node_eligibility(manifest, snapshot, feasibility)
        feasibility = constrain_gpu_feasibility(feasibility, eligibility)
        results.extend(snapshot.results)
        results.append(eligibility)
        results.append(feasibility)
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 1 if any(result["status"] == "FAIL" for result in results) else 0
