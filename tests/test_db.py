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
