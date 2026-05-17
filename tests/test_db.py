"""Tests for database operations."""

import tempfile
from pathlib import Path

import pytest

from mychatarchive import db


@pytest.fixture
def test_db():
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as f:
        db_path = Path(f.name)
    con = db.get_connection(db_path)
    db.ensure_schema(con)
    yield con, db_path
    con.close()
    db_path.unlink(missing_ok=True)


def test_schema_creation(test_db):
    con, _ = test_db
    tables = [
        row[0]
        for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    ]
    assert "messages" in tables
    assert "chunks" in tables
    assert "thoughts" in tables
    cols = {
        row[1]
        for row in con.execute("PRAGMA table_info(messages)").fetchall()
    }
    assert "meta" in cols
    assert "source_thread_id" in cols
    assert "source_message_id" in cols
    assert "source_path" in cols
    assert "source_bucket" in cols
    assert "provenance_json" in cols
    assert "message_blocks" in tables
    assert "provenance_refs" in tables
    assert "predicate_refs" in tables
    assert "predicate_roles" in tables


def test_insert_message(test_db):
    con, _ = test_db
    result = db.insert_message(
        con, "msg1", "thread1", "chatgpt", "main",
        "2026-01-01T00:00:00Z", "user", "Hello world", "Test", "src1",
        "conv-123", "msg-upstream-1",
        {"itir": {"tokenizer_profile_receipt": {"profile_id": "abc"}}},
    )
    assert result is True
    assert db.message_count(con) == 1
    stored = con.execute(
        "SELECT source_thread_id, source_message_id, meta FROM messages WHERE message_id = ?",
        ("msg1",),
    ).fetchone()
    assert stored[0] == "conv-123"
    assert stored[1] == "msg-upstream-1"
    assert stored[2] is not None
    assert "profile_id" in stored[2]

    dupe = db.insert_message(
        con, "msg1", "thread1", "chatgpt", "main",
        "2026-01-01T00:00:00Z", "user", "Hello world", "Test", "src1",
    )
    assert dupe is False
    assert db.message_count(con) == 1


def test_insert_message_with_extended_provenance(test_db):
    con, _ = test_db
    result = db.insert_message(
        con, "msg-provenance-1", "thread-provenance", "chatgpt", "main",
        "2026-01-02T00:00:00Z", "assistant", "Answer", "Test", "src-provenance",
        source_thread_id="conv-456",
        source_message_id="msg-upstream-2",
        source_path="/exports/chatgpt/account-a/export.json",
        source_bucket="chatgpt_export",
        provenance_json={
            "root_import_id": "imp-1",
            "trace": [{"step": "parse", "parser": "chatgpt"}],
        },
        meta={"ingest": {"stage": "1"}},
    )
    assert result is True

    stored = con.execute(
        """
        SELECT source_path, source_bucket, provenance_json, meta
        FROM messages WHERE message_id = ?
        """,
        ("msg-provenance-1",),
    ).fetchone()
    assert stored is not None
    assert stored[0] == "/exports/chatgpt/account-a/export.json"
    assert stored[1] == "chatgpt_export"
    assert '"root_import_id": "imp-1"' in stored[2]
    assert '"stage": "1"' in stored[3]

    row = next(iter(db.iter_messages(con, batch_size=1)))
    assert row["source_path"] == "/exports/chatgpt/account-a/export.json"
    assert row["source_bucket"] == "chatgpt_export"
    assert row["provenance_json"]["root_import_id"] == "imp-1"

    exported = db.export_messages(con)
    assert exported[0]["source_path"] == "/exports/chatgpt/account-a/export.json"
    assert exported[0]["source_bucket"] == "chatgpt_export"
    assert exported[0]["provenance_json"]["root_import_id"] == "imp-1"


def test_insert_chunk_and_search(test_db):
    con, _ = test_db
    fake_embedding = [0.1] * 384
    db.insert_chunk(
        con, "chunk1", "msg1", "thread1", 0,
        "Test chunk text", "2026-01-01", "2026-01-01",
        fake_embedding, {"role": "user"},
    )
    con.commit()
    assert db.chunk_count(con) == 1

    results = db.search_chunks(con, fake_embedding, limit=5)
    assert len(results) > 0
    assert results[0][0] == "chunk1"


def test_insert_thought_and_search(test_db):
    con, _ = test_db
    fake_embedding = [0.2] * 384
    db.insert_thought(
        con, "thought1", "A test thought", "2026-01-01T00:00:00Z",
        fake_embedding, {"tags": ["test"]},
    )
    con.commit()
    assert db.thought_count(con) == 1

    results = db.search_thoughts(con, fake_embedding, limit=5)
    assert len(results) > 0
    assert results[0][0] == "thought1"


def test_message_schema_migration_preserves_existing_rows():
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as f:
        db_path = Path(f.name)

    con = db.get_connection(db_path)
    try:
        con.executescript("""
            CREATE TABLE messages (
                message_id TEXT PRIMARY KEY,
                canonical_thread_id TEXT NOT NULL,
                platform TEXT NOT NULL,
                account_id TEXT NOT NULL,
                ts TEXT NOT NULL,
                role TEXT NOT NULL,
                text TEXT NOT NULL,
                title TEXT,
                source_id TEXT NOT NULL,
                source_thread_id TEXT,
                source_message_id TEXT,
                meta TEXT
            );
            INSERT INTO messages (
                message_id, canonical_thread_id, platform, account_id, ts, role,
                text, title, source_id, source_thread_id, source_message_id, meta
            )
            VALUES (
                'legacy-msg-1', 'legacy-thread', 'chatgpt', 'main',
                '2025-01-01T00:00:00Z', 'user', 'Legacy body', 'Legacy title',
                'legacy-src', 'legacy-upstream-thread', 'legacy-upstream-message',
                '{"legacy": true}'
            );
        """)
        con.commit()

        db.ensure_schema(con)

        cols = {
            row[1]
            for row in con.execute("PRAGMA table_info(messages)").fetchall()
        }
        assert "source_path" in cols
        assert "source_bucket" in cols
        assert "provenance_json" in cols

        row = con.execute(
            """
            SELECT text, source_thread_id, source_message_id, source_path, source_bucket,
                   provenance_json, meta
            FROM messages WHERE message_id = 'legacy-msg-1'
            """
        ).fetchone()
        assert row is not None
        assert row[0] == "Legacy body"
        assert row[1] == "legacy-upstream-thread"
        assert row[2] == "legacy-upstream-message"
        assert row[3] is None
        assert row[4] is None
        assert row[5] is None
        assert row[6] == '{"legacy": true}'

        tables = {
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "message_blocks" in tables
        assert "provenance_refs" in tables
    finally:
        con.close()
        db_path.unlink(missing_ok=True)


def test_message_blocks_and_provenance_refs_helpers(test_db):
    con, _ = test_db
    inserted = db.insert_message(
        con, "msg-block-1", "thread-block", "chatgpt", "main",
        "2026-01-03T00:00:00Z", "user", "source text", "Title", "src-block",
    )
    assert inserted is True

    block_inserted = db.insert_message_block(
        con,
        block_id="blk-1",
        message_id="msg-block-1",
        canonical_thread_id="thread-block",
        block_index=0,
        block_type="text",
        text="source text",
        source_path="/exports/a.json",
        source_bucket="chatgpt_export",
        provenance_json={"span": [0, 11]},
        meta={"normalized": True},
    )
    assert block_inserted is True
    assert db.insert_message_block(
        con,
        block_id="blk-1",
        message_id="msg-block-1",
        canonical_thread_id="thread-block",
        block_index=0,
        block_type="text",
        text="source text",
    ) is False

    ref_inserted = db.insert_provenance_ref(
        con,
        provenance_ref_id="pref-1",
        message_id="msg-block-1",
        block_id="blk-1",
        ref_index=0,
        source_id="src-block",
        source_thread_id="upstream-thread",
        source_message_id="upstream-message",
        source_path="/exports/a.json",
        source_bucket="chatgpt_export",
        locator_json={"offset": 0},
        meta={"score": 1.0},
    )
    assert ref_inserted is True
    assert db.insert_provenance_ref(
        con,
        provenance_ref_id="pref-1",
        message_id="msg-block-1",
        block_id="blk-1",
        ref_index=0,
    ) is False

    blocks = db.list_message_blocks(con, message_id="msg-block-1")
    assert len(blocks) == 1
    assert blocks[0]["block_id"] == "blk-1"
    assert blocks[0]["source_bucket"] == "chatgpt_export"
    assert blocks[0]["provenance_json"]["span"] == [0, 11]

    refs = db.list_provenance_refs(con, message_id="msg-block-1")
    assert len(refs) == 1
    assert refs[0]["provenance_ref_id"] == "pref-1"
    assert refs[0]["source_thread_id"] == "upstream-thread"
    assert refs[0]["locator_json"]["offset"] == 0


def test_predicate_projection_helpers(test_db):
    con, _ = test_db
    inserted = db.insert_message(
        con, "msg-pred-1", "thread-pred", "chatgpt", "main",
        "2026-01-04T00:00:00Z", "assistant", "Predicate text", "Pred", "src-pred",
    )
    assert inserted is True

    projection = {
        "version": "itir_predicate_projection_v1",
        "limits": {"max_predicates": 64},
        "counts": {"predicates_total": 1, "predicates_projected": 1, "predicates_truncated": False},
        "predicates": [
            {
                "ref": "pred:1",
                "atom_id": "pred:1",
                "predicate": "pay",
                "structural_signature": "pay",
                "roles": [
                    {"name": "subject", "value": "tenant", "status": "bound", "provenance_refs": ["head:0-5"]},
                    {"name": "object", "value": "rent", "status": "bound", "provenance_refs": ["arg:6-10"]},
                ],
                "polarity": "positive",
                "modality": "must",
                "provenance_refs": ["pred:1"],
                "source_spans": [{"start": 0, "end": 5, "ref": "head:0-5"}],
                "wrapper": {"status": "structural_projection", "evidence_only": True},
            }
        ],
        "index": {"by_structural_signature": {"pay": ["pred:1"]}},
    }
    count = db.insert_predicate_projection(con, "msg-pred-1", "thread-pred", projection)
    assert count == 1

    predicate_refs = db.list_predicate_refs(con, message_id="msg-pred-1")
    assert len(predicate_refs) == 1
    assert predicate_refs[0]["predicate"] == "pay"
    assert predicate_refs[0]["modality"] == "must"

    predicate_roles = db.list_predicate_roles(con, message_id="msg-pred-1")
    assert len(predicate_roles) == 2
    assert {role["role_name"] for role in predicate_roles} == {"subject", "object"}


def test_search_predicate_candidates_returns_scored_shortlist(test_db):
    con, _ = test_db
    db.insert_message(
        con,
        "msg-pred-search-1",
        "thread-pred-search",
        "chatgpt",
        "main",
        "2026-02-01T00:00:00Z",
        "assistant",
        "Tenant must pay rent immediately.",
        "Predicate search",
        "src-pred-search",
    )
    db.insert_chunk(
        con,
        "chunk-pred-search-1",
        "msg-pred-search-1",
        "thread-pred-search",
        0,
        "Tenant must pay rent immediately.",
        "2026-02-01T00:00:00Z",
        "2026-02-01T00:00:00Z",
        [0.3] * 384,
        {"role": "assistant"},
    )
    db.insert_predicate_projection(
        con,
        "msg-pred-search-1",
        "thread-pred-search",
        {
            "version": "itir_predicate_projection_v1",
            "predicates": [
                {
                    "ref": "pred:pay",
                    "predicate": "pay",
                    "structural_signature": "pay",
                    "roles": [
                        {"name": "subject", "value": "tenant"},
                        {"name": "object", "value": "rent"},
                    ],
                    "polarity": "positive",
                }
            ],
        },
    )
    con.commit()

    matches = db.search_predicate_candidates(
        con,
        ["pay"],
        ["subject|tenant", "object|rent"],
        limit=5,
    )

    assert len(matches) == 1
    assert matches[0]["message_id"] == "msg-pred-search-1"
    assert matches[0]["chunk_id"] == "chunk-pred-search-1"
    assert matches[0]["matched_signatures"] == ["pay"]
    assert set(matches[0]["matched_role_arguments"]) == {"subject|tenant", "object|rent"}
    assert matches[0]["predicate_score"] == pytest.approx(1.0)
