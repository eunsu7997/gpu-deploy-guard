"""Normalized scheduling node information from the existing read-only snapshot."""

from dataclasses import asdict, dataclass
from typing import Any

from gpu_guard.cluster_checks import node_gpu_capacity, node_is_ready


EFFECTS = ("NoSchedule", "NoExecute", "PreferNoSchedule")


@dataclass(frozen=True)
class NodeSnapshot:
    name: str
    ready: bool
    labels: dict[str, str]
    unschedulable: bool
    taints: list[dict[str, str]]
    allocatable_gpu: int

    def evidence(self) -> dict[str, Any]:
        return asdict(self)


def normalize_nodes(nodes: list[dict[str, Any]]) -> list[NodeSnapshot]:
    results = []
    names = set()
    for index, node in enumerate(nodes):
        metadata = node.get("metadata")
        name = metadata.get("name") if isinstance(metadata, dict) else None
        if not isinstance(name, str) or not name.strip() or name in names:
            raise ValueError(f"nodes[{index}].metadata.name = {name!r}; expected unique non-empty node name")
        names.add(name)
        labels = metadata.get("labels", {})
        if not isinstance(labels, dict) or any(not isinstance(k, str) or not k or not isinstance(v, str) for k, v in labels.items()):
            raise ValueError(f"node {name!r}.labels = {labels!r}; expected string mapping")
        spec = node.get("spec", {})
        if not isinstance(spec, dict):
            raise ValueError(f"node {name!r}.spec = {spec!r}; expected object")
        unschedulable = spec.get("unschedulable", False)
        if type(unschedulable) is not bool:
            raise ValueError(f"node {name!r}.unschedulable = {unschedulable!r}; expected boolean")
        taints = spec.get("taints", [])
        if not isinstance(taints, list):
            raise ValueError(f"node {name!r}.taints = {taints!r}; expected list")
        for taint in taints:
            if (not isinstance(taint, dict) or not isinstance(taint.get("key"), str) or not taint["key"]
                    or not isinstance(taint.get("value", ""), str) or taint.get("effect") not in EFFECTS):
                raise ValueError(f"node {name!r}.taint = {taint!r}; expected key/value/effect taint")
        capacity, _ = node_gpu_capacity(node, index)
        results.append(NodeSnapshot(name, node_is_ready(node, index), labels.copy(), unschedulable,
                                    [t.copy() for t in taints], capacity))
    return results
