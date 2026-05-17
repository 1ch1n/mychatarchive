"""Bounded residual-style comparison over serialized predicate projections."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def summarize_projection_residual(
    query_projection: Mapping[str, Any] | None,
    candidate_projection: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(query_projection, Mapping) or not isinstance(candidate_projection, Mapping):
        return {
            "available": False,
            "shared_predicates": [],
            "contradictions": [],
            "unresolved_predicates": [],
            "shared_role_arguments": [],
            "score": 0.0,
        }

    query_preds = _index_predicates(query_projection)
    candidate_preds = _index_predicates(candidate_projection)

    query_sigs = set(query_preds)
    candidate_sigs = set(candidate_preds)
    shared = sorted(query_sigs & candidate_sigs)
    unresolved = sorted(query_sigs - candidate_sigs)

    contradictions: list[str] = []
    shared_role_arguments: list[str] = []
    for sig in shared:
        query_item = query_preds[sig]
        candidate_item = candidate_preds[sig]
        if query_item["polarity"] and candidate_item["polarity"] and query_item["polarity"] != candidate_item["polarity"]:
            contradictions.append(sig)
        shared_role_arguments.extend(sorted(query_item["role_args"] & candidate_item["role_args"]))

    shared_role_arguments = sorted(set(shared_role_arguments))
    overlap_score = (len(shared) / len(query_sigs)) if query_sigs else 0.0
    contradiction_penalty = min(len(contradictions) * 0.25, 1.0)
    role_bonus = min(len(shared_role_arguments) * 0.05, 0.25)
    score = max(0.0, min(1.0, overlap_score + role_bonus - contradiction_penalty))

    return {
        "available": True,
        "shared_predicates": shared,
        "contradictions": contradictions,
        "unresolved_predicates": unresolved,
        "shared_role_arguments": shared_role_arguments,
        "score": round(score, 4),
    }


def extract_projection_terms(projection: Mapping[str, Any] | None) -> dict[str, list[str]]:
    """Return normalized lookup terms for archive-level predicate retrieval."""
    if not isinstance(projection, Mapping):
        return {"signatures": [], "role_arguments": []}

    indexed = _index_predicates(projection)
    signatures = sorted(indexed)
    role_arguments: set[str] = set()
    for item in indexed.values():
        role_arguments.update(item["role_args"])
    return {
        "signatures": signatures,
        "role_arguments": sorted(role_arguments),
    }


def _index_predicates(projection: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    predicates = projection.get("predicates")
    if not isinstance(predicates, list):
        return {}
    indexed: dict[str, dict[str, Any]] = {}
    for pred in predicates:
        if not isinstance(pred, Mapping):
            continue
        sig = str(pred.get("structural_signature") or pred.get("predicate") or "").strip()
        if not sig:
            continue
        indexed[sig] = {
            "polarity": str(pred.get("polarity") or "").strip() or None,
            "role_args": _role_args(pred.get("roles")),
        }
    return indexed


def _role_args(roles: Any) -> set[str]:
    if not isinstance(roles, list):
        return set()
    result: set[str] = set()
    for role in roles:
        if not isinstance(role, Mapping):
            continue
        name = str(role.get("name") or "").strip()
        value = str(role.get("value") or "").strip()
        if name and value:
            result.add(f"{name}|{value}")
    return result
