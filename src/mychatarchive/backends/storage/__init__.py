"""Storage backend protocol.

Any storage backend must implement these functions as module-level callables.
The default is 'sqlite' which uses SQLite + sqlite-vec.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterator, Optional, Protocol, runtime_checkable

if TYPE_CHECKING:
    from pathlib import Path


@runtime_checkable
class StorageBackend(Protocol):
    """Defines the interface every storage backend must satisfy."""

    def get_connection(self, db_path: Path): ...

    def ensure_schema(self, con) -> None: ...

    # Ingestion
    def insert_message(
        self, con, message_id: str, canonical_thread_id: str,
        platform: str, account_id: str, ts: str, role: str,
        text: str, title: str, source_id: str,
        source_thread_id: Optional[str] = None,
        source_message_id: Optional[str] = None,
        meta: Optional[dict] = None,
    ) -> bool: ...

    # Counts
    def message_count(self, con) -> int: ...
    def chunk_count(self, con) -> int: ...
    def thought_count(self, con) -> int: ...
    def thread_count(self, con) -> int: ...
    def platform_counts(self, con) -> list[tuple[str, int]]: ...

    # Iterators
    def iter_messages(self, con, batch_size: int = 1000) -> Iterator[dict]: ...
    def embedded_message_ids(self, con) -> set[str]: ...

    # Chunks & thoughts
    def insert_chunk(
        self, con, chunk_id: str, message_id: Optional[str],
        thread_id: str, chunk_index: int, text: str,
        ts_start: str, ts_end: str, embedding: list[float],
        meta: Optional[dict] = None,
    ) -> None: ...

    def insert_thought(
        self, con, thought_id: str, text: str, created_at: str,
        embedding: list[float], meta: Optional[dict] = None,
    ) -> None: ...

    # Search
    def search_chunks(self, con, embedding: list[float], limit: int = 10) -> list: ...
    def search_thoughts(self, con, embedding: list[float], limit: int = 10) -> list: ...
    def fts_search(self, con, query: str, limit: int = 20) -> list: ...

    # Retrieval
    def get_recent_chunks(self, con, cutoff_iso: str, limit: int = 20) -> list: ...
    def get_recent_thoughts(self, con, cutoff_iso: str, limit: int = 20) -> list: ...
    def get_chunk_by_id(self, con, chunk_id: str): ...
    def get_thought_by_id(self, con, thought_id: str): ...
