"""Required nodeAffinity parsing and label-only evaluation; no I/O."""

from dataclasses import dataclass
import re
from typing import Any


REQUIRED = "requiredDuringSchedulingIgnoredDuringExecution"
OPERATORS = ("In", "NotIn", "Exists", "DoesNotExist", "Gt", "Lt")


def _integer(value: Any) -> int:
    """Match Kubernetes ParseInt(base 10, 64), not Python's permissive int syntax."""
    if not isinstance(value, str) or re.fullmatch(r"[+-]?[0-9]+", value) is None:
        raise ValueError(f"{value!r} is not an integer string")
    number = int(value)
    if not -(2**63) <= number < 2**63:
        raise ValueError(f"{value!r} is outside signed 64-bit integer range")
    return number


@dataclass(frozen=True)
class Expression:
    key: str
    operator: str
    values: tuple[str, ...]
    threshold: int | None = None


@dataclass(frozen=True)
class RequiredNodeAffinity:
    terms: tuple[tuple[Expression, ...], ...]


def parse_required_node_affinity(pod: dict[str, Any]) -> RequiredNodeAffinity | None:
    """Validate all terms, including unmatched OR branches, before evaluation."""
    if "affinity" not in pod:
        return None
    affinity = pod["affinity"]
    if not isinstance(affinity, dict):
        raise ValueError(f"affinity = {affinity!r}; expected mapping")
    if "nodeAffinity" not in affinity:
        return None
    node_affinity = affinity["nodeAffinity"]
    if not isinstance(node_affinity, dict):
        raise ValueError(f"affinity.nodeAffinity = {node_affinity!r}; expected mapping")
    if REQUIRED not in node_affinity:
        return None
    required = node_affinity[REQUIRED]
    location = f"affinity.nodeAffinity.{REQUIRED}"
    if not isinstance(required, dict):
        raise ValueError(f"{location} = {required!r}; expected mapping")
    terms = required.get("nodeSelectorTerms")
    location += ".nodeSelectorTerms"
    if not isinstance(terms, list) or not terms:
        raise ValueError(f"{location} = {terms!r}; expected non-empty list")
    parsed = []
    for term_index, term in enumerate(terms):
        term_path = f"{location}[{term_index}]"
        if not isinstance(term, dict):
            raise ValueError(f"{term_path} = {term!r}; expected mapping")
        if "matchFields" in term:
            raise ValueError(f"{term_path}.matchFields = {term['matchFields']!r}; matchFields is unsupported")
        expressions = term.get("matchExpressions", [])
        if not isinstance(expressions, list):
            raise ValueError(f"{term_path}.matchExpressions = {expressions!r}; expected list")
        compiled = []
        for index, expression in enumerate(expressions):
            path = f"{term_path}.matchExpressions[{index}]"
            if not isinstance(expression, dict):
                raise ValueError(f"{path} = {expression!r}; expected mapping")
            key, operator, values = expression.get("key"), expression.get("operator"), expression.get("values", [])
            if not isinstance(key, str) or not key.strip():
                raise ValueError(f"{path}.key = {key!r}; expected non-empty string")
            if operator not in OPERATORS:
                raise ValueError(f"{path}.operator = {operator!r}; unsupported or missing operator")
            if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
                raise ValueError(f"{path}.values = {values!r}; expected string list")
            if operator in ("In", "NotIn") and not values:
                raise ValueError(f"{path}.values = {values!r}; {operator} requires non-empty values")
            if operator in ("Exists", "DoesNotExist") and values:
                raise ValueError(f"{path}.values = {values!r}; {operator} requires no values or an empty list")
            threshold = None
            if operator in ("Gt", "Lt"):
                if len(values) != 1:
                    raise ValueError(f"{path}.values = {values!r}; {operator} requires exactly one value")
                try:
                    threshold = _integer(values[0])
                except ValueError as exc:
                    raise ValueError(f"{path}.values = {values!r}; {exc}") from exc
            compiled.append(Expression(key, operator, tuple(values), threshold))
        parsed.append(tuple(compiled))
    return RequiredNodeAffinity(tuple(parsed))


def _evaluate_expression(expression: Expression, labels: dict[str, str]) -> tuple[bool, str]:
    key, operator, values = expression.key, expression.operator, expression.values
    present = key in labels
    observed = repr(labels[key]) if present else "<missing>"
    description = f"nodeAffinity mismatch: {key} {operator} {list(values)!r}; observed={observed}"
    if operator == "In":
        matched = present and labels[key] in values
    elif operator == "NotIn":
        matched = not present or labels[key] not in values
    elif operator == "Exists":
        matched = present
    elif operator == "DoesNotExist":
        matched = not present
    else:
        if not present:
            return False, description
        try:
            number = _integer(labels[key])
        except ValueError as exc:
            return False, f"{description}; {exc}"
        matched = number > expression.threshold if operator == "Gt" else number < expression.threshold
    return matched, "" if matched else description


def evaluate_required_node_affinity(rule: RequiredNodeAffinity, labels: dict[str, str]) -> dict[str, Any]:
    """OR between terms, AND within a term; an empty term matches no nodes."""
    terms, matched_terms = [], []
    for index, expressions in enumerate(rule.terms):
        reasons = []
        if not expressions:
            reasons.append("empty nodeSelectorTerm matches no nodes")
        for expression in expressions:
            matched, reason = _evaluate_expression(expression, labels)
            if not matched:
                reasons.append(reason)
        matched = not reasons
        if matched:
            matched_terms.append(index)
        terms.append(dict(term=index, matched=matched, reasons=reasons))
    return dict(matched=bool(matched_terms), terms=len(rule.terms),
                matched_term=matched_terms[0] if matched_terms else None, evaluated_terms=terms)
