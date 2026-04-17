"""Required ITIR/SensibLaw enrichment for imported messages.

MyChatArchive should not silently ingest messages without the shared-reducer
normalization payload. Imports fail fast when the local ITIR surface is
unavailable.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from functools import lru_cache
import importlib
from pathlib import Path
import sys
from typing import Any

from mychatarchive.config import get_itir_paths

_SHARED_REDUCER_MODULE = "sensiblaw.interfaces.shared_reducer"


class ITIREnrichment:
    """Wrapper around the supported SensibLaw shared reducer surface."""

    def __init__(self, root: Path, reducer_module):
        self.root = root
        self.reducer_module = reducer_module

    def enrich_text(self, text: str) -> dict[str, Any] | None:
        text = (text or "").strip()
        if not text:
            return None

        reducer = self.reducer_module
        payload: dict[str, Any] = {
            "surface": _SHARED_REDUCER_MODULE,
            "source_root": str(self.root),
            "tokenizer_profile_receipt": reducer.get_canonical_tokenizer_profile_receipt(),
            "token_spans": [
                {"text": token, "start": start, "end": end}
                for token, start, end in reducer.tokenize_canonical_with_spans(text)
            ],
            "lexeme_refs": reducer.collect_canonical_lexeme_refs(text),
            "structure_occurrences": [
                _to_plain_dict(occ)
                for occ in reducer.collect_canonical_structure_occurrences(text)
            ],
        }

        try:
            payload["relational_bundle"] = reducer.collect_canonical_relational_bundle(text)
        except Exception as exc:
            payload["relational_bundle_error"] = type(exc).__name__

        return payload


def _to_plain_dict(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return {key: _to_plain_dict(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_plain_dict(item) for item in value]
    return value


def _candidate_sys_paths(root: Path) -> list[Path]:
    candidates: list[Path] = []
    if root.name == "src":
        candidates.append(root.parent)
        candidates.append(root)
    else:
        candidates.append(root)
        src_dir = root / "src"
        if src_dir.is_dir():
            candidates.append(src_dir)
    return [path for path in candidates if path.exists()]


def _import_shared_reducer(root: Path):
    original_sys_path = list(sys.path)
    for candidate in reversed(_candidate_sys_paths(root)):
        candidate_str = str(candidate)
        if candidate_str not in sys.path:
            sys.path.insert(0, candidate_str)
    try:
        return importlib.import_module(_SHARED_REDUCER_MODULE)
    finally:
        sys.path[:] = original_sys_path


def _normalize_candidate_path(path: Path) -> Path:
    path = path.expanduser().resolve()
    if path.name == "shared_reducer.py":
        return path.parent.parent.parent
    if path.name == "interfaces":
        return path.parent.parent
    if path.name == "sensiblaw":
        return path.parent
    return path


def _iter_candidate_roots() -> tuple[Path, ...]:
    configured = tuple(_normalize_candidate_path(path) for path in get_itir_paths())
    if configured:
        return configured

    repo_root = Path(__file__).resolve().parents[2]
    defaults = (
        repo_root.parent / "ITIR-suite" / "SensibLaw",
        Path.home() / "Documents" / "code" / "ITIR-suite" / "SensibLaw",
    )
    return tuple(path for path in defaults if path.exists())


@lru_cache(maxsize=8)
def _load_enrichment(root_value: str) -> ITIREnrichment | None:
    root = Path(root_value)
    if not root.exists():
        return None
    try:
        reducer_module = _import_shared_reducer(root)
    except Exception:
        return None
    return ITIREnrichment(root=root, reducer_module=reducer_module)


def get_itir_enrichment() -> ITIREnrichment | None:
    for root in _iter_candidate_roots():
        enrichment = _load_enrichment(str(root))
        if enrichment is not None:
            return enrichment
    return None


def enrich_text(text: str) -> dict[str, Any] | None:
    text = (text or "").strip()
    if not text:
        return None

    enrichment = get_itir_enrichment()
    if enrichment is None:
        roots = ", ".join(str(path) for path in _iter_candidate_roots()) or "<none found>"
        raise RuntimeError(
            "Required ITIR shared reducer is unavailable. "
            f"Checked roots: {roots}. Configure config.itir.paths or "
            "MYCHATARCHIVE_ITIR_PATHS."
        )
    return enrichment.enrich_text(text)
