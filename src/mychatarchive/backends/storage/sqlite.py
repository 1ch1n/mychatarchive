"""SQLite + sqlite-vec storage backend (default).

All data lives in a single .sqlite file with FTS5 and vector search via sqlite-vec.
"""

import json
import sqlite3
import struct
from pathlib import Path
from typing import Optional

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
    return con


def ensure_schema(con: sqlite3.Connection):
    """Create all tables (ingestion + brain). Idempotent."""
    dim = _get_embedding_dim()
    cur = con.cursor()

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
            source_id TEXT NOT NULL
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

        CREATE TABLE IF NOT EXISTS thoughts (
            thought_id TEXT PRIMARY KEY,
            text TEXT NOT NULL,
            created_at TEXT NOT NULL,
            meta TEXT
        );
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


# --- Ingestion ---

def insert_message(con: sqlite3.Connection, message_id: str, canonical_thread_id: str,
                   platform: str, account_id: str, ts: str, role: str, text: str,
                   title: str, source_id: str) -> bool:
    """Insert a message. Returns True if inserted, False if duplicate."""
    cur = con.execute(
        "INSERT OR IGNORE INTO messages "
        "(message_id, canonical_thread_id, platform, account_id, ts, role, text, title, source_id) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (message_id, canonical_thread_id, platform, account_id, ts, role, text, title, source_id),
    )
    if cur.rowcount == 0:
        return False
    con.execute("INSERT INTO messages_fts (text) VALUES (?)", (text,))
    fts_rowid = con.execute("SELECT max(rowid) FROM messages_fts").fetchone()[0]
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
        SELECT message_id, canonical_thread_id, ts, role, text, title
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


def search_chunks(con: sqlite3.Connection, embedding: list[float], limit: int = 10):
    return con.execute(
        "SELECT chunk_id, distance FROM vec_chunks "
        "WHERE embedding MATCH ? AND k = ?",
        (serialize_f32(embedding), limit),
    ).fetchall()


def search_thoughts(con: sqlite3.Connection, embedding: list[float], limit: int = 10):
    return con.execute(
        "SELECT thought_id, distance FROM vec_thoughts "
        "WHERE embedding MATCH ? AND k = ?",
        (serialize_f32(embedding), limit),
    ).fetchall()


def fts_search(con: sqlite3.Connection, query: str, limit: int = 20):
    """Full-text search via FTS5."""
    return con.execute("""
        SELECT d.message_id, m.text, m.canonical_thread_id, m.ts, m.role, m.title
        FROM messages_fts f
        JOIN messages_fts_docids d ON f.rowid = d.rowid
        JOIN messages m ON m.message_id = d.message_id
        WHERE messages_fts MATCH ?
        LIMIT ?
    """, (query, limit)).fetchall()


def get_recent_chunks(con: sqlite3.Connection, cutoff_iso: str, limit: int = 20):
    return con.execute(
        "SELECT chunk_id, text, canonical_thread_id, ts_start, meta "
        "FROM chunks WHERE ts_start >= ? ORDER BY ts_start DESC LIMIT ?",
        (cutoff_iso, limit),
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
