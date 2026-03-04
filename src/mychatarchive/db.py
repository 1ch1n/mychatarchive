"""Data access layer -- thin shim that delegates to the active storage backend.

All public functions maintain the exact same signatures as before the refactor.
Callers (ingest.py, cli.py, mcp/server.py) don't need to change.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from mychatarchive.backends import get_storage


def _b():
    return get_storage()


def serialize_f32(vec: list[float]) -> bytes:
    return _b().serialize_f32(vec)


def get_connection(db_path: Path):
    return _b().get_connection(db_path)


def ensure_schema(con) -> None:
    return _b().ensure_schema(con)


def insert_message(con, message_id: str, canonical_thread_id: str,
                   platform: str, account_id: str, ts: str, role: str,
                   text: str, title: str, source_id: str) -> bool:
    return _b().insert_message(
        con, message_id, canonical_thread_id, platform, account_id,
        ts, role, text, title, source_id,
    )


def message_count(con) -> int:
    return _b().message_count(con)


def chunk_count(con) -> int:
    return _b().chunk_count(con)


def thought_count(con) -> int:
    return _b().thought_count(con)


def thread_count(con) -> int:
    return _b().thread_count(con)


def platform_counts(con) -> list[tuple[str, int]]:
    return _b().platform_counts(con)


def iter_messages(con, batch_size: int = 1000):
    return _b().iter_messages(con, batch_size)


def embedded_message_ids(con) -> set[str]:
    return _b().embedded_message_ids(con)


def clear_chunks(con) -> None:
    """Delete all chunks and vectors — used by embed --force to allow clean re-embed."""
    return _b().clear_chunks(con)


def insert_chunk(con, chunk_id: str, message_id: Optional[str],
                 thread_id: str, chunk_index: int, text: str,
                 ts_start: str, ts_end: str, embedding: list[float],
                 meta: Optional[dict] = None):
    return _b().insert_chunk(
        con, chunk_id, message_id, thread_id, chunk_index,
        text, ts_start, ts_end, embedding, meta,
    )


def insert_thought(con, thought_id: str, text: str, created_at: str,
                   embedding: list[float], meta: Optional[dict] = None):
    return _b().insert_thought(con, thought_id, text, created_at, embedding, meta)


def search_chunks(
    con,
    embedding: list[float],
    limit: int = 10,
    platform: str | list[str] | None = None,
):
    return _b().search_chunks(con, embedding, limit=limit, platform=platform)


def search_thoughts(con, embedding: list[float], limit: int = 10):
    return _b().search_thoughts(con, embedding, limit)


def fts_search(
    con,
    query: str,
    limit: int = 20,
    platform: str | list[str] | None = None,
):
    return _b().fts_search(con, query, limit=limit, platform=platform)


def get_recent_chunks(
    con,
    cutoff_iso: str,
    limit: int = 20,
    platform: str | list[str] | None = None,
):
    return _b().get_recent_chunks(con, cutoff_iso, limit=limit, platform=platform)


def get_recent_thoughts(con, cutoff_iso: str, limit: int = 20):
    return _b().get_recent_thoughts(con, cutoff_iso, limit)


def get_chunk_by_id(con, chunk_id: str):
    return _b().get_chunk_by_id(con, chunk_id)


def get_thought_by_id(con, thought_id: str):
    return _b().get_thought_by_id(con, thought_id)


def export_messages(con, platform: str | None = None, limit: int | None = None):
    """Export all messages as dicts, optionally filtered by platform."""
    query = """
        SELECT message_id, canonical_thread_id, platform, account_id,
               ts, role, text, title, source_id
        FROM messages ORDER BY platform, canonical_thread_id, ts
    """
    params: list = []
    if platform:
        query = query.replace("ORDER BY", "WHERE platform = ? ORDER BY")
        params.append(platform)
    if limit:
        query += " LIMIT ?"
        params.append(limit)

    rows = con.execute(query, params).fetchall()
    return [
        {
            "message_id": r[0],
            "thread_id": r[1],
            "platform": r[2],
            "account_id": r[3],
            "timestamp": r[4],
            "role": r[5],
            "content": r[6],
            "title": r[7],
            "source_id": r[8],
        }
        for r in rows
    ]


def export_thoughts(con):
    """Export all thoughts as dicts."""
    rows = con.execute(
        "SELECT thought_id, text, created_at, meta FROM thoughts ORDER BY created_at"
    ).fetchall()
    return [
        {
            "thought_id": r[0],
            "content": r[1],
            "created_at": r[2],
            "metadata": r[3],
        }
        for r in rows
    ]
