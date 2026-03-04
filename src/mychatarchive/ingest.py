"""Ingestion engine: parse exports and write to SQLite with dedup."""

import hashlib
import re
import datetime
import sys
from pathlib import Path

from tqdm import tqdm

from mychatarchive import db
from mychatarchive.parsers import parse, detect_format

IMPORTABLE_EXTENSIONS = {".json", ".jsonl"}


def norm_text(s: str | None) -> str:
    if s is None:
        return ""
    return re.sub(r"\s+", " ", s).strip().lower()


def sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", errors="ignore")).hexdigest()


def iso_from_epoch(ts) -> str | None:
    if ts is None or ts == 0:
        return None
    try:
        return (
            datetime.datetime.fromtimestamp(float(ts), datetime.timezone.utc)
            .replace(microsecond=0)
            .isoformat()
        )
    except Exception:
        return None


def round_epoch(ts) -> int | None:
    if ts is None:
        return None
    try:
        return int(round(float(ts)))
    except Exception:
        return None


def run(
    file_path: Path,
    db_path: Path,
    format_name: str | None = None,
    platform: str | None = None,
    account_id: str = "main",
    source_id: str | None = None,
):
    """Ingest an export file into the archive database."""
    is_auto = str(file_path) == "auto"

    if format_name is None:
        format_name = detect_format(file_path)
        if format_name is None:
            print(
                f"Could not detect format for {file_path}. Use --format.",
                file=sys.stderr,
            )
            return 0, 0

    platform = platform or format_name
    source_id = source_id or f"import_{file_path.stem if not is_auto else format_name}"

    con = db.get_connection(db_path)
    db.ensure_schema(con)

    print(f"Parsing {format_name} export: {file_path}", file=sys.stderr)
    messages = list(parse(file_path, format_name))

    if not messages:
        print("No messages found in export.", file=sys.stderr)
        con.close()
        return 0, 0

    thread_ids = set(m["thread_id"] for m in messages)
    print(f"Found {len(messages)} messages in {len(thread_ids)} threads.", file=sys.stderr)

    # Group by thread
    threads: dict[str, list[dict]] = {}
    for msg in messages:
        threads.setdefault(msg["thread_id"], []).append(msg)

    inserted = 0
    duplicates = 0

    for _tid, thread_messages in tqdm(threads.items(), desc="Importing", file=sys.stderr):
        thread_messages.sort(key=lambda m: m["created_at"])
        first = thread_messages[0]
        first_snip = (first["content"] or "")[:256]

        canonical_thread_id = sha1("|".join([
            platform,
            account_id,
            norm_text(first["thread_title"]),
            str(round_epoch(first["created_at"]) or ""),
            first["role"] or "",
            norm_text(first_snip),
        ]))

        for msg in thread_messages:
            ts_round = round_epoch(msg["created_at"]) or 0
            message_id = sha1("|".join([
                platform, account_id, canonical_thread_id, msg["role"] or "",
                str(ts_round), norm_text(msg["content"] or ""),
            ]))

            ts_iso = iso_from_epoch(msg["created_at"])
            if not ts_iso:
                continue

            was_inserted = db.insert_message(
                con, message_id, canonical_thread_id, platform, account_id,
                ts_iso, msg["role"] or "", msg["content"] or "",
                msg["thread_title"], source_id,
            )

            if was_inserted:
                inserted += 1
            else:
                duplicates += 1

        con.commit()

    total = db.message_count(con)
    con.close()

    print(f"\nDone.", file=sys.stderr)
    print(f"  Inserted:   {inserted}", file=sys.stderr)
    print(f"  Duplicates: {duplicates}", file=sys.stderr)
    print(f"  Total in DB: {total}", file=sys.stderr)

    return inserted, duplicates


def discover_files(directory: Path) -> list[Path]:
    """Recursively find all importable files in a directory."""
    found = []
    for p in sorted(directory.rglob("*")):
        if p.is_file() and p.suffix.lower() in IMPORTABLE_EXTENSIONS:
            found.append(p)
    return found


def run_directory(
    dir_path: Path,
    db_path: Path,
    format_name: str | None = None,
    platform: str | None = None,
    account_id: str = "main",
    source_id: str | None = None,
):
    """Import all recognized files from a directory (recursive)."""
    files = discover_files(dir_path)
    if not files:
        print(f"No importable files found in {dir_path}", file=sys.stderr)
        return 0, 0

    print(f"Found {len(files)} file(s) in {dir_path}:", file=sys.stderr)
    for f in files:
        print(f"  {f.relative_to(dir_path)}", file=sys.stderr)
    print(file=sys.stderr)

    total_inserted = 0
    total_dupes = 0

    for filepath in files:
        fmt = format_name
        if fmt is None:
            fmt = detect_format(filepath)
            if fmt is None:
                print(f"  Skipping {filepath.name} (unknown format)", file=sys.stderr)
                continue

        file_source = source_id or f"import_{filepath.stem}"
        result = run(
            file_path=filepath,
            db_path=db_path,
            format_name=fmt,
            platform=platform or fmt,
            account_id=account_id,
            source_id=file_source,
        )
        if result:
            total_inserted += result[0]
            total_dupes += result[1]

    print(f"\n{'=' * 40}", file=sys.stderr)
    print(f"Batch complete: {len(files)} file(s)", file=sys.stderr)
    print(f"  Total inserted:   {total_inserted}", file=sys.stderr)
    print(f"  Total duplicates: {total_dupes}", file=sys.stderr)
    return total_inserted, total_dupes


def run_source(
    source_name: str,
    db_path: Path,
):
    """Import from a named source defined in config."""
    from mychatarchive.config import get_source

    src = get_source(source_name)
    if src is None:
        print(f"Unknown source '{source_name}'. Run 'mychatarchive sources list'.", file=sys.stderr)
        return 0, 0

    path = Path(src["path"]).expanduser()
    if not path.exists():
        print(f"Source path does not exist: {path}", file=sys.stderr)
        return 0, 0

    fmt = src.get("format")
    account = src.get("account", "main")

    print(f"Importing from source '{source_name}' ({path})", file=sys.stderr)

    if path.is_dir():
        return run_directory(
            dir_path=path, db_path=db_path,
            format_name=fmt, account_id=account,
            source_id=f"source_{source_name}",
        )
    else:
        result = run(
            file_path=path, db_path=db_path,
            format_name=fmt, account_id=account,
            source_id=f"source_{source_name}",
        )
        return result or (0, 0)


def run_auto_source(format_name: str, db_path: Path):
    """Import from a built-in auto-discovery source (claude_code or cursor)."""
    print(f"\n{'─' * 40}", file=sys.stderr)
    print(f"Auto-importing: {format_name}", file=sys.stderr)
    print(f"{'─' * 40}", file=sys.stderr)

    try:
        result = run(
            file_path=Path("auto"),
            db_path=db_path,
            format_name=format_name,
            platform=format_name,
            account_id="main",
            source_id=f"auto_{format_name}",
        )
        return result or (0, 0)
    except (FileNotFoundError, ValueError) as e:
        print(f"  Skipped ({e})", file=sys.stderr)
        return 0, 0


def run_drop_folder(db_path: Path):
    """Import all files from the drop folder."""
    from mychatarchive.config import get_drop_folder

    folder = get_drop_folder()
    if not folder.exists():
        return 0, 0

    files = discover_files(folder)
    if not files:
        return 0, 0

    print(f"\n{'─' * 40}", file=sys.stderr)
    print(f"Drop folder: {folder}", file=sys.stderr)
    print(f"{'─' * 40}", file=sys.stderr)

    return run_directory(
        dir_path=folder,
        db_path=db_path,
        source_id="drop_folder",
    )


def run_all(db_path: Path):
    """Import from everything: auto-sources + drop folder + named sources.

    This is the one-command sync: `mychatarchive sync` or `mychatarchive import --from all`.
    """
    from mychatarchive.config import get_auto_sources, get_sources, get_drop_folder

    grand_inserted = 0
    grand_dupes = 0

    print("=" * 50, file=sys.stderr)
    print("MyChatArchive -- Syncing all sources", file=sys.stderr)
    print("=" * 50, file=sys.stderr)

    # 1. Auto-discovery sources (Claude Code, Cursor)
    auto = get_auto_sources()
    for name, enabled in auto.items():
        if not enabled:
            continue
        ins, dupes = run_auto_source(name, db_path)
        grand_inserted += ins
        grand_dupes += dupes

    # 2. Drop folder
    drop = get_drop_folder()
    if drop.exists():
        ins, dupes = run_drop_folder(db_path)
        grand_inserted += ins
        grand_dupes += dupes
    else:
        print(f"\nDrop folder not found: {drop}", file=sys.stderr)
        print(f"  Create it or run 'mychatarchive init' to configure.", file=sys.stderr)

    # 3. Named sources
    sources = get_sources()
    for name in sources:
        ins, dupes = run_source(name, db_path)
        grand_inserted += ins
        grand_dupes += dupes

    print(f"\n{'=' * 50}", file=sys.stderr)
    print(f"Sync complete", file=sys.stderr)
    print(f"  Total inserted:   {grand_inserted:,}", file=sys.stderr)
    print(f"  Total duplicates: {grand_dupes:,}", file=sys.stderr)
    print(f"{'=' * 50}", file=sys.stderr)

    return grand_inserted, grand_dupes
