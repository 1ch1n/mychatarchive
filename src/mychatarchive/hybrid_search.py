"""Hybrid keyword/vector retrieval for agent-facing archive search."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable


@dataclass
class HybridSearchResult:
    """Normalized search result with preserved score components."""

    result_id: str
    match_key: str
    thread_id: str
    message_id: str | None
    chunk_id: str | None
    text: str
    title: str
    role: str
    timestamp: str | None
    source: list[str] = field(default_factory=list)
    keyword_rank: int | None = None
    semantic_rank: int | None = None
    keyword_score: float = 0.0
    semantic_score: float = 0.0
    exact_score: float = 0.0
    hybrid_score: float = 0.0
    distance: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "result_id": self.result_id,
            "match_key": self.match_key,
            "thread_id": self.thread_id,
            "message_id": self.message_id,
            "chunk_id": self.chunk_id,
            "title": self.title,
            "role": self.role,
            "timestamp": self.timestamp,
            "text": self.text,
            "source": self.source,
            "scores": {
                "hybrid": round(self.hybrid_score, 4),
                "keyword": round(self.keyword_score, 4),
                "semantic": round(self.semantic_score, 4),
                "exact": round(self.exact_score, 4),
                "distance": round(self.distance, 6) if self.distance is not None else None,
                "keyword_rank": self.keyword_rank,
                "semantic_rank": self.semantic_rank,
            },
        }


def _safe_lower(value: str | None) -> str:
    return (value or "").lower()


def _match_key(*, message_id: str | None, thread_id: str, chunk_id: str | None = None) -> str:
    if message_id:
        return f"message:{message_id}"
    if thread_id:
        return f"thread:{thread_id}"
    return f"chunk:{chunk_id or ''}"


def _exact_score(query: str, result: HybridSearchResult) -> float:
    query_text = query.strip().lower()
    if not query_text:
        return 0.0

    identifiers = [
        result.message_id,
        result.thread_id,
        result.chunk_id,
        result.title,
    ]
    if any(query_text == _safe_lower(identifier) for identifier in identifiers):
        return 1.0
    if any(query_text and query_text in _safe_lower(identifier) for identifier in identifiers):
        return 0.75
    if query_text in _safe_lower(result.text):
        return 0.55

    tokens = [token for token in query_text.replace("_", " ").replace("-", " ").split() if token]
    if not tokens:
        return 0.0
    identifier_blob = " ".join(_safe_lower(identifier) for identifier in identifiers if identifier)
    if any(token in identifier_blob for token in tokens if len(token) >= 4):
        return 0.35
    return 0.0


def keyword_candidates(rows: Iterable[Any], query: str) -> list[HybridSearchResult]:
    """Adapt db.fts_search rows to normalized candidates.

    Expected row shape: (message_id, text, canonical_thread_id, ts, role, title).
    """
    candidates: list[HybridSearchResult] = []
    for rank, row in enumerate(rows, 1):
        message_id = row[0]
        text = row[1] or ""
        thread_id = row[2] or ""
        result = HybridSearchResult(
            result_id=message_id or thread_id,
            match_key=_match_key(message_id=message_id, thread_id=thread_id),
            thread_id=thread_id,
            message_id=message_id,
            chunk_id=None,
            text=text,
            title=row[5] or "",
            role=row[4] or "",
            timestamp=row[3],
            source=["keyword"],
            keyword_rank=rank,
            keyword_score=1.0 / rank,
        )
        result.exact_score = _exact_score(query, result)
        candidates.append(result)
    return candidates


def semantic_candidates(
    con: Any,
    rows: Iterable[tuple[str, float]],
    query: str,
) -> list[HybridSearchResult]:
    """Adapt vector chunk rows to normalized candidates using the current schema."""
    raw_rows = list(rows)
    if not raw_rows:
        return []

    chunk_ids = [chunk_id for chunk_id, _ in raw_rows]
    placeholders = ",".join("?" for _ in chunk_ids)
    records = con.execute(
        f"""
        SELECT c.chunk_id, c.message_id, c.canonical_thread_id, c.text, c.ts_start,
               c.meta, m.role, m.title
        FROM chunks c
        LEFT JOIN messages m ON m.message_id = c.message_id
        WHERE c.chunk_id IN ({placeholders})
        """,
        chunk_ids,
    ).fetchall()
    by_chunk_id = {row[0]: row for row in records}

    candidates: list[HybridSearchResult] = []
    for rank, (chunk_id, distance) in enumerate(raw_rows, 1):
        record = by_chunk_id.get(chunk_id)
        if not record:
            continue
        semantic_score = max(0.0, 1.0 - float(distance))
        result = HybridSearchResult(
            result_id=record[1] or chunk_id,
            match_key=_match_key(
                message_id=record[1],
                thread_id=record[2] or "",
                chunk_id=chunk_id,
            ),
            thread_id=record[2] or "",
            message_id=record[1],
            chunk_id=chunk_id,
            text=record[3] or "",
            title=record[7] or "",
            role=record[6] or "",
            timestamp=record[4],
            source=["semantic"],
            semantic_rank=rank,
            semantic_score=semantic_score,
            distance=float(distance),
        )
        result.exact_score = _exact_score(query, result)
        candidates.append(result)
    return candidates


def merge_candidates(
    keyword: Iterable[HybridSearchResult],
    semantic: Iterable[HybridSearchResult],
    *,
    query: str,
    limit: int,
) -> list[HybridSearchResult]:
    """Merge FTS and semantic candidates while preserving exact/keyword priority."""
    merged: dict[str, HybridSearchResult] = {}

    for candidate in [*keyword, *semantic]:
        candidate.exact_score = max(candidate.exact_score, _exact_score(query, candidate))
        existing = merged.get(candidate.match_key)
        if existing is None:
            merged[candidate.match_key] = candidate
            continue

        if "keyword" in candidate.source and "keyword" not in existing.source:
            existing.source.append("keyword")
        if "semantic" in candidate.source and "semantic" not in existing.source:
            existing.source.append("semantic")

        existing.keyword_score = max(existing.keyword_score, candidate.keyword_score)
        existing.semantic_score = max(existing.semantic_score, candidate.semantic_score)
        existing.exact_score = max(existing.exact_score, candidate.exact_score)
        existing.keyword_rank = min(
            rank for rank in [existing.keyword_rank, candidate.keyword_rank] if rank is not None
        ) if existing.keyword_rank is not None or candidate.keyword_rank is not None else None
        existing.semantic_rank = min(
            rank for rank in [existing.semantic_rank, candidate.semantic_rank] if rank is not None
        ) if existing.semantic_rank is not None or candidate.semantic_rank is not None else None
        if candidate.distance is not None:
            existing.distance = (
                candidate.distance
                if existing.distance is None
                else min(existing.distance, candidate.distance)
            )
        if candidate.chunk_id and not existing.chunk_id:
            existing.chunk_id = candidate.chunk_id
        if len(candidate.text) > len(existing.text) and "keyword" not in existing.source:
            existing.text = candidate.text

    for result in merged.values():
        source_bonus = 0.15 if {"keyword", "semantic"}.issubset(set(result.source)) else 0.0
        result.hybrid_score = (
            result.keyword_score * 1.15
            + result.semantic_score * 0.75
            + result.exact_score * 1.35
            + source_bonus
        )

    ranked = sorted(
        merged.values(),
        key=lambda item: (
            item.hybrid_score,
            item.exact_score,
            item.keyword_score,
            item.semantic_score,
            item.timestamp or "",
        ),
        reverse=True,
    )
    return ranked[:limit]


def search_hybrid(
    con: Any,
    query: str,
    embedding: list[float],
    *,
    limit: int = 10,
    platform: str | list[str] | None = None,
    cutoff_iso: str | None = None,
    sort_by_time: bool = False,
    group_thread_ids: set[str] | None = None,
    keyword_search: Callable[..., list[Any]] | None = None,
    semantic_search: Callable[..., list[tuple[str, float]]] | None = None,
) -> list[dict[str, Any]]:
    """Run hybrid retrieval and return JSON-ready result dictionaries."""
    from mychatarchive import db

    keyword_search = keyword_search or db.fts_search
    semantic_search = semantic_search or db.search_chunks
    candidate_limit = max(limit * 4, 20)

    keyword_rows = keyword_search(
        con,
        query,
        limit=candidate_limit,
        platform=platform,
        cutoff_iso=cutoff_iso,
        sort_by_time=sort_by_time,
        group_thread_ids=group_thread_ids,
    )
    semantic_rows = semantic_search(
        con,
        embedding,
        limit=candidate_limit,
        platform=platform,
        cutoff_iso=cutoff_iso,
        sort_by_time=sort_by_time,
        group_thread_ids=group_thread_ids,
    )
    results = merge_candidates(
        keyword_candidates(keyword_rows, query),
        semantic_candidates(con, semantic_rows, query),
        query=query,
        limit=limit,
    )
    return [result.as_dict() for result in results]
