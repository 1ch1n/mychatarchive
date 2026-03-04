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
    cutoff_iso: str | None = None,
    sort_by_time: bool = False,
    group_thread_ids: set | None = None,
):
    return _b().search_chunks(
        con, embedding, limit=limit, platform=platform,
        cutoff_iso=cutoff_iso, sort_by_time=sort_by_time,
        group_thread_ids=group_thread_ids,
    )


def search_thoughts(con, embedding: list[float], limit: int = 10):
    return _b().search_thoughts(con, embedding, limit)


def fts_search(
    con,
    query: str,
    limit: int = 20,
    platform: str | list[str] | None = None,
    cutoff_iso: str | None = None,
    sort_by_time: bool = False,
    group_thread_ids: set | None = None,
):
    return _b().fts_search(
        con, query, limit=limit, platform=platform,
        cutoff_iso=cutoff_iso, sort_by_time=sort_by_time,
        group_thread_ids=group_thread_ids,
    )


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
    return _b().export_messages(con, platform=platform, limit=limit)


def export_thoughts(con):
    return _b().export_thoughts(con)


# ── Thread summaries ──────────────────────────────────────────────────────────

def iter_threads(con):
    return _b().iter_threads(con)


def get_thread_messages(con, canonical_thread_id: str) -> list[dict]:
    return _b().get_thread_messages(con, canonical_thread_id)


def has_thread_summary(con, canonical_thread_id: str) -> bool:
    return _b().has_thread_summary(con, canonical_thread_id)


def insert_thread_summary(con, canonical_thread_id: str, title, platform,
                          message_count: int, ts_start, ts_end,
                          summary: str, key_topics: list, summary_model: str, now: str):
    return _b().insert_thread_summary(
        con, canonical_thread_id, title, platform, message_count,
        ts_start, ts_end, summary, key_topics, summary_model, now,
    )


def insert_thread_summary_embedding(con, canonical_thread_id: str, embedding: list[float]):
    return _b().insert_thread_summary_embedding(con, canonical_thread_id, embedding)


def get_thread_summary(con, canonical_thread_id: str):
    return _b().get_thread_summary(con, canonical_thread_id)


def list_thread_summaries(con, limit: int = 100, platform=None, since_iso=None):
    return _b().list_thread_summaries(con, limit=limit, platform=platform, since_iso=since_iso)


def search_thread_summaries(con, embedding: list[float], limit: int = 10):
    return _b().search_thread_summaries(con, embedding, limit)


def summary_count(con) -> int:
    return _b().summary_count(con)


def unsummarized_thread_count(con) -> int:
    return _b().unsummarized_thread_count(con)


# ── Thread groups ─────────────────────────────────────────────────────────────

def create_group(con, group_id: str, name: str, description, now: str) -> bool:
    return _b().create_group(con, group_id, name, description, now)


def list_groups(con) -> list:
    return _b().list_groups(con)


def get_group_by_name(con, name: str):
    return _b().get_group_by_name(con, name)


def add_to_group(con, canonical_thread_id: str, group_id: str, now: str) -> bool:
    return _b().add_to_group(con, canonical_thread_id, group_id, now)


def remove_from_group(con, canonical_thread_id: str, group_id: str) -> bool:
    return _b().remove_from_group(con, canonical_thread_id, group_id)


def delete_group(con, group_id: str) -> bool:
    return _b().delete_group(con, group_id)


def get_threads_in_group(con, group_id: str) -> list[dict]:
    return _b().get_threads_in_group(con, group_id)


def get_group_thread_ids(con, group_id: str) -> set:
    return _b().get_group_thread_ids(con, group_id)


def group_count(con) -> int:
    return _b().group_count(con)
