"""Ingestion engine: parse exports and write to SQLite with dedup."""

import hashlib
import re
import datetime
import inspect
import sys
from pathlib import Path
from typing import Any

from tqdm import tqdm

from mychatarchive import db
from mychatarchive.backends import get_storage
from mychatarchive.itir import enrich_text as enrich_text_with_itir
from mychatarchive.live import choose_live_selector, fetch_live_messages
from mychatarchive.parsers import parse, detect_format

IMPORTABLE_EXTENSIONS = {".json", ".jsonl"}
OPTIONAL_ARCHIVE_TRUTH_FIELDS = (
    "source_path",
    "source_bucket",
    "provenance_json",
    "content_blocks",
    "provenance_refs",
)


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


def _build_message_meta(content: str | None) -> dict | None:
    if not content or not content.strip():
        return None
    itir_payload = enrich_text_with_itir(content)
    return {"itir": itir_payload}


def _extract_optional_archive_fields(msg: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for key in OPTIONAL_ARCHIVE_TRUTH_FIELDS:
        value = msg.get(key)
        if value is not None:
            fields[key] = value
    return fields


def _legacy_meta_with_archive_truth(
    message_meta: dict[str, Any] | None,
    optional_fields: dict[str, Any],
    *,
    include_fields: tuple[str, ...] = OPTIONAL_ARCHIVE_TRUTH_FIELDS,
) -> dict[str, Any] | None:
    if not optional_fields or not include_fields:
        return message_meta

    merged = dict(message_meta or {})
    archive_truth = merged.get("archive_truth")
    if not isinstance(archive_truth, dict):
        archive_truth = {}

    for key in include_fields:
        if key in optional_fields:
            archive_truth[key] = optional_fields[key]
    merged["archive_truth"] = archive_truth
    return merged


def _supported_insert_params() -> set[str]:
    try:
        return set(inspect.signature(db.insert_message).parameters)
    except (TypeError, ValueError):
        return set()


def _resolve_optional_writer(hook_names: tuple[str, ...]):
    for name in hook_names:
        writer = getattr(db, name, None)
        if callable(writer):
            return writer
    storage = get_storage()
    for name in hook_names:
        writer = getattr(storage, name, None)
        if callable(writer):
            return writer
    return None


def _call_optional_writer(
    writer,
    *,
    con,
    message_id: str,
    payload,
    payload_kw: str,
) -> None:
    attempts = (
        lambda: writer(con, message_id, payload),
        lambda: writer(con=con, message_id=message_id, **{payload_kw: payload}),
        lambda: writer(connection=con, message_id=message_id, **{payload_kw: payload}),
    )
    for attempt in attempts:
        try:
            attempt()
            return
        except TypeError:
            continue
    raise TypeError(f"Unable to call optional writer {writer!r} with payload '{payload_kw}'.")


def _persist_optional_side_tables(
    con,
    message_id: str,
    canonical_thread_id: str,
    optional_fields: dict[str, Any],
) -> None:
    content_blocks = optional_fields.get("content_blocks")
    if content_blocks is not None:
        writer = _resolve_optional_writer((
            "insert_message_blocks",
            "upsert_message_blocks",
            "insert_content_blocks",
            "insert_message_block",
        ))
        if writer:
            if getattr(writer, "__name__", "") == "insert_message_block":
                for idx, block in enumerate(content_blocks):
                    if not isinstance(block, dict):
                        continue
                    block_id = str(block.get("block_id") or f"{message_id}:block:{idx}")
                    db.insert_message_block(
                        con,
                        block_id=block_id,
                        message_id=message_id,
                        canonical_thread_id=str(
                            block.get("canonical_thread_id") or block.get("thread_id") or canonical_thread_id
                        ),
                        block_index=int(block.get("block_index", idx)),
                        block_type=str(block.get("block_type") or block.get("type") or "text"),
                        text=str(block.get("text") or block.get("content") or ""),
                        source_path=str(block.get("source_path") or optional_fields.get("source_path") or "") or None,
                        source_bucket=str(block.get("source_bucket") or optional_fields.get("source_bucket") or "") or None,
                        provenance_json=block.get("provenance_json"),
                        meta=block.get("meta"),
                    )
            else:
                _call_optional_writer(
                    writer,
                    con=con,
                    message_id=message_id,
                    payload=content_blocks,
                    payload_kw="blocks",
                )

    provenance_refs = optional_fields.get("provenance_refs")
    if provenance_refs is not None:
        writer = _resolve_optional_writer((
            "insert_provenance_refs",
            "upsert_provenance_refs",
            "insert_message_provenance_refs",
            "insert_provenance_ref",
        ))
        if writer:
            if getattr(writer, "__name__", "") == "insert_provenance_ref":
                for idx, ref in enumerate(provenance_refs):
                    if not isinstance(ref, dict):
                        continue
                    provenance_ref_id = str(ref.get("provenance_ref_id") or f"{message_id}:prov:{idx}")
                    db.insert_provenance_ref(
                        con,
                        provenance_ref_id=provenance_ref_id,
                        message_id=message_id,
                        block_id=str(ref.get("block_id") or "") or None,
                        ref_index=int(ref.get("ref_index", idx)),
                        source_id=str(ref.get("source_id") or "") or None,
                        source_thread_id=str(ref.get("source_thread_id") or "") or None,
                        source_message_id=str(ref.get("source_message_id") or "") or None,
                        source_path=str(ref.get("source_path") or optional_fields.get("source_path") or "") or None,
                        source_bucket=str(ref.get("source_bucket") or optional_fields.get("source_bucket") or "") or None,
                        locator_json=ref.get("locator_json"),
                        meta=ref.get("meta"),
                    )
            else:
                _call_optional_writer(
                    writer,
                    con=con,
                    message_id=message_id,
                    payload=provenance_refs,
                    payload_kw="refs",
                )


def _persist_predicate_projection(
    con,
    message_id: str,
    canonical_thread_id: str,
    message_meta: dict[str, Any] | None,
) -> None:
    if not isinstance(message_meta, dict):
        return
    itir_payload = message_meta.get("itir")
    if not isinstance(itir_payload, dict):
        return
    projection = itir_payload.get("predicate_projection")
    if not isinstance(projection, dict):
        return
    writer = getattr(db, "insert_predicate_projection", None)
    if callable(writer):
        writer(con, message_id, canonical_thread_id, projection)


def _canonical_thread_id_for_messages(
    thread_messages: list[dict],
    *,
    platform: str,
    account_id: str,
) -> str:
    first = thread_messages[0]
    thread_source_id = str(first.get("thread_id") or "").strip()
    if thread_source_id:
        return sha1("|".join([platform, account_id, "source_thread_id", thread_source_id]))

    first_snip = (first["content"] or "")[:256]
    return sha1("|".join([
        platform,
        account_id,
        norm_text(first["thread_title"]),
        str(round_epoch(first["created_at"]) or ""),
        first["role"] or "",
        norm_text(first_snip),
    ]))


def _message_id_for_message(
    msg: dict,
    *,
    platform: str,
    account_id: str,
    canonical_thread_id: str,
) -> str:
    source_message_id = str(msg.get("source_message_id") or "").strip()
    if source_message_id:
        return sha1("|".join([
            platform, account_id, canonical_thread_id, "source_message_id", source_message_id,
        ]))

    ts_round = round_epoch(msg["created_at"]) or 0
    return sha1("|".join([
        platform, account_id, canonical_thread_id, msg["role"] or "",
        str(ts_round), norm_text(msg["content"] or ""),
    ]))


def ingest_parsed_messages(
    messages: list[dict],
    *,
    db_path: Path,
    platform: str,
    account_id: str = "main",
    source_id: str,
):
    """Insert already-parsed messages while preserving upstream provenance."""
    con = db.get_connection(db_path)
    db.ensure_schema(con)

    if not messages:
        con.close()
        return 0, 0

    threads: dict[str, list[dict]] = {}
    for msg in messages:
        threads.setdefault(str(msg["thread_id"]), []).append(msg)

    inserted = 0
    duplicates = 0
    insert_params = _supported_insert_params()
    for _tid, thread_messages in tqdm(threads.items(), desc="Importing", file=sys.stderr):
        thread_messages.sort(key=lambda m: m["created_at"])
        canonical_thread_id = _canonical_thread_id_for_messages(
            thread_messages,
            platform=platform,
            account_id=account_id,
        )

        for msg in thread_messages:
            ts_iso = iso_from_epoch(msg["created_at"])
            if not ts_iso:
                continue
            message_id = _message_id_for_message(
                msg,
                platform=platform,
                account_id=account_id,
                canonical_thread_id=canonical_thread_id,
            )
            optional_fields = _extract_optional_archive_fields(msg)
            message_meta = _build_message_meta(msg["content"] or "")

            insert_kwargs: dict[str, Any] = {
                "con": con,
                "message_id": message_id,
                "canonical_thread_id": canonical_thread_id,
                "platform": platform,
                "account_id": account_id,
                "ts": ts_iso,
                "role": msg["role"] or "",
                "text": msg["content"] or "",
                "title": msg.get("thread_title"),
                "source_id": source_id,
                "source_thread_id": str(msg.get("thread_id") or "") or None,
                "source_message_id": str(msg.get("source_message_id") or "") or None,
                "meta": message_meta,
            }

            direct_optional = ("source_path", "source_bucket", "provenance_json")
            fallback_fields = [
                key for key in OPTIONAL_ARCHIVE_TRUTH_FIELDS
                if key in optional_fields and key not in insert_params
            ]
            if fallback_fields:
                insert_kwargs["meta"] = _legacy_meta_with_archive_truth(
                    message_meta,
                    optional_fields,
                    include_fields=tuple(fallback_fields),
                )
            for key in direct_optional:
                if key in insert_params and key in optional_fields:
                    insert_kwargs[key] = optional_fields[key]

            was_inserted = db.insert_message(**insert_kwargs)
            if was_inserted:
                inserted += 1
                _persist_optional_side_tables(con, message_id, canonical_thread_id, optional_fields)
                _persist_predicate_projection(con, message_id, canonical_thread_id, insert_kwargs["meta"])
            else:
                duplicates += 1
        con.commit()

    con.close()
    return inserted, duplicates


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

    print(f"Parsing {format_name} export: {file_path}", file=sys.stderr)
    messages = list(parse(file_path, format_name))

    if not messages:
        print("No messages found in export.", file=sys.stderr)
        return 0, 0

    thread_ids = set(m["thread_id"] for m in messages)
    print(f"Found {len(messages)} messages in {len(thread_ids)} threads.", file=sys.stderr)

    inserted, duplicates = ingest_parsed_messages(
        messages,
        db_path=db_path,
        platform=platform,
        account_id=account_id,
        source_id=source_id,
    )
    con = db.get_connection(db_path)
    db.ensure_schema(con)
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

    fmt = src.get("format")
    account = src.get("account", "main")
    source_type = src.get("type", "path")

    if source_type == "live":
        return run_live_source(source_name, db_path)

    path = Path(src["path"]).expanduser()
    if not path.exists():
        print(f"Source path does not exist: {path}", file=sys.stderr)
        return 0, 0

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


def run_live_source(source_name: str, db_path: Path):
    """Import from a named live source using DB-first selector resolution."""
    from mychatarchive.config import get_source

    src = get_source(source_name)
    if src is None:
        print(f"Unknown source '{source_name}'. Run 'mychatarchive sources list'.", file=sys.stderr)
        return 0, 0

    provider = src.get("provider", "chatgpt")
    selector = src.get("selector")
    account = src.get("account", "main")
    if not selector:
        print(f"Live source '{source_name}' is missing a selector.", file=sys.stderr)
        return 0, 0

    resolved_selector, resolution_meta = choose_live_selector(db_path, selector)
    print(
        f"Importing live source '{source_name}' via {provider} "
        f"(selector={selector!r}, resolved={resolved_selector!r})",
        file=sys.stderr,
    )

    messages, live_meta = fetch_live_messages(provider, resolved_selector)
    for message in messages:
        if not message.get("thread_title") and src.get("title"):
            message["thread_title"] = src["title"]

    inserted, duplicates = ingest_parsed_messages(
        messages,
        db_path=db_path,
        platform=provider,
        account_id=account,
        source_id=f"live_{source_name}",
    )
    print(
        f"  Resolution: {resolution_meta.get('resolution')} -> {live_meta.get('provider_resolution')}",
        file=sys.stderr,
    )
    return inserted, duplicates


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
