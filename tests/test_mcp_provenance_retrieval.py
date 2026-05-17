"""Tests for provenance/governance-aware retrieval explanations."""

from __future__ import annotations

import json
import sys
import tempfile
import types
from pathlib import Path

import pytest

from mychatarchive import db
from mychatarchive.config import get_embedding_dim
from mychatarchive.retrieval_explain import build_provenance_ranked_chunk_results


@pytest.fixture
def provenance_db():
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as f:
        db_path = Path(f.name)
    con = db.get_connection(db_path)
    db.ensure_schema(con)
    yield con, db_path
    con.close()
    db_path.unlink(missing_ok=True)


def _vec(value: float) -> list[float]:
    return [value] * get_embedding_dim()


def _seed_two_messages(con) -> None:
    predicate_projection = {
        "version": "itir_predicate_projection_v1",
        "predicates": [
            {
                "ref": "pred:1",
                "predicate": "pay",
                "structural_signature": "pay",
                "roles": [
                    {"name": "subject", "value": "tenant"},
                    {"name": "object", "value": "rent"},
                ],
                "polarity": "positive",
                "modality": "must",
                "provenance_refs": ["pred:1"],
                "source_spans": [{"start": 0, "end": 5, "ref": "head:0-5"}],
                "wrapper": {"status": "structural_projection", "evidence_only": True},
            }
        ],
    }
    db.insert_message(
        con, "msg_prov", "thread_prov", "chatgpt", "main",
        "2026-01-01T00:00:00Z", "assistant", "Provenance rich answer", "Provenance",
        "src_prov", "upstream-thread-1", "upstream-msg-1",
        {
            "itir": {
                "tokenizer_profile_receipt": {"profile_id": "deterministic-legal"},
                "relational_bundle": {"version": "relational_bundle_v1"},
                "predicate_projection": predicate_projection,
            }
        },
        source_path="/exports/chatgpt/export-a.json",
        source_bucket="chatgpt_export",
        provenance_json={"root_import_id": "imp-1"},
    )
    db.insert_chunk(
        con, "chunk_prov", "msg_prov", "thread_prov", 0, "Provenance rich answer",
        "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", _vec(0.10),
        {"role": "assistant", "itir": {"tokenizer_profile_receipt": {"profile_id": "deterministic-legal"}}},
    )

    db.insert_message_block(
        con,
        block_id="blk-1",
        message_id="msg_prov",
        canonical_thread_id="thread_prov",
        block_index=0,
        block_type="text",
        text="Provenance rich answer",
        source_path="/exports/chatgpt/export-a.json",
        source_bucket="chatgpt_export",
        provenance_json={"span": [0, 21]},
    )
    db.insert_provenance_ref(
        con,
        provenance_ref_id="pref-1",
        message_id="msg_prov",
        block_id="blk-1",
        ref_index=0,
        source_id="src_prov",
        source_thread_id="upstream-thread-1",
        source_message_id="upstream-msg-1",
        source_path="/exports/chatgpt/export-a.json",
        source_bucket="chatgpt_export",
        locator_json={"line": 12},
    )
    db.insert_predicate_projection(con, "msg_prov", "thread_prov", predicate_projection)

    db.insert_message(
        con, "msg_plain", "thread_plain", "chatgpt", "main",
        "2026-01-01T00:00:01Z", "assistant", "Semantically strong but weak provenance",
        "Plain", "src_plain",
    )
    db.insert_chunk(
        con, "chunk_plain", "msg_plain", "thread_plain", 0,
        "Semantically strong but weak provenance",
        "2026-01-01T00:00:01Z", "2026-01-01T00:00:01Z", _vec(0.12),
        {"role": "assistant"},
    )
    con.commit()


def test_provenance_ranker_can_promote_governed_results(provenance_db):
    con, _ = provenance_db
    _seed_two_messages(con)

    semantic_matches = [
        ("chunk_plain", 0.01),  # higher semantic similarity
        ("chunk_prov", 0.35),   # lower semantic similarity, much stronger provenance
    ]
    ranked = build_provenance_ranked_chunk_results(
        con,
        semantic_matches,
        limit=2,
        governance_weight=0.50,
        rerank_by_governance=True,
    )

    assert [row["chunk_id"] for row in ranked] == ["chunk_prov", "chunk_plain"]
    assert ranked[0]["schema"] == "mychatarchive.semantic_result.v1"
    assert ranked[0]["result_type"] == "message_chunk"
    assert ranked[0]["source_db"].endswith(".sqlite")
    assert ranked[0]["canonical_thread_id"] == "thread_prov"
    assert ranked[0]["thread_id"] == "thread_prov"
    assert ranked[0]["message_id"] == "msg_prov"
    assert ranked[0]["source_thread_id"] == "upstream-thread-1"
    assert ranked[0]["source_message_id"] == "upstream-msg-1"
    assert ranked[0]["timestamp_start"] == "2026-01-01T00:00:00Z"
    assert ranked[0]["timestamp_end"] == "2026-01-01T00:00:00Z"
    assert ranked[0]["excerpt"] == "Provenance rich answer"
    assert ranked[0]["scores"]["semantic_distance"] == pytest.approx(0.35)
    assert ranked[0]["scores"]["rank_score"] == ranked[0]["rank_score"]
    assert ranked[0]["provenance"]["canonical_thread_id"] == "thread_prov"
    assert ranked[0]["provenance"]["chunk_id"] == "chunk_prov"
    assert ranked[0]["provenance"]["source_id"] == "src_prov"
    assert ranked[0]["provenance"]["source_path"] == "/exports/chatgpt/export-a.json"
    assert ranked[0]["provenance"]["provenance_json"] == {"root_import_id": "imp-1"}
    assert ranked[0]["governance"]["tier"] == "high"
    assert "provenance_refs=1" in ranked[0]["governance"]["signals"]
    assert "predicate_refs=1" in ranked[0]["governance"]["signals"]
    assert ranked[0]["governance"]["itir"]["tokenizer_profile_id"] == "deterministic-legal"
    assert ranked[0]["rank_score"] > ranked[1]["rank_score"]


def test_provenance_ranker_surfaces_predicate_residuals(provenance_db):
    con, _ = provenance_db
    _seed_two_messages(con)

    semantic_matches = [("chunk_prov", 0.20)]
    query_projection = {
        "version": "itir_predicate_projection_v1",
        "predicates": [
            {
                "ref": "query:1",
                "predicate": "pay",
                "structural_signature": "pay",
                "roles": [
                    {"name": "subject", "value": "tenant"},
                    {"name": "object", "value": "rent"},
                ],
                "polarity": "positive",
            }
        ],
    }
    ranked = build_provenance_ranked_chunk_results(
        con,
        semantic_matches,
        limit=1,
        governance_weight=0.30,
        rerank_by_governance=True,
        query_projection=query_projection,
    )

    assert ranked[0]["governance"]["predicate_residual"]["available"] is True
    assert ranked[0]["governance"]["predicate_residual"]["shared_predicates"] == ["pay"]
    assert ranked[0]["governance"]["predicate_residual"]["contradictions"] == []


def test_provenance_result_warns_instead_of_fabricating_missing_ids(provenance_db):
    con, _ = provenance_db
    db.insert_chunk(
        con, "chunk_orphan", None, "thread_orphan", 0,
        "Chunk without a source message",
        "2026-01-01T00:00:02Z", "2026-01-01T00:00:02Z", _vec(0.13),
        {"role": "assistant"},
    )
    con.commit()

    ranked = build_provenance_ranked_chunk_results(
        con,
        [("chunk_orphan", 0.05)],
        limit=1,
    )

    assert ranked[0]["message_id"] is None
    assert ranked[0]["canonical_thread_id"] == "thread_orphan"
    assert ranked[0]["provenance"]["message_id"] is None
    assert ranked[0]["scores"]["semantic_distance"] == pytest.approx(0.05)
    assert ranked[0]["warnings"][0]["code"] == "semantic_result_missing_provenance"
    assert "message_id" in ranked[0]["warnings"][0]["missing_fields"]
    assert "provenance.canonical_thread_id" not in ranked[0]["warnings"][0]["missing_fields"]


def test_search_brain_governed_returns_explanation_payload(monkeypatch, provenance_db):
    con, _ = provenance_db
    _seed_two_messages(con)

    class _DummyFastMCP:
        def __init__(self, *_args, **_kwargs):
            pass

        def tool(self):
            def _decorator(fn):
                return fn
            return _decorator

        def run(self, *_args, **_kwargs):
            return None

    fake_mcp = types.ModuleType("mcp")
    fake_server_pkg = types.ModuleType("mcp.server")
    fake_fastmcp = types.ModuleType("mcp.server.fastmcp")
    fake_fastmcp.FastMCP = _DummyFastMCP

    monkeypatch.setitem(sys.modules, "mcp", fake_mcp)
    monkeypatch.setitem(sys.modules, "mcp.server", fake_server_pkg)
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", fake_fastmcp)

    from mychatarchive.mcp import server

    monkeypatch.setattr(server, "_get_con", lambda: con)
    monkeypatch.setattr(server, "_lazy_embed", lambda _query: _vec(0.2))
    monkeypatch.setattr(
        server,
        "enrich_text_with_itir",
        lambda _query: {
            "predicate_projection": {
                "version": "itir_predicate_projection_v1",
                "predicates": [
                    {
                        "ref": "query:1",
                        "predicate": "pay",
                        "structural_signature": "pay",
                        "roles": [
                            {"name": "subject", "value": "tenant"},
                            {"name": "object", "value": "rent"},
                        ],
                        "polarity": "positive",
                    }
                ],
            }
        },
    )
    monkeypatch.setattr(
        db,
        "search_chunks",
        lambda *_args, **_kwargs: [("chunk_plain", 0.01), ("chunk_prov", 0.35)],
    )

    payload = json.loads(
        server.search_brain_governed(
            "governed retrieval",
            limit=2,
            rerank_by_governance=True,
            governance_weight=0.50,
        )
    )

    assert payload["count"] == 2
    assert payload["scoring"]["governance_weight"] == 0.5
    assert payload["results"][0]["chunk_id"] == "chunk_prov"
    assert payload["results"][0]["governance"]["source"]["source_path"] == (
        "/exports/chatgpt/export-a.json"
    )
    assert payload["results"][0]["governance"]["evidence_counts"]["provenance_refs"] == 1
    assert payload["results"][0]["governance"]["evidence_counts"]["predicate_refs"] == 1
    assert payload["results"][0]["governance"]["predicate_residual"]["shared_predicates"] == ["pay"]


def test_search_brain_governed_can_surface_predicate_only_candidates(monkeypatch, provenance_db):
    con, _ = provenance_db
    _seed_two_messages(con)

    class _DummyFastMCP:
        def __init__(self, *_args, **_kwargs):
            pass

        def tool(self):
            def _decorator(fn):
                return fn
            return _decorator

        def run(self, *_args, **_kwargs):
            return None

    fake_mcp = types.ModuleType("mcp")
    fake_server_pkg = types.ModuleType("mcp.server")
    fake_fastmcp = types.ModuleType("mcp.server.fastmcp")
    fake_fastmcp.FastMCP = _DummyFastMCP

    monkeypatch.setitem(sys.modules, "mcp", fake_mcp)
    monkeypatch.setitem(sys.modules, "mcp.server", fake_server_pkg)
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", fake_fastmcp)

    from mychatarchive.mcp import server

    monkeypatch.setattr(server, "_get_con", lambda: con)
    monkeypatch.setattr(server, "_lazy_embed", lambda _query: _vec(0.2))
    monkeypatch.setattr(
        server,
        "enrich_text_with_itir",
        lambda _query: {
            "predicate_projection": {
                "version": "itir_predicate_projection_v1",
                "predicates": [
                    {
                        "ref": "query:1",
                        "predicate": "pay",
                        "structural_signature": "pay",
                        "roles": [
                            {"name": "subject", "value": "tenant"},
                            {"name": "object", "value": "rent"},
                        ],
                        "polarity": "positive",
                    }
                ],
            }
        },
    )
    monkeypatch.setattr(
        db,
        "search_chunks",
        lambda *_args, **_kwargs: [],
    )

    payload = json.loads(
        server.search_brain_governed(
            "tenant must pay rent",
            limit=2,
            rerank_by_governance=True,
            governance_weight=0.30,
        )
    )

    assert payload["count"] == 1
    assert payload["scoring"]["predicate_candidates"] == 1
    assert payload["results"][0]["chunk_id"] == "chunk_prov"
    assert payload["results"][0]["semantic_similarity"] == 0.0
    assert payload["results"][0]["predicate_similarity"] > 0.0
    assert "predicate_candidate_matches=pay" in payload["results"][0]["governance"]["signals"]
