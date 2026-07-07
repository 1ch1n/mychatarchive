"""Acceptance proof for streaming ingest (v0.3.0): ingest a large real-format
export while sampling peak RSS. Pass = peak RSS stays bounded (one thread
buffered at a time), instead of scaling with file size as the old
list(parse(...)) implementation did.

Usage:
    python scripts/ingest_memory_proof.py <export.json> <archive.db> [rss-cap-mb]
"""

import sys
import threading
import time
from pathlib import Path

import psutil

from mychatarchive import db, ingest


def main() -> int:
    export = Path(sys.argv[1])
    archive = Path(sys.argv[2])
    cap_mb = int(sys.argv[3]) if len(sys.argv) > 3 else 600

    proc = psutil.Process()
    peak = {"rss": 0}
    stop = threading.Event()

    def sample():
        while not stop.is_set():
            peak["rss"] = max(peak["rss"], proc.memory_info().rss)
            time.sleep(0.2)

    t = threading.Thread(target=sample, daemon=True)
    t.start()
    started = time.time()

    inserted, dupes = ingest.run(export, archive, format_name="chatgpt")

    stop.set()
    t.join(timeout=2)
    peak["rss"] = max(peak["rss"], proc.memory_info().rss)
    secs = time.time() - started

    size_mb = export.stat().st_size / 1024 / 1024
    peak_mb = peak["rss"] / 1024 / 1024

    con = db.get_connection(archive)
    total = db.message_count(con)
    threads = db.thread_count(con)
    con.close()

    print("\n=== STREAMING INGEST PROOF ===")
    print(f"file:       {size_mb:.1f}MB")
    print(f"inserted:   {inserted} messages ({dupes} dupes) in {threads} threads; db total {total}")
    print(f"wall time:  {secs:.1f}s ({size_mb / secs:.1f}MB/s)")
    print(f"peak RSS:   {peak_mb:.0f}MB (cap {cap_mb}MB)")
    ok = peak_mb < cap_mb and inserted > 0
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
