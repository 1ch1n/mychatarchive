"""Tests for required ITIR enrichment."""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

from mychatarchive import itir


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_enrich_text_uses_local_shared_reducer(monkeypatch, tmp_path):
    fake_root = tmp_path / "SensibLaw"
    _write(
        fake_root / "src" / "sensiblaw" / "__init__.py",
        "",
    )
    _write(
        fake_root / "src" / "sensiblaw" / "interfaces" / "__init__.py",
        "",
    )
    _write(
        fake_root / "src" / "sensiblaw" / "interfaces" / "shared_reducer.py",
        """
from dataclasses import dataclass

def get_canonical_tokenizer_profile_receipt():
    return {"profile_id": "fake", "canonical_mode": "deterministic_legal"}

def tokenize_canonical_with_spans(text):
    return [("hello", 0, 5), ("world", 6, 11)]

def collect_canonical_lexeme_refs(text):
    return [{"occurrence_id": "abc", "kind": "word", "span_start": 0, "span_end": 5}]

@dataclass(frozen=True)
class _Structure:
    text: str
    norm_text: str
    kind: str
    start_char: int
    end_char: int
    flags: int = 0

def collect_canonical_structure_occurrences(text):
    return [_Structure("hello", "hello", "word_ref", 0, 5, 0)]

def collect_canonical_relational_bundle(text):
    return {"version": "relational_bundle_v1", "canonical_text": text, "atoms": [], "relations": []}

def collect_canonical_predicate_atoms(text):
    return [
        {
            "atom_id": "pred:1",
            "predicate": "pay",
            "structural_signature": "pay",
            "roles": {
                "subject": {
                    "value": "tenant",
                    "status": "bound",
                    "entity_type": "person",
                    "provenance": ["head:0-5"],
                },
                "object": {
                    "value": "rent",
                    "status": "bound",
                    "provenance": ["arg:6-11"],
                },
            },
            "qualifiers": {"polarity": "negative", "modality": "must"},
            "wrapper": {"status": "structural_projection", "evidence_only": True},
            "provenance": ["pred:1", "head:0-5", "arg:6-11"],
        }
    ]

def build_predicate_ref_map(atoms):
    refs = {}
    for index, atom in enumerate(atoms):
        ref = atom.get("atom_id") or f"pnf:{index}"
        refs[ref] = atom
    return refs

def build_predicate_index(atoms):
    refs = [atom.get("atom_id", "pnf:0") for atom in atoms]
    return {
        "by_structural_sig": {"pay": refs},
        "by_role_slot": {"subject": refs, "object": refs},
        "by_argval": {"tenant": refs, "rent": refs},
        "by_role_arg": {("subject", "tenant"): refs, ("object", "rent"): refs},
    }
""",
    )

    itir._load_enrichment.cache_clear()
    for module_name in list(sys.modules):
        if module_name == "sensiblaw" or module_name.startswith("sensiblaw."):
            sys.modules.pop(module_name, None)
    monkeypatch.setenv("MYCHATARCHIVE_ITIR_PATHS", str(fake_root))

    payload = itir.enrich_text("hello world")

    assert payload is not None
    assert payload["surface"] == "sensiblaw.interfaces.shared_reducer"
    assert payload["source_root"] == str(fake_root.resolve())
    assert payload["token_spans"][0]["text"] == "hello"
    assert payload["lexeme_refs"][0]["occurrence_id"] == "abc"
    assert payload["structure_occurrences"][0]["kind"] == "word_ref"
    assert payload["relational_bundle"]["version"] == "relational_bundle_v1"
    assert payload["predicate_projection"]["version"] == "itir_predicate_projection_v1"
    assert payload["predicate_projection"]["predicates"][0]["ref"] == "pred:1"
    assert payload["predicate_projection"]["predicates"][0]["predicate"] == "pay"
    assert payload["predicate_projection"]["predicates"][0]["structural_signature"] == "pay"
    assert payload["predicate_projection"]["predicates"][0]["polarity"] == "negative"
    assert payload["predicate_projection"]["predicates"][0]["modality"] == "must"
    assert payload["predicate_projection"]["predicates"][0]["roles"][0]["name"] == "object"
    assert payload["predicate_projection"]["predicates"][0]["source_spans"][0]["start"] == 0
    assert payload["predicate_projection"]["index"]["by_role_argument"]["subject|tenant"] == ["pred:1"]


def test_enrich_text_degrades_when_projection_surfaces_missing(monkeypatch, tmp_path):
    fake_root = tmp_path / "SensibLaw"
    _write(fake_root / "src" / "sensiblaw" / "__init__.py", "")
    _write(fake_root / "src" / "sensiblaw" / "interfaces" / "__init__.py", "")
    _write(
        fake_root / "src" / "sensiblaw" / "interfaces" / "shared_reducer.py",
        """
def get_canonical_tokenizer_profile_receipt():
    return {"profile_id": "fake"}

def tokenize_canonical_with_spans(text):
    return [("hello", 0, 5)]

def collect_canonical_lexeme_refs(text):
    return [{"occurrence_id": "abc"}]

def collect_canonical_structure_occurrences(text):
    return []

def collect_canonical_relational_bundle(text):
    return {"version": "relational_bundle_v1", "canonical_text": text, "atoms": [], "relations": []}
""",
    )

    itir._load_enrichment.cache_clear()
    for module_name in list(sys.modules):
        if module_name == "sensiblaw" or module_name.startswith("sensiblaw."):
            sys.modules.pop(module_name, None)
    monkeypatch.setenv("MYCHATARCHIVE_ITIR_PATHS", str(fake_root))
    payload = itir.enrich_text("hello")

    assert payload is not None
    assert payload["surface"] == "sensiblaw.interfaces.shared_reducer"
    assert payload["relational_bundle"]["version"] == "relational_bundle_v1"
    assert payload["predicate_projection_error"] == "RuntimeError"
    assert "predicate_projection" not in payload


def test_enrich_text_raises_when_missing(monkeypatch, tmp_path):
    itir._load_enrichment.cache_clear()
    monkeypatch.setattr("mychatarchive.itir.get_itir_paths", lambda: [tmp_path / "missing"])
    monkeypatch.delenv("MYCHATARCHIVE_ITIR_PATHS", raising=False)
    with pytest.raises(RuntimeError):
        itir.enrich_text("hello world")
