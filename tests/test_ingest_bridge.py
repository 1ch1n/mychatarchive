"""Tests for ingest bridge compatibility and optional archive-truth propagation."""

from __future__ import annotations

from types import SimpleNamespace

import json
import sqlite3

import pytest

from mychatarchive import db, ingest, ingest_bridge


def _mock_connection():
    return SimpleNamespace(commit=lambda: None, close=lambda: None)


def test_ingest_parsed_messages_legacy_shape(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy.sqlite"
    monkeypatch.setattr(
        ingest,
        "enrich_text_with_itir",
        lambda text: {"tokenizer_profile_receipt": {"profile_id": "stub"}, "text": text},
    )
    monkeypatch.setattr(db, "get_connection", lambda _path: _mock_connection())
    monkeypatch.setattr(db, "ensure_schema", lambda _con: None)

    captured: dict[str, object] = {}

    def fake_insert_message(
        con,
        message_id,
        canonical_thread_id,
        platform,
        account_id,
        ts,
        role,
        text,
        title,
        source_id,
        source_thread_id=None,
        source_message_id=None,
        meta=None,
    ):
        captured["source_thread_id"] = source_thread_id
        captured["source_message_id"] = source_message_id
        captured["meta"] = meta
        return True

    monkeypatch.setattr(db, "insert_message", fake_insert_message)

    messages = [
        {
            "thread_id": "thread-legacy",
            "thread_title": "Legacy",
            "role": "user",
            "content": "hello legacy",
            "created_at": 1700000000.0,
        }
    ]

    inserted, duplicates = ingest.ingest_parsed_messages(
        messages,
        db_path=db_path,
        platform="chatgpt",
        account_id="main",
        source_id="source_legacy",
    )

    assert inserted == 1
    assert duplicates == 0
    assert captured["source_thread_id"] == "thread-legacy"
    assert captured["source_message_id"] is None
    meta = captured["meta"]
    assert isinstance(meta, dict)
    assert meta["itir"]["tokenizer_profile_receipt"]["profile_id"] == "stub"
    assert "archive_truth" not in meta


def test_ingest_optional_fields_bridge_to_insert_and_side_writers(tmp_path, monkeypatch):
    db_path = tmp_path / "bridge.sqlite"
    monkeypatch.setattr(
        ingest,
        "enrich_text_with_itir",
        lambda text: {"tokenizer_profile_receipt": {"profile_id": "stub"}, "text": text},
    )
    monkeypatch.setattr(db, "get_connection", lambda _path: _mock_connection())
    monkeypatch.setattr(db, "ensure_schema", lambda _con: None)

    captured: dict[str, object] = {}
    blocks_calls: list[tuple[str, object]] = []
    refs_calls: list[tuple[str, object]] = []

    def fake_insert_message(
        con,
        message_id,
        canonical_thread_id,
        platform,
        account_id,
        ts,
        role,
        text,
        title,
        source_id,
        source_thread_id=None,
        source_message_id=None,
        meta=None,
        source_path=None,
        source_bucket=None,
        provenance_json=None,
    ):
        captured["message_id"] = message_id
        captured["source_path"] = source_path
        captured["source_bucket"] = source_bucket
        captured["provenance_json"] = provenance_json
        captured["meta"] = meta
        return True

    def fake_insert_message_blocks(con, message_id, blocks):
        blocks_calls.append((message_id, blocks))

    def fake_insert_provenance_refs(con, message_id, refs):
        refs_calls.append((message_id, refs))

    monkeypatch.setattr(db, "insert_message", fake_insert_message)
    monkeypatch.setattr(db, "insert_message_blocks", fake_insert_message_blocks, raising=False)
    monkeypatch.setattr(db, "insert_provenance_refs", fake_insert_provenance_refs, raising=False)

    messages = [
        {
            "thread_id": "thread-bridge",
            "thread_title": "Bridge",
            "role": "assistant",
            "content": "hello bridge",
            "created_at": 1700000001.0,
            "source_message_id": "src-msg-1",
            "source_path": "/exports/bridge.json",
            "source_bucket": "drop_folder",
            "provenance_json": {"origin": "chat-export-structurer"},
            "content_blocks": [{"kind": "text", "text": "hello bridge"}],
            "provenance_refs": [{"type": "file", "id": "ref-1"}],
        }
    ]

    inserted, duplicates = ingest.ingest_parsed_messages(
        messages,
        db_path=db_path,
        platform="chatgpt",
        account_id="main",
        source_id="source_bridge",
    )

    assert inserted == 1
    assert duplicates == 0
    assert captured["source_path"] == "/exports/bridge.json"
    assert captured["source_bucket"] == "drop_folder"
    assert captured["provenance_json"] == {"origin": "chat-export-structurer"}
    assert isinstance(captured["meta"], dict)
    assert "archive_truth" in captured["meta"]
    assert captured["meta"]["archive_truth"]["content_blocks"] == [
        {"kind": "text", "text": "hello bridge"}
    ]
    assert captured["meta"]["archive_truth"]["provenance_refs"] == [
        {"type": "file", "id": "ref-1"}
    ]

    assert len(blocks_calls) == 1
    assert blocks_calls[0][0] == captured["message_id"]
    assert blocks_calls[0][1] == [{"kind": "text", "text": "hello bridge"}]

    assert len(refs_calls) == 1
    assert refs_calls[0][0] == captured["message_id"]
    assert refs_calls[0][1] == [{"type": "file", "id": "ref-1"}]


def test_ingest_persists_predicate_projection_when_available(tmp_path, monkeypatch):
    db_path = tmp_path / "predicate.sqlite"
    monkeypatch.setattr(
        ingest,
        "enrich_text_with_itir",
        lambda text: {
            "tokenizer_profile_receipt": {"profile_id": "stub"},
            "predicate_projection": {
                "version": "itir_predicate_projection_v1",
                "predicates": [{"ref": "pred:1", "predicate": "pay", "roles": []}],
            },
        },
    )
    monkeypatch.setattr(db, "get_connection", lambda _path: _mock_connection())
    monkeypatch.setattr(db, "ensure_schema", lambda _con: None)
    monkeypatch.setattr(db, "insert_message", lambda *args, **kwargs: True)

    projection_calls: list[tuple[str, str, object]] = []

    def fake_insert_predicate_projection(con, message_id, canonical_thread_id, projection):
        projection_calls.append((message_id, canonical_thread_id, projection))
        return 1

    monkeypatch.setattr(db, "insert_predicate_projection", fake_insert_predicate_projection, raising=False)

    messages = [
        {
            "thread_id": "thread-predicate",
            "thread_title": "Predicate",
            "role": "assistant",
            "content": "tenant must pay rent",
            "created_at": 1700000002.0,
        }
    ]

    inserted, duplicates = ingest.ingest_parsed_messages(
        messages,
        db_path=db_path,
        platform="chatgpt",
        account_id="main",
        source_id="source_predicate",
    )

    assert inserted == 1
    assert duplicates == 0
    assert len(projection_calls) == 1
    assert projection_calls[0][2]["version"] == "itir_predicate_projection_v1"


def _create_canonical_archive(path):
    con = sqlite3.connect(path)
    con.execute(
        """
        CREATE TABLE messages (
            message_id TEXT PRIMARY KEY,
            canonical_thread_id TEXT NOT NULL,
            platform TEXT,
            account_id TEXT,
            ts TEXT NOT NULL,
            role TEXT NOT NULL,
            text TEXT NOT NULL,
            title TEXT,
            source_id TEXT,
            source_thread_id TEXT,
            source_message_id TEXT,
            source_path TEXT,
            source_bucket TEXT,
            provenance_json TEXT,
            meta TEXT
        )
        """
    )
    con.executemany(
        """
        INSERT INTO messages (
            message_id, canonical_thread_id, platform, account_id, ts, role, text,
            title, source_id, source_thread_id, source_message_id, source_path,
            source_bucket, provenance_json, meta
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        [
            (
                "canon-msg-1",
                "canon-thread-1",
                "chatgpt",
                "acct-a",
                "2026-01-01T00:00:00+00:00",
                "user",
                "Canonical bridge question",
                "Bridge Thread",
                "structurer-src",
                "online-thread-1",
                "online-msg-1",
                "/exports/chatgpt.json",
                "canonical_archive",
                json.dumps({"origin": "structurer"}),
                json.dumps({"existing": {"key": "value"}}),
            ),
            (
                "canon-msg-2",
                "canon-thread-1",
                "chatgpt",
                "acct-a",
                "2026-01-01T00:01:00+00:00",
                "assistant",
                "Canonical bridge answer",
                "Bridge Thread",
                "structurer-src",
                "online-thread-1",
                "online-msg-2",
                "/exports/chatgpt.json",
                "canonical_archive",
                None,
                None,
            ),
        ],
    )
    con.commit()
    con.close()


def test_import_canonical_archive_is_idempotent_and_preserves_provenance(tmp_path):
    canonical_path = tmp_path / "canonical.sqlite"
    mca_path = tmp_path / "mca.sqlite"
    _create_canonical_archive(canonical_path)

    first = ingest_bridge.import_canonical_archive(canonical_path, mca_path)
    second = ingest_bridge.import_canonical_archive(canonical_path, mca_path)

    assert first.as_dict()["inserted"] == 2
    assert first.duplicates == 0
    assert first.skipped == 0
    assert second.inserted == 0
    assert second.duplicates == 2

    con = db.get_connection(mca_path)
    try:
        rows = db.export_messages(con)
    finally:
        con.close()

    assert [row["message_id"] for row in rows] == ["canon-msg-1", "canon-msg-2"]
    first_row = rows[0]
    assert first_row["thread_id"] == "canon-thread-1"
    assert first_row["source_thread_id"] == "online-thread-1"
    assert first_row["source_message_id"] == "online-msg-1"
    assert first_row["source_path"] == "/exports/chatgpt.json"
    assert first_row["source_bucket"] == "canonical_archive"
    assert first_row["provenance_json"]["origin"] == "structurer"
    assert first_row["provenance_json"]["bridge"]["kind"] == "canonical_archive_to_mychatarchive"
    assert first_row["meta"]["existing"]["key"] == "value"
    assert first_row["meta"]["canonical_bridge"]["canonical_message_id"] == "canon-msg-1"


def test_import_canonical_archive_accepts_legacy_alias_schema(tmp_path):
    canonical_path = tmp_path / "legacy_canonical.sqlite"
    mca_path = tmp_path / "mca.sqlite"
    con = sqlite3.connect(canonical_path)
    con.execute(
        """
        CREATE TABLE messages (
            id TEXT PRIMARY KEY,
            thread_hash TEXT NOT NULL,
            conversation_id TEXT,
            created_at REAL NOT NULL,
            speaker TEXT NOT NULL,
            content TEXT NOT NULL,
            thread_title TEXT
        )
        """
    )
    con.execute(
        """
        INSERT INTO messages (
            id, thread_hash, conversation_id, created_at, speaker, content, thread_title
        ) VALUES (?,?,?,?,?,?,?)
        """,
        (
            "legacy-msg-1",
            "legacy-thread-1",
            "online-legacy-thread",
            1767225600.0,
            "user",
            "Legacy canonical content",
            "Legacy Bridge",
        ),
    )
    con.commit()
    con.close()

    result = ingest_bridge.import_canonical_archive(
        canonical_path,
        mca_path,
        default_platform="perplexity",
        source_id="legacy-source",
    )

    assert result.inserted == 1
    con = db.get_connection(mca_path)
    try:
        row = db.export_messages(con)[0]
    finally:
        con.close()

    assert row["message_id"] == "legacy-msg-1"
    assert row["thread_id"] == "legacy-thread-1"
    assert row["platform"] == "perplexity"
    assert row["source_thread_id"] == "online-legacy-thread"
    assert row["source_message_id"] == "legacy-msg-1"
    assert row["source_id"] == "legacy-source"


def test_import_canonical_archive_requires_messages_table(tmp_path):
    canonical_path = tmp_path / "not_canonical.sqlite"
    mca_path = tmp_path / "mca.sqlite"
    con = sqlite3.connect(canonical_path)
    con.execute("CREATE TABLE other (id TEXT)")
    con.commit()
    con.close()

    with pytest.raises(ValueError, match="messages table"):
        ingest_bridge.import_canonical_archive(canonical_path, mca_path)
