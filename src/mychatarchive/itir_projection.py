"""Bounded ITIR predicate projection helpers."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, is_dataclass
import re
from typing import Any

_PROJECTION_VERSION = "itir_predicate_projection_v1"
_MAX_PREDICATES = 64
_MAX_ROLES_PER_PREDICATE = 12
_MAX_PROVENANCE_REFS = 16
_MAX_SPAN_REFS = 16
_MAX_INDEX_KEYS = 128
_MAX_INDEX_REFS_PER_KEY = 32

_SPAN_REF_RE = re.compile(r"^[a-z_]+:(\d+)-(\d+)$")


def build_predicate_projection(text: str, reducer_module) -> dict[str, Any]:
    """Build a deterministic, serializable, bounded predicate projection."""
    predicate_atoms = _collect_predicate_atoms(text, reducer_module)
    ref_map = _build_ref_map(predicate_atoms, reducer_module)
    projected_items = sorted(ref_map.items(), key=lambda item: str(item[0]))[:_MAX_PREDICATES]

    predicates = [_serialize_predicate(ref, atom) for ref, atom in projected_items]
    index = _build_index([atom for _, atom in projected_items], reducer_module)

    return {
        "version": _PROJECTION_VERSION,
        "limits": {
            "max_predicates": _MAX_PREDICATES,
            "max_roles_per_predicate": _MAX_ROLES_PER_PREDICATE,
            "max_provenance_refs": _MAX_PROVENANCE_REFS,
            "max_span_refs": _MAX_SPAN_REFS,
            "max_index_keys": _MAX_INDEX_KEYS,
            "max_index_refs_per_key": _MAX_INDEX_REFS_PER_KEY,
        },
        "counts": {
            "predicates_total": len(ref_map),
            "predicates_projected": len(predicates),
            "predicates_truncated": len(ref_map) > len(predicates),
        },
        "predicates": predicates,
        "index": index,
    }


def _collect_predicate_atoms(text: str, reducer_module) -> list[Any]:
    text = (text or "").strip()
    if not text:
        return []
    if hasattr(reducer_module, "collect_canonical_predicate_atoms"):
        collected = reducer_module.collect_canonical_predicate_atoms(text)
        return list(collected or [])
    if hasattr(reducer_module, "collect_canonical_predicate_pnfs"):
        collected = reducer_module.collect_canonical_predicate_pnfs(text)
        return list(collected or [])
    raise RuntimeError("missing predicate collection surface")


def _build_ref_map(predicate_atoms: Iterable[Any], reducer_module) -> dict[str, Any]:
    if hasattr(reducer_module, "build_predicate_ref_map"):
        raw = reducer_module.build_predicate_ref_map(predicate_atoms)
        if isinstance(raw, Mapping):
            return {str(key): value for key, value in raw.items()}
    fallback: dict[str, Any] = {}
    for index, atom in enumerate(predicate_atoms):
        atom_payload = _to_plain_dict(atom)
        atom_id = atom_payload.get("atom_id") or atom_payload.get("id")
        fallback[str(atom_id or f"pnf:{index}")] = atom
    return fallback


def _build_index(predicate_atoms: list[Any], reducer_module) -> dict[str, Any]:
    if hasattr(reducer_module, "build_predicate_index"):
        index_obj = reducer_module.build_predicate_index(predicate_atoms)
        return _serialize_index(index_obj)
    return _serialize_index(_fallback_index(predicate_atoms))


def _fallback_index(predicate_atoms: list[Any]) -> dict[str, Any]:
    by_structural_sig: dict[str, list[str]] = {}
    by_role_slot: dict[str, list[str]] = {}
    by_argval: dict[str, list[str]] = {}
    by_role_arg: dict[str, list[str]] = {}

    for index, atom in enumerate(predicate_atoms):
        payload = _to_plain_dict(atom)
        ref = str(payload.get("atom_id") or payload.get("id") or f"pnf:{index}")
        sig = str(payload.get("structural_signature") or payload.get("predicate") or "")
        if sig:
            by_structural_sig.setdefault(sig, []).append(ref)
        roles = payload.get("roles")
        if not isinstance(roles, Mapping):
            continue
        for role_name, role_value in roles.items():
            role_key = str(role_name)
            by_role_slot.setdefault(role_key, []).append(ref)
            role_payload = _to_plain_dict(role_value)
            value = str(role_payload.get("value", "")).strip()
            if not value:
                continue
            by_argval.setdefault(value, []).append(ref)
            by_role_arg.setdefault(f"{role_key}|{value}", []).append(ref)

    return {
        "by_structural_sig": by_structural_sig,
        "by_role_slot": by_role_slot,
        "by_argval": by_argval,
        "by_role_arg": by_role_arg,
    }


def _serialize_predicate(ref: str, atom: Any) -> dict[str, Any]:
    payload = _to_plain_dict(atom)
    roles = _serialize_roles(payload.get("roles"))
    provenance_refs = _normalize_refs(payload.get("provenance"))
    span_refs = _extract_span_refs(
        [*provenance_refs, *(ref_value for role in roles for ref_value in role["provenance_refs"])]
    )

    qualifiers = _to_plain_dict(payload.get("qualifiers"))
    wrapper = _to_plain_dict(payload.get("wrapper"))

    predicate_payload: dict[str, Any] = {
        "ref": ref,
        "atom_id": _optional_str(payload.get("atom_id") or payload.get("id")),
        "predicate": str(payload.get("predicate", "")),
        "structural_signature": str(payload.get("structural_signature") or payload.get("predicate") or ""),
        "roles": roles,
        "polarity": str(qualifiers.get("polarity") or "positive"),
        "modality": _optional_str(qualifiers.get("modality")),
        "provenance_refs": provenance_refs,
        "source_spans": span_refs,
        "wrapper": {
            "status": _optional_str(wrapper.get("status")),
            "evidence_only": bool(wrapper.get("evidence_only", True)),
        },
    }
    domain = _optional_str(payload.get("domain"))
    if domain is not None:
        predicate_payload["domain"] = domain
    return predicate_payload


def _serialize_roles(raw_roles: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_roles, Mapping):
        return []
    roles: list[dict[str, Any]] = []
    for role_name in sorted(str(key) for key in raw_roles):
        role_payload = _to_plain_dict(raw_roles.get(role_name))
        value = _optional_str(role_payload.get("value"))
        if value is None:
            continue
        role_entry: dict[str, Any] = {
            "name": role_name,
            "value": value,
            "status": str(role_payload.get("status") or "bound"),
            "entity_type": _optional_str(role_payload.get("entity_type")),
            "cardinality": str(role_payload.get("cardinality") or "single"),
            "members": _normalize_refs(role_payload.get("members")),
            "provenance_refs": _normalize_refs(role_payload.get("provenance")),
        }
        roles.append(role_entry)
        if len(roles) >= _MAX_ROLES_PER_PREDICATE:
            break
    return roles


def _serialize_index(index_obj: Any) -> dict[str, Any]:
    payload = _to_plain_dict(index_obj)
    return {
        "by_structural_signature": _normalize_index_mapping(payload.get("by_structural_sig")),
        "by_role": _normalize_index_mapping(payload.get("by_role_slot")),
        "by_argument": _normalize_index_mapping(payload.get("by_argval")),
        "by_role_argument": _normalize_index_mapping(payload.get("by_role_arg"), normalize_tuple_keys=True),
    }


def _normalize_index_mapping(raw: Any, *, normalize_tuple_keys: bool = False) -> dict[str, list[str]]:
    if not isinstance(raw, Mapping):
        return {}
    normalized: dict[str, list[str]] = {}
    for key in sorted(raw, key=lambda value: str(value)):
        refs = _normalize_refs(raw.get(key), max_items=_MAX_INDEX_REFS_PER_KEY)
        if not refs:
            continue
        if normalize_tuple_keys and isinstance(key, tuple) and len(key) == 2:
            normalized_key = f"{key[0]}|{key[1]}"
        else:
            normalized_key = str(key)
        normalized[normalized_key] = refs
        if len(normalized) >= _MAX_INDEX_KEYS:
            break
    return normalized


def _normalize_refs(raw: Any, *, max_items: int = _MAX_PROVENANCE_REFS) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]
    if not isinstance(raw, Iterable):
        return [str(raw)]
    refs: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if item is None:
            continue
        value = str(item).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        refs.append(value)
        if len(refs) >= max_items:
            break
    return refs


def _extract_span_refs(refs: list[str]) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for ref in refs:
        match = _SPAN_REF_RE.match(ref)
        if not match:
            continue
        start = int(match.group(1))
        end = int(match.group(2))
        key = (start, end)
        if key in seen:
            continue
        seen.add(key)
        spans.append({"start": start, "end": end, "ref": ref})
        if len(spans) >= _MAX_SPAN_REFS:
            break
    return spans


def _to_plain_dict(value: Any) -> Any:
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _to_plain_dict(value.to_dict())
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Mapping):
        return {key: _to_plain_dict(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_plain_dict(item) for item in value]
    return value


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
