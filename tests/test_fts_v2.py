"""Tests for the v0.3.0 external-content FTS5 migration, bm25 ranking, query
escaping, and the self-describing archive_meta dimension guard."""

import sqlite3
import tempfile
from pathlib import Path

import pytest

from mychatarchive.backends.storage import sqlite as store


@pytest.fixture
def con():
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as f:
        db_path = Path(f.name)
    c = store.get_connection(db_path)
    store.ensure_schema(c)
    yield c
    c.close()
    db_path.unlink(missing_ok=True)


def _insert(c, mid, text, ts, thread="t1", platform="chatgpt"):
    store.insert_message(c, mid, thread, platform, "main", ts, "user", text, "Title", "src")
    c.commit()


def test_fts_ranks_by_relevance_not_insertion_order(con):
    # msg1 inserted first, mentions the term once inside a long message.
    _insert(con, "m1",
            "the meeting covered budget timelines hiring python and lunch options at length",
            "2026-01-01T00:00:00Z")
    # msg2 inserted second, short and densely about the term.
    _insert(con, "m2", "python python tips", "2026-01-02T00:00:00Z")

    rows = store.fts_search(con, "python", limit=10)
    ids = [r[0] for r in rows]
    assert ids == ["m2", "m1"], f"bm25 should rank the dense short match first, got {ids}"


def test_fts_sort_by_time_overrides_relevance(con):
    _insert(con, "m1", "python python tips", "2026-01-01T00:00:00Z")
    _insert(con, "m2", "a single python mention here", "2026-02-01T00:00:00Z")
    rows = store.fts_search(con, "python", limit=10, sort_by_time=True)
    ids = [r[0] for r in rows]
    assert ids == ["m2", "m1"], f"sort_by_time should be reverse-chronological, got {ids}"


@pytest.mark.parametrize("q", [
    "foo-bar",            # hyphen
    'quote " mark',       # bare double quote
    "C:",                 # colon
    "AND OR NOT NEAR",    # bare operators
    "(unbalanced",        # bare paren
    "wild*card",          # star
    "i've can't",         # apostrophes
    "^caret",             # column-filter char
])
def test_fts_never_raises_on_hostile_input(con, q):
    _insert(con, "m1", "some ordinary content", "2026-01-01T00:00:00Z")
    # Must not raise sqlite3.OperationalError (fts5 syntax error).
    result = store.fts_search(con, q, limit=5)
    assert isinstance(result, list)


def test_fts_finds_hyphenated_and_apostrophe_terms(con):
    _insert(con, "m1", "we shipped the well-known feature", "2026-01-01T00:00:00Z")
    _insert(con, "m2", "i've been testing it", "2026-01-02T00:00:00Z")
    # Hyphen/apostrophe tokens are quoted into phrase queries: "well-known" ->
    # [well, known] adjacent, "i've" -> [i, ve] adjacent, both matching the
    # stored text under the default unicode61 tokenizer.
    assert [r[0] for r in store.fts_search(con, "well-known", limit=5)] == ["m1"]
    assert [r[0] for r in store.fts_search(con, "i've", limit=5)] == ["m2"]


def test_external_content_no_docid_table(con):
    tables = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "messages_fts_docids" not in tables, "docid map should be gone in v0.3.0"
    fts_sql = con.execute(
        "SELECT sql FROM sqlite_master WHERE name='messages_fts'").fetchone()[0]
    assert "content='messages'" in fts_sql


def test_delete_message_updates_fts(con):
    _insert(con, "m1", "deletable python content", "2026-01-01T00:00:00Z")
    assert len(store.fts_search(con, "python", limit=5)) == 1
    con.execute("DELETE FROM messages WHERE message_id = 'm1'")
    con.commit()
    assert store.fts_search(con, "python", limit=5) == [], "delete trigger should purge FTS"


def test_migration_from_contentless_fts():
    """A pre-0.3.0 archive (contentless FTS + docid map) migrates in place and
    search works afterward."""
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as f:
        db_path = Path(f.name)
    try:
        c = store.get_connection(db_path)
        # Build the OLD structure by hand (messages + contentless fts + docids).
        c.executescript("""
            CREATE TABLE messages (
                message_id TEXT PRIMARY KEY, canonical_thread_id TEXT NOT NULL,
                platform TEXT NOT NULL, account_id TEXT NOT NULL, ts TEXT NOT NULL,
                role TEXT NOT NULL, text TEXT NOT NULL, title TEXT, source_id TEXT NOT NULL
            );
            CREATE VIRTUAL TABLE messages_fts USING fts5(text, content='');
            CREATE TABLE messages_fts_docids (rowid INTEGER PRIMARY KEY, message_id TEXT NOT NULL);
        """)
        c.execute("INSERT INTO messages VALUES ('old1','t1','chatgpt','main','2026-01-01T00:00:00Z','user','legacy python row','T','s')")
        c.execute("INSERT INTO messages_fts (text) VALUES ('legacy python row')")
        rowid = c.execute("SELECT max(rowid) FROM messages_fts").fetchone()[0]
        c.execute("INSERT INTO messages_fts_docids (rowid, message_id) VALUES (?, 'old1')", (rowid,))
        c.commit()

        # Now run the v0.3.0 schema — should migrate.
        store.ensure_schema(c)

        tables = {r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "messages_fts_docids" not in tables
        fts_sql = c.execute("SELECT sql FROM sqlite_master WHERE name='messages_fts'").fetchone()[0]
        assert "content='messages'" in fts_sql
        # The rebuilt index finds the pre-existing row.
        assert [r[0] for r in store.fts_search(c, "python", limit=5)] == ["old1"]
        c.close()
    finally:
        db_path.unlink(missing_ok=True)


def test_archive_meta_stamped_on_fresh_db(con):
    meta = dict(con.execute("SELECT key, value FROM archive_meta").fetchall())
    assert meta["schema_version"] == store.SCHEMA_VERSION
    assert meta["embedding_dim"] == "384"
    assert "embedding_model" in meta


def test_archive_meta_dim_mismatch_raises(monkeypatch):
    """An archive built at one dim refuses to re-open under a different
    configured dim, instead of silently building mismatched vec tables."""
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as f:
        db_path = Path(f.name)
    try:
        c = store.get_connection(db_path)
        store.ensure_schema(c)  # built at default 384
        # Now pretend config switched to a 1536-dim model.
        monkeypatch.setattr(store, "_get_embedding_dim", lambda: 1536)
        with pytest.raises(RuntimeError, match="dimension mismatch"):
            store.ensure_schema(c)
        c.close()
    finally:
        db_path.unlink(missing_ok=True)
