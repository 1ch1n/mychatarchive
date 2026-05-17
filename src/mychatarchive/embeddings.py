"""Embedding pipeline — orchestrates embedding generation and storage.

Delegates actual embedding to the active embedder backend.
The run() function reads messages from DB, chunks them, embeds each chunk,
and stores vectors. Short messages produce 1 chunk; long messages produce
multiple overlapping chunks so no content is discarded.
"""

import hashlib
import sys
import time
from pathlib import Path

from tqdm import tqdm

from mychatarchive import db
from mychatarchive.config import get_chunk_size, get_chunk_overlap
from mychatarchive.chunker import chunk_text


def _embedder():
    from mychatarchive.backends import get_embedder
    return get_embedder()


def sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", errors="ignore")).hexdigest()


def embed_texts(texts: list[str]) -> list[list[float]]:
    return _embedder().embed_texts(texts)


def embed_single(text: str) -> list[float]:
    return _embedder().embed_single(text)


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def _format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{secs:02d}s"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


def run(
    db_path: Path,
    batch_size: int = 64,
    force: bool = False,
    max_messages: int | None = None,
    max_chunks: int | None = None,
    progress_interval: int = 10,
):
    con = db.get_connection(db_path)
    db.ensure_schema(con)

    total = db.message_count(con)
    if total == 0:
        print("No messages in database. Import some chats first.", file=sys.stderr)
        con.close()
        return

    if force:
        print(f"Force mode: clearing existing chunks and re-embedding all {total} messages.",
              file=sys.stderr)
        db.clear_chunks(con)
        already_embedded: set[str] = set()
    else:
        already_embedded = db.embedded_message_ids(con)
        if already_embedded:
            print(
                f"{len(already_embedded)} messages already embedded. "
                f"Embedding remaining. Use --force to re-embed all.",
                file=sys.stderr,
            )

    # Warm up embedder (loads model / validates API key)
    _embedder()

    chunk_size = get_chunk_size()
    overlap = get_chunk_overlap()

    # Each batch item: (chunk_text, message_meta, chunk_index)
    batch: list[tuple[str, dict, int]] = []
    messages_embedded = 0
    chunks_embedded = 0
    skipped = 0
    scanned = 0
    started = time.monotonic()
    last_progress = started

    pbar = tqdm(total=total, desc="Embedding", file=sys.stderr)
    for msg in db.iter_messages(con):
        scanned += 1
        pbar.update(1)

        if msg["message_id"] in already_embedded:
            skipped += 1
            continue

        text = (msg["text"] or "").strip()
        if not text or len(text) < 10:
            skipped += 1
            continue

        if max_messages is not None and messages_embedded >= max_messages:
            print(
                f"Reached --max-messages={max_messages}; stop point is resumable.",
                file=sys.stderr,
            )
            break

        chunks = chunk_text(text, chunk_size=chunk_size, overlap=overlap)

        pending_chunks = chunks_embedded + len(batch)
        if max_chunks is not None and pending_chunks + len(chunks) > max_chunks:
            print(
                f"Reached --max-chunks={max_chunks}; stop point is resumable.",
                file=sys.stderr,
            )
            break

        for idx, chunk in enumerate(chunks):
            batch.append((chunk, msg, idx))

        messages_embedded += 1

        if len(batch) >= batch_size:
            chunks_embedded += _flush_batch(con, batch)
            batch = []

        now = time.monotonic()
        if progress_interval > 0 and now - last_progress >= progress_interval:
            elapsed = now - started
            rate = scanned / elapsed if elapsed else 0.0
            remaining_messages = max(total - scanned, 0)
            eta = remaining_messages / rate if rate else 0.0
            print(
                "Progress: "
                f"scanned={scanned}/{total} "
                f"embedded_messages={messages_embedded} "
                f"chunks={chunks_embedded + len(batch)} "
                f"skipped={skipped} "
                f"rate={rate:.1f} msg/s "
                f"eta={_format_duration(eta)}",
                file=sys.stderr,
                flush=True,
            )
            last_progress = now

    if batch:
        chunks_embedded += _flush_batch(con, batch)

    pbar.close()
    con.close()

    print(
        f"\nDone. Messages: {messages_embedded} | Chunks: {chunks_embedded} | Skipped: {skipped}",
        file=sys.stderr,
    )


def _flush_batch(con, batch: list[tuple[str, dict, int]]) -> int:
    """Embed a batch of (text, message_meta, chunk_index) tuples and persist."""
    texts = [text for text, _, _ in batch]
    embeddings = embed_texts(texts)

    for (text, meta, chunk_idx), emb in zip(batch, embeddings):
        chunk_id = sha1(f"{meta['message_id']}|{chunk_idx}")
        db.insert_chunk(
            con,
            chunk_id=chunk_id,
            message_id=meta["message_id"],
            thread_id=meta["canonical_thread_id"],
            chunk_index=chunk_idx,
            text=text,
            ts_start=meta["ts"],
            ts_end=meta["ts"],
            embedding=emb,
            meta={"role": meta["role"], "title": meta["title"]},
        )

    con.commit()
    return len(batch)
