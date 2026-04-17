"""Tests for required ITIR enrichment."""

from __future__ import annotations

from pathlib import Path

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
""",
    )

    itir._load_enrichment.cache_clear()
    monkeypatch.setenv("MYCHATARCHIVE_ITIR_PATHS", str(fake_root))

    payload = itir.enrich_text("hello world")

    assert payload is not None
    assert payload["surface"] == "sensiblaw.interfaces.shared_reducer"
    assert payload["source_root"] == str(fake_root.resolve())
    assert payload["token_spans"][0]["text"] == "hello"
    assert payload["lexeme_refs"][0]["occurrence_id"] == "abc"
    assert payload["structure_occurrences"][0]["kind"] == "word_ref"
    assert payload["relational_bundle"]["version"] == "relational_bundle_v1"


def test_enrich_text_raises_when_missing(monkeypatch, tmp_path):
    itir._load_enrichment.cache_clear()
    monkeypatch.setattr("mychatarchive.itir.get_itir_paths", lambda: [tmp_path / "missing"])
    monkeypatch.delenv("MYCHATARCHIVE_ITIR_PATHS", raising=False)
    with pytest.raises(RuntimeError):
        itir.enrich_text("hello world")
