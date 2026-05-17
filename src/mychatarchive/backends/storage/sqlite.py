"""SQLite + sqlite-vec storage backend (default).

All data lives in a single .sqlite file with FTS5 and vector search via sqlite-vec.
"""

import json
import re
import sqlite3
import struct
from pathlib import Path
from typing import Any, Mapping, Optional

import sqlite_vec


def _get_embedding_dim() -> int:
    from mychatarchive.config import get_embedding_dim
    return get_embedding_dim()


def serialize_f32(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def get_connection(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(db_path))
    con.enable_load_extension(True)
    sqlite_vec.load(con)
    con.enable_load_extension(False)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA temp_store=MEMORY")
    return con


def _ensure_thread_summaries_v2(con: sqlite3.Connection, dim: int) -> None:
    """Create or migrate thread_summaries to the multi-segment schema.

    Old schema: canonical_thread_id TEXT PRIMARY KEY  (one row per thread)
    New schema: summary_id TEXT PRIMARY KEY           (one row per segment)

    summary_id format: "{canonical_thread_id}::{segment_index:04d}"

    Migration copies old rows as segment 0 of each thread. Embeddings are
    dropped and must be regenerated with 'mychatarchive summarize'.
    """
    cols = {row[1] for row in con.execute("PRAGMA table_info(thread_summaries)").fetchall()}

    if "summary_id" not in cols:
        if cols:
            # Old single-segment schema — migrate data, keep summary text
            con.executescript("""
                CREATE TABLE thread_summaries_new (
                    summary_id TEXT PRIMARY KEY,
                    canonical_thread_id TEXT NOT NULL,
                    segment_index INTEGER NOT NULL DEFAULT 0,
                    title TEXT,
                    platform TEXT,
                    message_count INTEGER,
                    segment_chars INTEGER,
                    ts_start TEXT,
                    ts_end TEXT,
                    summary TEXT NOT NULL,
                    key_topics TEXT,
                    summary_model TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                INSERT INTO thread_summaries_new
                    (summary_id, canonical_thread_id, segment_index, title, platform,
                     message_count, segment_chars, ts_start, ts_end, summary, key_topics,
                     summary_model, created_at, updated_at)
                SELECT
                    canonical_thread_id || '::0000',
                    canonical_thread_id, 0, title, platform,
                    message_count, NULL, ts_start, ts_end, summary, key_topics,
                    summary_model, created_at, updated_at
                FROM thread_summaries;
                DROP TABLE thread_summaries;
                ALTER TABLE thread_summaries_new RENAME TO thread_summaries;
            """)
        else:
            # Fresh install — create new schema directly
            con.execute("""
                CREATE TABLE thread_summaries (
                    summary_id TEXT PRIMARY KEY,
                    canonical_thread_id TEXT NOT NULL,
                    segment_index INTEGER NOT NULL DEFAULT 0,
                    title TEXT,
                    platform TEXT,
                    message_count INTEGER,
                    segment_chars INTEGER,
                    ts_start TEXT,
                    ts_end TEXT,
                    summary TEXT NOT NULL,
                    key_topics TEXT,
                    summary_model TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
        # Drop old vec table (wrong PK: canonical_thread_id) and recreate with summary_id
        con.execute("DROP TABLE IF EXISTS vec_thread_summaries")
        con.execute(f"""
            CREATE VIRTUAL TABLE vec_thread_summaries
            USING vec0(summary_id TEXT PRIMARY KEY, embedding float[{dim}] distance_metric=cosine)
        """)
        con.commit()

    # Idempotent: ensure index and vec exist for already-migrated DBs
    con.execute("""
        CREATE INDEX IF NOT EXISTS idx_thread_summaries_thread
        ON thread_summaries(canonical_thread_id)
    """)
    con.execute(f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS vec_thread_summaries
        USING vec0(summary_id TEXT PRIMARY KEY, embedding float[{dim}] distance_metric=cosine)
    """)
    con.commit()


def _ensure_message_meta_column(con: sqlite3.Connection) -> None:
    cols = {row[1] for row in con.execute("PRAGMA table_info(messages)").fetchall()}
    if cols and "meta" not in cols:
        con.execute("ALTER TABLE messages ADD COLUMN meta TEXT")
        con.commit()


def _ensure_message_provenance_columns(con: sqlite3.Connection) -> None:
    cols = {row[1] for row in con.execute("PRAGMA table_info(messages)").fetchall()}
    if cols and "source_thread_id" not in cols:
        con.execute("ALTER TABLE messages ADD COLUMN source_thread_id TEXT")
    if cols and "source_message_id" not in cols:
        con.execute("ALTER TABLE messages ADD COLUMN source_message_id TEXT")
    if cols and "source_path" not in cols:
        con.execute("ALTER TABLE messages ADD COLUMN source_path TEXT")
    if cols and "source_bucket" not in cols:
        con.execute("ALTER TABLE messages ADD COLUMN source_bucket TEXT")
    if cols and "provenance_json" not in cols:
        con.execute("ALTER TABLE messages ADD COLUMN provenance_json TEXT")
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_messages_source_thread_id ON messages(source_thread_id)"
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_messages_source_bucket ON messages(source_bucket)")
    con.commit()


def ensure_schema(con: sqlite3.Connection):
    """Create all tables (ingestion + brain). Idempotent."""
    dim = _get_embedding_dim()
    cur = con.cursor()

    # thread_summaries is handled separately below (needs migration logic).
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS messages (
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
            source_path TEXT,
            source_bucket TEXT,
            provenance_json TEXT,
            meta TEXT
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts
            USING fts5(text, content='');

        CREATE TABLE IF NOT EXISTS messages_fts_docids (
            rowid INTEGER PRIMARY KEY,
            message_id TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS chunks (
            chunk_id TEXT PRIMARY KEY,
            message_id TEXT,
            canonical_thread_id TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            text TEXT NOT NULL,
            ts_start TEXT,
            ts_end TEXT,
            meta TEXT
        );

        CREATE TABLE IF NOT EXISTS message_blocks (
            block_id TEXT PRIMARY KEY,
            message_id TEXT NOT NULL,
            canonical_thread_id TEXT NOT NULL,
            block_index INTEGER NOT NULL,
            block_type TEXT NOT NULL,
            text TEXT NOT NULL,
            source_path TEXT,
            source_bucket TEXT,
            provenance_json TEXT,
            meta TEXT
        );

        CREATE TABLE IF NOT EXISTS provenance_refs (
            provenance_ref_id TEXT PRIMARY KEY,
            message_id TEXT,
            block_id TEXT,
            ref_index INTEGER NOT NULL,
            source_id TEXT,
            source_thread_id TEXT,
            source_message_id TEXT,
            source_path TEXT,
            source_bucket TEXT,
            locator_json TEXT,
            meta TEXT
        );

        CREATE TABLE IF NOT EXISTS predicate_refs (
            predicate_ref_id TEXT PRIMARY KEY,
            message_id TEXT NOT NULL,
            canonical_thread_id TEXT NOT NULL,
            predicate_ref TEXT NOT NULL,
            atom_id TEXT,
            predicate TEXT NOT NULL,
            structural_signature TEXT,
            polarity TEXT,
            modality TEXT,
            domain TEXT,
            wrapper_status TEXT,
            evidence_only INTEGER NOT NULL DEFAULT 1,
            source_spans_json TEXT,
            provenance_refs_json TEXT,
            meta TEXT
        );

        CREATE TABLE IF NOT EXISTS predicate_roles (
            predicate_role_id TEXT PRIMARY KEY,
            predicate_ref_id TEXT NOT NULL,
            message_id TEXT NOT NULL,
            role_name TEXT NOT NULL,
            role_value TEXT NOT NULL,
            role_status TEXT,
            entity_type TEXT,
            cardinality TEXT,
            members_json TEXT,
            provenance_refs_json TEXT,
            meta TEXT
        );

        CREATE TABLE IF NOT EXISTS thoughts (
            thought_id TEXT PRIMARY KEY,
            text TEXT NOT NULL,
            created_at TEXT NOT NULL,
            meta TEXT
        );

        -- User-curated thread groups (e.g. "jarvis", "coding", "projects").
        CREATE TABLE IF NOT EXISTS thread_groups (
            group_id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            description TEXT,
            created_at TEXT NOT NULL
        );

        -- Many-to-many: threads belong to one or more groups.
        CREATE TABLE IF NOT EXISTS thread_group_members (
            canonical_thread_id TEXT NOT NULL,
            group_id TEXT NOT NULL,
            added_at TEXT NOT NULL,
            PRIMARY KEY (canonical_thread_id, group_id)
        );
    """)
    cur.executescript("""
        CREATE INDEX IF NOT EXISTS idx_message_blocks_message_id
        ON message_blocks(message_id);
        CREATE INDEX IF NOT EXISTS idx_chunks_message_id
        ON chunks(message_id);
        CREATE INDEX IF NOT EXISTS idx_chunks_thread_id
        ON chunks(canonical_thread_id);
        CREATE INDEX IF NOT EXISTS idx_message_blocks_thread_id
        ON message_blocks(canonical_thread_id);
        CREATE INDEX IF NOT EXISTS idx_provenance_refs_message_id
        ON provenance_refs(message_id);
        CREATE INDEX IF NOT EXISTS idx_provenance_refs_block_id
        ON provenance_refs(block_id);
        CREATE INDEX IF NOT EXISTS idx_predicate_refs_message_id
        ON predicate_refs(message_id);
        CREATE INDEX IF NOT EXISTS idx_predicate_refs_signature
        ON predicate_refs(structural_signature);
        CREATE INDEX IF NOT EXISTS idx_predicate_refs_message_signature
        ON predicate_refs(message_id, structural_signature);
        CREATE INDEX IF NOT EXISTS idx_predicate_roles_predicate_ref_id
        ON predicate_roles(predicate_ref_id);
        CREATE INDEX IF NOT EXISTS idx_predicate_roles_message_id
        ON predicate_roles(message_id);
        CREATE INDEX IF NOT EXISTS idx_predicate_roles_lookup
        ON predicate_roles(role_name, role_value, message_id);
    """)

    cur.execute(f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks
        USING vec0(chunk_id TEXT PRIMARY KEY, embedding float[{dim}] distance_metric=cosine)
    """)

    cur.execute(f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS vec_thoughts
        USING vec0(thought_id TEXT PRIMARY KEY, embedding float[{dim}] distance_metric=cosine)
    """)

    con.commit()
    _ensure_message_meta_column(con)
    _ensure_message_provenance_columns(con)

    # Thread summaries: create or migrate to multi-segment schema
    _ensure_thread_summaries_v2(con, dim)


# --- Ingestion ---

def insert_message(con: sqlite3.Connection, message_id: str, canonical_thread_id: str,
                   platform: str, account_id: str, ts: str, role: str, text: str,
                   title: str, source_id: str,
                   source_thread_id: Optional[str] = None,
                   source_message_id: Optional[str] = None,
                   meta: Optional[dict] = None,
                   source_path: Optional[str] = None,
                   source_bucket: Optional[str] = None,
                   provenance_json: Optional[dict] = None) -> bool:
    """Insert a message. Returns True if inserted, False if duplicate."""
    cur = con.execute(
        "INSERT OR IGNORE INTO messages "
        "("
        "message_id, canonical_thread_id, platform, account_id, ts, role, text, title, source_id, "
        "source_thread_id, source_message_id, source_path, source_bucket, provenance_json, meta"
        ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            message_id,
            canonical_thread_id,
            platform,
            account_id,
            ts,
            role,
            text,
            title,
            source_id,
            source_thread_id,
            source_message_id,
            source_path,
            source_bucket,
            json.dumps(provenance_json) if provenance_json is not None else None,
            json.dumps(meta) if meta else None,
        ),
    )
    if cur.rowcount == 0:
        return False
    fts_cur = con.execute("INSERT INTO messages_fts (text) VALUES (?)", (text,))
    fts_rowid = fts_cur.lastrowid
    con.execute(
        "INSERT INTO messages_fts_docids (rowid, message_id) VALUES (?,?)",
        (fts_rowid, message_id),
    )
    return True


# --- Counts ---

def message_count(con: sqlite3.Connection) -> int:
    return con.execute("SELECT count(*) FROM messages").fetchone()[0]


def chunk_count(con: sqlite3.Connection) -> int:
    try:
        return con.execute("SELECT count(*) FROM chunks").fetchone()[0]
    except sqlite3.OperationalError:
        return 0


def thought_count(con: sqlite3.Connection) -> int:
    try:
        return con.execute("SELECT count(*) FROM thoughts").fetchone()[0]
    except sqlite3.OperationalError:
        return 0


def thread_count(con: sqlite3.Connection) -> int:
    return con.execute("SELECT count(DISTINCT canonical_thread_id) FROM messages").fetchone()[0]


def platform_counts(con: sqlite3.Connection) -> list[tuple[str, int]]:
    return con.execute(
        "SELECT platform, count(*) FROM messages GROUP BY platform ORDER BY count(*) DESC"
    ).fetchall()


# --- Iterators ---

def iter_messages(con: sqlite3.Connection, batch_size: int = 1000):
    cur = con.cursor()
    cur.execute("""
        SELECT message_id, canonical_thread_id, ts, role, text, title,
               source_thread_id, source_message_id, source_path, source_bucket, provenance_json, meta
        FROM messages ORDER BY canonical_thread_id, ts
    """)
    while True:
        rows = cur.fetchmany(batch_size)
        if not rows:
            break
        for row in rows:
            yield {
                "message_id": row[0],
                "canonical_thread_id": row[1],
                "ts": row[2],
                "role": row[3],
                "text": row[4],
                "title": row[5],
                "source_thread_id": row[6],
                "source_message_id": row[7],
                "source_path": row[8],
                "source_bucket": row[9],
                "provenance_json": json.loads(row[10]) if row[10] else None,
                "meta": json.loads(row[11]) if row[11] else None,
            }


def embedded_message_ids(con: sqlite3.Connection) -> set[str]:
    try:
        return {
            row[0]
            for row in con.execute(
                "SELECT message_id FROM chunks WHERE message_id IS NOT NULL"
            ).fetchall()
        }
    except sqlite3.OperationalError:
        return set()


# --- Vector operations ---

def clear_chunks(con: sqlite3.Connection) -> None:
    """Delete all chunks and their vectors. Used by embed --force."""
    con.execute("DELETE FROM vec_chunks")
    con.execute("DELETE FROM chunks")
    con.commit()


def insert_chunk(con: sqlite3.Connection, chunk_id: str, message_id: Optional[str],
                 thread_id: str, chunk_index: int, text: str,
                 ts_start: str, ts_end: str, embedding: list[float],
                 meta: Optional[dict] = None):
    con.execute(
        "INSERT OR IGNORE INTO chunks "
        "(chunk_id, message_id, canonical_thread_id, chunk_index, text, ts_start, ts_end, meta) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (chunk_id, message_id, thread_id, chunk_index, text, ts_start, ts_end,
         json.dumps(meta) if meta else None),
    )
    con.execute(
        "INSERT OR IGNORE INTO vec_chunks (chunk_id, embedding) VALUES (?, ?)",
        (chunk_id, serialize_f32(embedding)),
    )


def insert_thought(con: sqlite3.Connection, thought_id: str, text: str,
                   created_at: str, embedding: list[float], meta: Optional[dict] = None):
    con.execute(
        "INSERT OR IGNORE INTO thoughts (thought_id, text, created_at, meta) VALUES (?,?,?,?)",
        (thought_id, text, created_at, json.dumps(meta) if meta else None),
    )
    con.execute(
        "INSERT OR IGNORE INTO vec_thoughts (thought_id, embedding) VALUES (?, ?)",
        (thought_id, serialize_f32(embedding)),
    )


def search_chunks(
    con: sqlite3.Connection,
    embedding: list[float],
    limit: int = 10,
    platform: str | list[str] | None = None,
    cutoff_iso: str | None = None,
    sort_by_time: bool = False,
    group_thread_ids: set[str] | None = None,
):
    # An explicit empty set means "filter to zero threads" → nothing to return.
    # Without this, bool(set()) is False, needs_filter ignores it, and we'd
    # silently return global results instead of empty — wrong semantics.
    if group_thread_ids is not None and not group_thread_ids:
        return []

    needs_filter = bool(platform or cutoff_iso or group_thread_ids)
    # When scoping to a small set of threads, the target chunks are unlikely to appear
    # in the global top (limit * 5) results across 90k+ chunks. Use a much larger
    # candidate pool so filtering actually finds matching chunks.
    if group_thread_ids and len(group_thread_ids) <= 3:
        fetch_limit = max(limit * 15, 100)
    elif needs_filter:
        fetch_limit = limit * 5
    else:
        fetch_limit = limit
    raw = con.execute(
        "SELECT chunk_id, distance FROM vec_chunks "
        "WHERE embedding MATCH ? AND k = ?",
        (serialize_f32(embedding), fetch_limit),
    ).fetchall()

    if not needs_filter and not sort_by_time:
        return raw[:limit]

    chunk_ids = [r[0] for r in raw]
    if not chunk_ids:
        return []

    conditions = [f"c.chunk_id IN ({','.join('?' * len(chunk_ids))})"]
    params: list = list(chunk_ids)

    need_message_join = bool(platform)

    if platform:
        platforms = [platform] if isinstance(platform, str) else platform
        placeholders = ",".join("?" * len(platforms))
        conditions.append(f"m.platform IN ({placeholders})")
        params.extend(platforms)

    if cutoff_iso:
        conditions.append("c.ts_start >= ?")
        params.append(cutoff_iso)

    if group_thread_ids:
        placeholders = ",".join("?" * len(group_thread_ids))
        conditions.append(f"c.canonical_thread_id IN ({placeholders})")
        params.extend(group_thread_ids)

    join_clause = " JOIN messages m ON c.message_id = m.message_id" if need_message_join else ""
    where_sql = " AND ".join(conditions)

    matching_rows = con.execute(
        f"SELECT c.chunk_id, c.ts_start FROM chunks c {join_clause} WHERE {where_sql}",
        params,
    ).fetchall()

    raw_by_id = {c: d for c, d in raw}
    matched = [(r[0], r[1], raw_by_id.get(r[0], 0)) for r in matching_rows]

    if sort_by_time:
        matched.sort(key=lambda x: x[1] or "", reverse=True)

    return [(c, d) for c, ts, d in matched[:limit]]


def search_thoughts(con: sqlite3.Connection, embedding: list[float], limit: int = 10):
    return con.execute(
        "SELECT thought_id, distance FROM vec_thoughts "
        "WHERE embedding MATCH ? AND k = ?",
        (serialize_f32(embedding), limit),
    ).fetchall()


def _sanitize_fts_query(query: str) -> str:
    """Sanitize query for FTS5 to avoid syntax errors (e.g. apostrophes in 'i've')."""
    if not query or not isinstance(query, str):
        return query or ""
    # Remove apostrophes - FTS5 treats them as special and raises syntax errors
    return re.sub(r"'", "", query.strip())


def fts_search(
    con: sqlite3.Connection,
    query: str,
    limit: int = 20,
    platform: str | list[str] | None = None,
    cutoff_iso: str | None = None,
    sort_by_time: bool = False,
    group_thread_ids: set[str] | None = None,
):
    """Full-text search via FTS5."""
    query = _sanitize_fts_query(query)
    if not query:
        return []
    sql = """
        SELECT d.message_id, m.text, m.canonical_thread_id, m.ts, m.role, m.title
        FROM messages_fts f
        JOIN messages_fts_docids d ON f.rowid = d.rowid
        JOIN messages m ON m.message_id = d.message_id
        WHERE messages_fts MATCH ?
    """
    params: list = [query]
    if platform:
        platforms = [platform] if isinstance(platform, str) else platform
        placeholders = ",".join("?" * len(platforms))
        sql += f" AND m.platform IN ({placeholders})"
        params.extend(platforms)
    if cutoff_iso:
        sql += " AND m.ts >= ?"
        params.append(cutoff_iso)
    if group_thread_ids:
        placeholders = ",".join("?" * len(group_thread_ids))
        sql += f" AND m.canonical_thread_id IN ({placeholders})"
        params.extend(group_thread_ids)
    if sort_by_time:
        sql += " ORDER BY m.ts DESC"
    sql += " LIMIT ?"
    params.append(limit)
    return con.execute(sql, params).fetchall()


def get_recent_chunks(
    con: sqlite3.Connection,
    cutoff_iso: str,
    limit: int = 20,
    platform: str | list[str] | None = None,
):
    if not platform:
        return con.execute(
            "SELECT chunk_id, text, canonical_thread_id, ts_start, meta "
            "FROM chunks WHERE ts_start >= ? ORDER BY ts_start DESC LIMIT ?",
            (cutoff_iso, limit),
        ).fetchall()

    platforms = [platform] if isinstance(platform, str) else platform
    placeholders = ",".join("?" * len(platforms))
    return con.execute(
        f"""
        SELECT c.chunk_id, c.text, c.canonical_thread_id, c.ts_start, c.meta
        FROM chunks c
        JOIN messages m ON c.message_id = m.message_id
        WHERE c.ts_start >= ? AND m.platform IN ({placeholders})
        ORDER BY c.ts_start DESC LIMIT ?
        """,
        (cutoff_iso, *platforms, limit),
    ).fetchall()


def get_recent_thoughts(con: sqlite3.Connection, cutoff_iso: str, limit: int = 20):
    return con.execute(
        "SELECT thought_id, text, created_at, meta "
        "FROM thoughts WHERE created_at >= ? ORDER BY created_at DESC LIMIT ?",
        (cutoff_iso, limit),
    ).fetchall()


def get_chunk_by_id(con: sqlite3.Connection, chunk_id: str):
    return con.execute(
        "SELECT text, canonical_thread_id, ts_start, ts_end, meta FROM chunks WHERE chunk_id = ?",
        (chunk_id,),
    ).fetchone()


def get_thought_by_id(con: sqlite3.Connection, thought_id: str):
    return con.execute(
        "SELECT text, created_at, meta FROM thoughts WHERE thought_id = ?",
        (thought_id,),
    ).fetchone()


# ── Export helpers ────────────────────────────────────────────────────────────

def export_messages(con: sqlite3.Connection, platform: Optional[str] = None,
                    limit: Optional[int] = None):
    query = """
        SELECT message_id, canonical_thread_id, platform, account_id,
               ts, role, text, title, source_id, source_thread_id, source_message_id,
               source_path, source_bucket, provenance_json, meta
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
        {"message_id": r[0], "thread_id": r[1], "platform": r[2], "account_id": r[3],
         "timestamp": r[4], "role": r[5], "content": r[6], "title": r[7], "source_id": r[8],
         "source_thread_id": r[9], "source_message_id": r[10], "source_path": r[11],
         "source_bucket": r[12], "provenance_json": json.loads(r[13]) if r[13] else None,
         "meta": json.loads(r[14]) if r[14] else None}
        for r in rows
    ]


def insert_message_block(
    con: sqlite3.Connection,
    block_id: str,
    message_id: str,
    canonical_thread_id: str,
    block_index: int,
    block_type: str,
    text: str,
    source_path: Optional[str] = None,
    source_bucket: Optional[str] = None,
    provenance_json: Optional[dict] = None,
    meta: Optional[dict] = None,
) -> bool:
    """Insert a structured block for a message. Returns False if duplicate block_id."""
    cur = con.execute(
        """
        INSERT OR IGNORE INTO message_blocks
            (block_id, message_id, canonical_thread_id, block_index, block_type, text,
             source_path, source_bucket, provenance_json, meta)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (
            block_id,
            message_id,
            canonical_thread_id,
            block_index,
            block_type,
            text,
            source_path,
            source_bucket,
            json.dumps(provenance_json) if provenance_json is not None else None,
            json.dumps(meta) if meta is not None else None,
        ),
    )
    return cur.rowcount > 0


def list_message_blocks(
    con: sqlite3.Connection,
    message_id: Optional[str] = None,
    canonical_thread_id: Optional[str] = None,
) -> list[dict]:
    sql = """
        SELECT block_id, message_id, canonical_thread_id, block_index, block_type, text,
               source_path, source_bucket, provenance_json, meta
        FROM message_blocks
    """
    conditions: list[str] = []
    params: list = []
    if message_id is not None:
        conditions.append("message_id = ?")
        params.append(message_id)
    if canonical_thread_id is not None:
        conditions.append("canonical_thread_id = ?")
        params.append(canonical_thread_id)
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY message_id, block_index"
    rows = con.execute(sql, params).fetchall()
    return [
        {
            "block_id": row[0],
            "message_id": row[1],
            "canonical_thread_id": row[2],
            "block_index": row[3],
            "block_type": row[4],
            "text": row[5],
            "source_path": row[6],
            "source_bucket": row[7],
            "provenance_json": json.loads(row[8]) if row[8] else None,
            "meta": json.loads(row[9]) if row[9] else None,
        }
        for row in rows
    ]


def insert_provenance_ref(
    con: sqlite3.Connection,
    provenance_ref_id: str,
    message_id: Optional[str],
    block_id: Optional[str],
    ref_index: int,
    source_id: Optional[str] = None,
    source_thread_id: Optional[str] = None,
    source_message_id: Optional[str] = None,
    source_path: Optional[str] = None,
    source_bucket: Optional[str] = None,
    locator_json: Optional[dict] = None,
    meta: Optional[dict] = None,
) -> bool:
    """Insert a provenance reference row. Returns False if duplicate provenance_ref_id."""
    cur = con.execute(
        """
        INSERT OR IGNORE INTO provenance_refs
            (provenance_ref_id, message_id, block_id, ref_index, source_id,
             source_thread_id, source_message_id, source_path, source_bucket,
             locator_json, meta)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            provenance_ref_id,
            message_id,
            block_id,
            ref_index,
            source_id,
            source_thread_id,
            source_message_id,
            source_path,
            source_bucket,
            json.dumps(locator_json) if locator_json is not None else None,
            json.dumps(meta) if meta is not None else None,
        ),
    )
    return cur.rowcount > 0


def list_provenance_refs(
    con: sqlite3.Connection,
    message_id: Optional[str] = None,
    block_id: Optional[str] = None,
) -> list[dict]:
    sql = """
        SELECT provenance_ref_id, message_id, block_id, ref_index, source_id,
               source_thread_id, source_message_id, source_path, source_bucket,
               locator_json, meta
        FROM provenance_refs
    """
    conditions: list[str] = []
    params: list = []
    if message_id is not None:
        conditions.append("message_id = ?")
        params.append(message_id)
    if block_id is not None:
        conditions.append("block_id = ?")
        params.append(block_id)
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY message_id, block_id, ref_index"
    rows = con.execute(sql, params).fetchall()
    return [
        {
            "provenance_ref_id": row[0],
            "message_id": row[1],
            "block_id": row[2],
            "ref_index": row[3],
            "source_id": row[4],
            "source_thread_id": row[5],
            "source_message_id": row[6],
            "source_path": row[7],
            "source_bucket": row[8],
            "locator_json": json.loads(row[9]) if row[9] else None,
            "meta": json.loads(row[10]) if row[10] else None,
        }
        for row in rows
    ]


def insert_predicate_projection(
    con: sqlite3.Connection,
    message_id: str,
    canonical_thread_id: str,
    projection: Mapping[str, Any],
) -> int:
    """Replace predicate projection rows for one message. Returns predicate count inserted."""
    predicates = projection.get("predicates")
    if not isinstance(predicates, list):
        return 0

    con.execute("DELETE FROM predicate_roles WHERE message_id = ?", (message_id,))
    con.execute("DELETE FROM predicate_refs WHERE message_id = ?", (message_id,))

    inserted = 0
    for pred_index, predicate in enumerate(predicates):
        if not isinstance(predicate, Mapping):
            continue
        predicate_ref = str(predicate.get("ref") or f"pred:{pred_index}")
        predicate_ref_id = f"{message_id}:{predicate_ref}"
        con.execute(
            """
            INSERT OR REPLACE INTO predicate_refs
                (predicate_ref_id, message_id, canonical_thread_id, predicate_ref, atom_id,
                 predicate, structural_signature, polarity, modality, domain, wrapper_status,
                 evidence_only, source_spans_json, provenance_refs_json, meta)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                predicate_ref_id,
                message_id,
                canonical_thread_id,
                predicate_ref,
                predicate.get("atom_id"),
                str(predicate.get("predicate") or ""),
                str(predicate.get("structural_signature") or "") or None,
                str(predicate.get("polarity") or "") or None,
                str(predicate.get("modality") or "") or None,
                str(predicate.get("domain") or "") or None,
                str((predicate.get("wrapper") or {}).get("status") or "") or None,
                1 if bool((predicate.get("wrapper") or {}).get("evidence_only", True)) else 0,
                json.dumps(predicate.get("source_spans")) if predicate.get("source_spans") is not None else None,
                json.dumps(predicate.get("provenance_refs")) if predicate.get("provenance_refs") is not None else None,
                json.dumps(
                    {
                        "version": projection.get("version"),
                        "limits": projection.get("limits"),
                        "counts": projection.get("counts"),
                    }
                ),
            ),
        )
        roles = predicate.get("roles")
        if isinstance(roles, list):
            for role_index, role in enumerate(roles):
                if not isinstance(role, Mapping):
                    continue
                role_name = str(role.get("name") or "")
                role_value = str(role.get("value") or "")
                if not role_name or not role_value:
                    continue
                predicate_role_id = f"{predicate_ref_id}:role:{role_index}"
                con.execute(
                    """
                    INSERT OR REPLACE INTO predicate_roles
                        (predicate_role_id, predicate_ref_id, message_id, role_name, role_value,
                         role_status, entity_type, cardinality, members_json, provenance_refs_json, meta)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        predicate_role_id,
                        predicate_ref_id,
                        message_id,
                        role_name,
                        role_value,
                        str(role.get("status") or "") or None,
                        str(role.get("entity_type") or "") or None,
                        str(role.get("cardinality") or "") or None,
                        json.dumps(role.get("members")) if role.get("members") is not None else None,
                        json.dumps(role.get("provenance_refs")) if role.get("provenance_refs") is not None else None,
                        json.dumps({}),
                    ),
                )
        inserted += 1
    return inserted


def list_predicate_refs(
    con: sqlite3.Connection,
    message_id: Optional[str] = None,
    canonical_thread_id: Optional[str] = None,
) -> list[dict]:
    sql = """
        SELECT predicate_ref_id, message_id, canonical_thread_id, predicate_ref, atom_id,
               predicate, structural_signature, polarity, modality, domain, wrapper_status,
               evidence_only, source_spans_json, provenance_refs_json, meta
        FROM predicate_refs
    """
    conditions: list[str] = []
    params: list[Any] = []
    if message_id is not None:
        conditions.append("message_id = ?")
        params.append(message_id)
    if canonical_thread_id is not None:
        conditions.append("canonical_thread_id = ?")
        params.append(canonical_thread_id)
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY message_id, predicate_ref"
    rows = con.execute(sql, params).fetchall()
    return [
        {
            "predicate_ref_id": row[0],
            "message_id": row[1],
            "canonical_thread_id": row[2],
            "predicate_ref": row[3],
            "atom_id": row[4],
            "predicate": row[5],
            "structural_signature": row[6],
            "polarity": row[7],
            "modality": row[8],
            "domain": row[9],
            "wrapper_status": row[10],
            "evidence_only": bool(row[11]),
            "source_spans": json.loads(row[12]) if row[12] else None,
            "provenance_refs": json.loads(row[13]) if row[13] else None,
            "meta": json.loads(row[14]) if row[14] else None,
        }
        for row in rows
    ]


def list_predicate_roles(
    con: sqlite3.Connection,
    predicate_ref_id: Optional[str] = None,
    message_id: Optional[str] = None,
) -> list[dict]:
    sql = """
        SELECT predicate_role_id, predicate_ref_id, message_id, role_name, role_value,
               role_status, entity_type, cardinality, members_json, provenance_refs_json, meta
        FROM predicate_roles
    """
    conditions: list[str] = []
    params: list[Any] = []
    if predicate_ref_id is not None:
        conditions.append("predicate_ref_id = ?")
        params.append(predicate_ref_id)
    if message_id is not None:
        conditions.append("message_id = ?")
        params.append(message_id)
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY predicate_ref_id, role_name, role_value"
    rows = con.execute(sql, params).fetchall()
    return [
        {
            "predicate_role_id": row[0],
            "predicate_ref_id": row[1],
            "message_id": row[2],
            "role_name": row[3],
            "role_value": row[4],
            "role_status": row[5],
            "entity_type": row[6],
            "cardinality": row[7],
            "members": json.loads(row[8]) if row[8] else None,
            "provenance_refs": json.loads(row[9]) if row[9] else None,
            "meta": json.loads(row[10]) if row[10] else None,
        }
        for row in rows
    ]


def search_predicate_candidates(
    con: sqlite3.Connection,
    structural_signatures: list[str],
    role_arguments: list[str] | None = None,
    *,
    limit: int = 20,
    platform: str | list[str] | None = None,
    cutoff_iso: str | None = None,
    group_thread_ids: set[str] | None = None,
) -> list[dict]:
    """Return predicate-first message candidates with representative chunks."""
    if group_thread_ids is not None and not group_thread_ids:
        return []

    normalized_signatures = sorted({str(sig).strip() for sig in structural_signatures if str(sig).strip()})
    normalized_role_args = sorted({str(arg).strip() for arg in (role_arguments or []) if str(arg).strip()})
    if not normalized_signatures and not normalized_role_args:
        return []

    message_filters = _predicate_message_filter_sql(platform, cutoff_iso, group_thread_ids)

    candidates: dict[str, dict[str, Any]] = {}
    if normalized_signatures:
        placeholders = ",".join("?" * len(normalized_signatures))
        rows = con.execute(
            f"""
            SELECT pr.message_id, pr.canonical_thread_id, pr.structural_signature
            FROM predicate_refs pr
            JOIN messages m ON pr.message_id = m.message_id
            WHERE pr.structural_signature IN ({placeholders}) {message_filters['sql']}
            """,
            [*normalized_signatures, *message_filters["params"]],
        ).fetchall()
        for message_id, canonical_thread_id, structural_signature in rows:
            if not message_id:
                continue
            candidate = candidates.setdefault(
                message_id,
                {
                    "message_id": message_id,
                    "canonical_thread_id": canonical_thread_id,
                    "matched_signatures": set(),
                    "matched_role_arguments": set(),
                },
            )
            if structural_signature:
                candidate["matched_signatures"].add(structural_signature)

    if normalized_role_args:
        role_pairs = [
            tuple(arg.split("|", 1))
            for arg in normalized_role_args
            if "|" in arg and all(part.strip() for part in arg.split("|", 1))
        ]
        if role_pairs:
            clause = " OR ".join("(prr.role_name = ? AND prr.role_value = ?)" for _ in role_pairs)
            rows = con.execute(
                f"""
                SELECT prr.message_id, pr.canonical_thread_id, prr.role_name, prr.role_value
                FROM predicate_roles prr
                JOIN predicate_refs pr ON prr.predicate_ref_id = pr.predicate_ref_id
                JOIN messages m ON prr.message_id = m.message_id
                WHERE ({clause}) {message_filters['sql']}
                """,
                [value for pair in role_pairs for value in pair] + message_filters["params"],
            ).fetchall()
            for message_id, canonical_thread_id, role_name, role_value in rows:
                if not message_id:
                    continue
                candidate = candidates.setdefault(
                    message_id,
                    {
                        "message_id": message_id,
                        "canonical_thread_id": canonical_thread_id,
                        "matched_signatures": set(),
                        "matched_role_arguments": set(),
                    },
                )
                if role_name and role_value:
                    candidate["matched_role_arguments"].add(f"{role_name}|{role_value}")

    if not candidates:
        return []

    rep_rows = _representative_chunks_for_messages(con, list(candidates))
    rep_by_message = {row["message_id"]: row for row in rep_rows}

    total_sigs = len(normalized_signatures)
    total_roles = len(normalized_role_args)
    scored: list[dict] = []
    for candidate in candidates.values():
        rep = rep_by_message.get(candidate["message_id"])
        if not rep:
            continue
        matched_signatures = sorted(candidate["matched_signatures"])
        matched_role_arguments = sorted(candidate["matched_role_arguments"])
        signature_score = (len(matched_signatures) / total_sigs) if total_sigs else 0.0
        role_score = (len(matched_role_arguments) / total_roles) if total_roles else 0.0
        predicate_score = min(1.0, (signature_score * 0.8) + (role_score * 0.2))
        scored.append(
            {
                "message_id": candidate["message_id"],
                "canonical_thread_id": candidate["canonical_thread_id"],
                "chunk_id": rep["chunk_id"],
                "timestamp": rep["ts_start"],
                "predicate_score": round(predicate_score, 4),
                "matched_signatures": matched_signatures,
                "matched_role_arguments": matched_role_arguments,
            }
        )

    scored.sort(
        key=lambda item: (item["predicate_score"], len(item["matched_role_arguments"]), item["timestamp"] or ""),
        reverse=True,
    )
    return scored[:limit]


def _predicate_message_filter_sql(
    platform: str | list[str] | None,
    cutoff_iso: str | None,
    group_thread_ids: set[str] | None,
) -> dict[str, Any]:
    conditions: list[str] = []
    params: list[Any] = []

    if platform:
        platforms = [platform] if isinstance(platform, str) else platform
        placeholders = ",".join("?" * len(platforms))
        conditions.append(f"m.platform IN ({placeholders})")
        params.extend(platforms)
    if cutoff_iso:
        conditions.append("m.ts >= ?")
        params.append(cutoff_iso)
    if group_thread_ids:
        placeholders = ",".join("?" * len(group_thread_ids))
        conditions.append(f"m.canonical_thread_id IN ({placeholders})")
        params.extend(group_thread_ids)

    if not conditions:
        return {"sql": "", "params": params}
    return {"sql": " AND " + " AND ".join(conditions), "params": params}


def _representative_chunks_for_messages(
    con: sqlite3.Connection,
    message_ids: list[str],
) -> list[dict]:
    if not message_ids:
        return []
    placeholders = ",".join("?" * len(message_ids))
    rows = con.execute(
        f"""
        SELECT c.message_id, c.chunk_id, c.ts_start
        FROM chunks c
        JOIN (
            SELECT message_id, MIN(chunk_index) AS min_chunk_index
            FROM chunks
            WHERE message_id IN ({placeholders})
            GROUP BY message_id
        ) first_chunk
          ON c.message_id = first_chunk.message_id
         AND c.chunk_index = first_chunk.min_chunk_index
        """,
        message_ids,
    ).fetchall()
    return [
        {"message_id": row[0], "chunk_id": row[1], "ts_start": row[2]}
        for row in rows
    ]


def export_thoughts(con: sqlite3.Connection):
    rows = con.execute(
        "SELECT thought_id, text, created_at, meta FROM thoughts ORDER BY created_at"
    ).fetchall()
    return [{"thought_id": r[0], "content": r[1], "created_at": r[2], "metadata": r[3]}
            for r in rows]


# ── Thread iteration + summaries ─────────────────────────────────────────────

def iter_threads(con: sqlite3.Connection):
    """Yield one dict per unique thread with metadata aggregated from messages."""
    cur = con.execute("""
        SELECT canonical_thread_id,
               MAX(platform) AS platform,
               MAX(title) AS title,
               COUNT(*) AS message_count,
               MIN(ts) AS ts_start,
               MAX(ts) AS ts_end
        FROM messages
        GROUP BY canonical_thread_id
        ORDER BY ts_start ASC
    """)
    for row in cur:
        yield {
            "canonical_thread_id": row[0],
            "platform": row[1],
            "title": row[2],
            "message_count": row[3],
            "ts_start": row[4],
            "ts_end": row[5],
        }


def get_thread_messages(con: sqlite3.Connection, canonical_thread_id: str) -> list[dict]:
    """Return all messages for a thread, ordered chronologically."""
    rows = con.execute(
        "SELECT role, text, ts FROM messages WHERE canonical_thread_id = ? ORDER BY ts",
        (canonical_thread_id,),
    ).fetchall()
    return [{"role": r[0], "text": r[1], "ts": r[2]} for r in rows]


_SUMMARY_SELECT = """
    SELECT summary_id, canonical_thread_id, segment_index, title, platform,
           message_count, ts_start, ts_end, summary, key_topics
    FROM thread_summaries
"""
# Column indices for the fixed 10-col layout above:
#   summary_id[0], canonical_thread_id[1], segment_index[2], title[3],
#   platform[4], message_count[5], ts_start[6], ts_end[7], summary[8], key_topics[9]


def has_thread_summary(con: sqlite3.Connection, canonical_thread_id: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM thread_summaries WHERE canonical_thread_id = ?",
        (canonical_thread_id,),
    ).fetchone()
    return row is not None


def insert_thread_summary(
    con: sqlite3.Connection,
    summary_id: str,
    canonical_thread_id: str,
    segment_index: int,
    title: Optional[str],
    platform: Optional[str],
    message_count: int,
    segment_chars: int,
    ts_start: Optional[str],
    ts_end: Optional[str],
    summary: str,
    key_topics: list[str],
    summary_model: str,
    now: str,
):
    """Insert or replace a single summary segment."""
    con.execute(
        """
        INSERT OR REPLACE INTO thread_summaries
            (summary_id, canonical_thread_id, segment_index, title, platform,
             message_count, segment_chars, ts_start, ts_end, summary,
             key_topics, summary_model, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,
            COALESCE((SELECT created_at FROM thread_summaries WHERE summary_id=?), ?),
            ?)
        """,
        (summary_id, canonical_thread_id, segment_index, title, platform,
         message_count, segment_chars, ts_start, ts_end, summary,
         json.dumps(key_topics), summary_model,
         summary_id, now, now),
    )


def insert_thread_summary_embedding(
    con: sqlite3.Connection, summary_id: str, embedding: list[float]
):
    """Insert or replace the embedding for a summary segment."""
    con.execute(
        "INSERT OR REPLACE INTO vec_thread_summaries (summary_id, embedding) VALUES (?,?)",
        (summary_id, serialize_f32(embedding)),
    )


def delete_thread_summaries(con: sqlite3.Connection, canonical_thread_id: str) -> int:
    """Delete all segments and embeddings for a thread. Returns number of segments deleted."""
    summary_ids = [
        r[0] for r in con.execute(
            "SELECT summary_id FROM thread_summaries WHERE canonical_thread_id = ?",
            (canonical_thread_id,),
        ).fetchall()
    ]
    if summary_ids:
        placeholders = ",".join("?" * len(summary_ids))
        con.execute(f"DELETE FROM vec_thread_summaries WHERE summary_id IN ({placeholders})", summary_ids)
    cur = con.execute(
        "DELETE FROM thread_summaries WHERE canonical_thread_id = ?",
        (canonical_thread_id,),
    )
    return cur.rowcount


def get_thread_summary(con: sqlite3.Connection, canonical_thread_id: str):
    """Returns the first segment (segment_index=0) for a thread using the 10-col layout, or None."""
    return con.execute(
        _SUMMARY_SELECT + " WHERE canonical_thread_id = ? ORDER BY segment_index LIMIT 1",
        (canonical_thread_id,),
    ).fetchone()


def get_thread_summaries(con: sqlite3.Connection, canonical_thread_id: str) -> list:
    """Return all segments for a thread in segment_index order (10-col layout).

    Returns an empty list if the thread has no summary yet.
    Use this when you need the full picture of a thread (e.g. for display),
    rather than get_thread_summary which returns only the first segment.
    """
    return con.execute(
        _SUMMARY_SELECT + " WHERE canonical_thread_id = ? ORDER BY segment_index",
        (canonical_thread_id,),
    ).fetchall()


def get_summary_by_id(con: sqlite3.Connection, summary_id: str):
    """Fetch a single segment by summary_id using the 10-col layout."""
    return con.execute(
        _SUMMARY_SELECT + " WHERE summary_id = ?",
        (summary_id,),
    ).fetchone()


def list_thread_summaries(
    con: sqlite3.Connection,
    limit: int = 100,
    platform: Optional[str] = None,
    since_iso: Optional[str] = None,
):
    """Returns rows in the 10-col layout, one row per segment, ordered newest first.

    10-col layout: summary_id[0], canonical_thread_id[1], segment_index[2], title[3],
    platform[4], message_count[5], ts_start[6], ts_end[7], summary[8], key_topics[9].
    """
    sql = _SUMMARY_SELECT
    params: list = []
    conditions = []
    if platform:
        conditions.append("platform = ?")
        params.append(platform)
    if since_iso:
        conditions.append("ts_start >= ?")
        params.append(since_iso)
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY ts_start DESC, segment_index ASC LIMIT ?"
    params.append(limit)
    return con.execute(sql, params).fetchall()


def search_thread_summaries(
    con: sqlite3.Connection, embedding: list[float], limit: int = 10
):
    """Vector KNN search on thread summaries. Returns [(summary_id, distance)]."""
    return con.execute(
        "SELECT summary_id, distance FROM vec_thread_summaries "
        "WHERE embedding MATCH ? AND k = ?",
        (serialize_f32(embedding), limit),
    ).fetchall()


def summary_count(con: sqlite3.Connection) -> int:
    """Number of summary segments (not threads)."""
    try:
        return con.execute("SELECT count(*) FROM thread_summaries").fetchone()[0]
    except sqlite3.OperationalError:
        return 0


def summarized_thread_count(con: sqlite3.Connection) -> int:
    """Number of distinct threads that have at least one summary segment."""
    try:
        return con.execute(
            "SELECT count(DISTINCT canonical_thread_id) FROM thread_summaries"
        ).fetchone()[0]
    except sqlite3.OperationalError:
        return 0


def unsummarized_thread_count(con: sqlite3.Connection) -> int:
    """Threads that have messages but no summary yet."""
    row = con.execute("""
        SELECT count(DISTINCT canonical_thread_id) FROM messages
        WHERE canonical_thread_id NOT IN (SELECT canonical_thread_id FROM thread_summaries)
    """).fetchone()
    return row[0] if row else 0


# ── Thread groups ─────────────────────────────────────────────────────────────

def create_group(
    con: sqlite3.Connection,
    group_id: str,
    name: str,
    description: Optional[str],
    now: str,
) -> bool:
    """Create a new group. Returns False if name already exists."""
    try:
        con.execute(
            "INSERT INTO thread_groups (group_id, name, description, created_at) VALUES (?,?,?,?)",
            (group_id, name, description, now),
        )
        con.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def list_groups(con: sqlite3.Connection) -> list[tuple]:
    """Returns (group_id, name, description, created_at, member_count) per group."""
    return con.execute("""
        SELECT g.group_id, g.name, g.description, g.created_at,
               COUNT(m.canonical_thread_id) AS member_count
        FROM thread_groups g
        LEFT JOIN thread_group_members m ON g.group_id = m.group_id
        GROUP BY g.group_id
        ORDER BY g.name
    """).fetchall()


def get_group_by_name(con: sqlite3.Connection, name: str):
    """Returns (group_id, name, description, created_at) or None."""
    return con.execute(
        "SELECT group_id, name, description, created_at FROM thread_groups WHERE name = ?",
        (name,),
    ).fetchone()


def add_to_group(
    con: sqlite3.Connection,
    canonical_thread_id: str,
    group_id: str,
    now: str,
) -> bool:
    """Add a thread to a group. Returns True if inserted, False if already a member."""
    cur = con.execute(
        "INSERT OR IGNORE INTO thread_group_members (canonical_thread_id, group_id, added_at) "
        "VALUES (?,?,?)",
        (canonical_thread_id, group_id, now),
    )
    return cur.rowcount > 0


def remove_from_group(con: sqlite3.Connection, canonical_thread_id: str, group_id: str) -> bool:
    cur = con.execute(
        "DELETE FROM thread_group_members WHERE canonical_thread_id = ? AND group_id = ?",
        (canonical_thread_id, group_id),
    )
    return cur.rowcount > 0


def delete_group(con: sqlite3.Connection, group_id: str) -> bool:
    """Delete a group and all its memberships."""
    con.execute("DELETE FROM thread_group_members WHERE group_id = ?", (group_id,))
    cur = con.execute("DELETE FROM thread_groups WHERE group_id = ?", (group_id,))
    con.commit()
    return cur.rowcount > 0


def get_threads_in_group(con: sqlite3.Connection, group_id: str) -> list[dict]:
    """Return thread metadata for all members of a group."""
    rows = con.execute("""
        SELECT m.canonical_thread_id,
               MAX(msgs.platform) AS platform,
               MAX(msgs.title) AS title,
               COUNT(msgs.message_id) AS message_count,
               MIN(msgs.ts) AS ts_start,
               MAX(msgs.ts) AS ts_end
        FROM thread_group_members m
        JOIN messages msgs ON msgs.canonical_thread_id = m.canonical_thread_id
        WHERE m.group_id = ?
        GROUP BY m.canonical_thread_id
        ORDER BY ts_start DESC
    """, (group_id,)).fetchall()
    return [
        {"canonical_thread_id": r[0], "platform": r[1], "title": r[2],
         "message_count": r[3], "ts_start": r[4], "ts_end": r[5]}
        for r in rows
    ]


def get_group_thread_ids(con: sqlite3.Connection, group_id: str) -> set[str]:
    """Return the set of canonical_thread_ids belonging to a group."""
    rows = con.execute(
        "SELECT canonical_thread_id FROM thread_group_members WHERE group_id = ?",
        (group_id,),
    ).fetchall()
    return {r[0] for r in rows}


def group_count(con: sqlite3.Connection) -> int:
    try:
        return con.execute("SELECT count(*) FROM thread_groups").fetchone()[0]
    except sqlite3.OperationalError:
        return 0
