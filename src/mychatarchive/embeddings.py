"""Embedding pipeline — orchestrates embedding generation and storage.

Delegates actual embedding to the active embedder backend.
The run() function reads messages from DB, embeds them, and stores vectors.
"""

import hashlib
import sys
from pathlib import Path

from tqdm import tqdm

from mychatarchive import db
from mychatarchive.config import get_chunk_max_chars


def _embedder():
    from mychatarchive.backends import get_embedder
    return get_embedder()


def sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", errors="ignore")).hexdigest()


def embed_texts(texts: list[str]) -> list[list[float]]:
    return _embedder().embed_texts(texts)


def embed_single(text: str) -> list[float]:
    return _embedder().embed_single(text)


def run(db_path: Path, batch_size: int = 64, force: bool = False):
    con = db.get_connection(db_path)
    db.ensure_schema(con)

    total = db.message_count(con)
    if total == 0:
        print("No messages in database. Import some chats first.", file=sys.stderr)
        con.close()
        return

    if force:
        already_embedded = set()
        print(f"Force mode: re-embedding all {total} messages.", file=sys.stderr)
    else:
        already_embedded = db.embedded_message_ids(con)
        if already_embedded:
            print(
                f"{len(already_embedded)} messages already embedded. "
                f"Embedding remaining. Use --force to re-embed all.",
                file=sys.stderr,
            )

    # Warm up the embedder
    _embedder()

    chunk_max = get_chunk_max_chars()
    batch_texts: list[str] = []
    batch_meta: list[dict] = []
    embedded = 0
    skipped = 0

    pbar = tqdm(total=total, desc="Embedding", file=sys.stderr)
    for msg in db.iter_messages(con):
        pbar.update(1)

        if msg["message_id"] in already_embedded:
            skipped += 1
            continue

        text = (msg["text"] or "").strip()
        if not text or len(text) < 10:
            skipped += 1
            continue

        if len(text) > chunk_max:
            text = text[:chunk_max]

        batch_texts.append(text)
        batch_meta.append(msg)

        if len(batch_texts) >= batch_size:
            embedded += _flush_batch(con, batch_texts, batch_meta)
            batch_texts, batch_meta = [], []

    if batch_texts:
        embedded += _flush_batch(con, batch_texts, batch_meta)

    pbar.close()
    con.close()

    print(f"\nDone. Embedded: {embedded} | Skipped: {skipped} | Total: {total}", file=sys.stderr)


def _flush_batch(con, texts: list[str], metas: list[dict]) -> int:
    embeddings = embed_texts(texts)
    for text, meta, emb in zip(texts, metas, embeddings):
        chunk_id = sha1(f"{meta['message_id']}|0")
        db.insert_chunk(
            con,
            chunk_id=chunk_id,
            message_id=meta["message_id"],
            thread_id=meta["canonical_thread_id"],
            chunk_index=0,
            text=text,
            ts_start=meta["ts"],
            ts_end=meta["ts"],
            embedding=emb,
            meta={"role": meta["role"], "title": meta["title"]},
        )
    con.commit()
    return len(texts)
