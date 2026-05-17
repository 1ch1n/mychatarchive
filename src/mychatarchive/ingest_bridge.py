"""Bridge canonical chat archive rows into MyChatArchive storage.

The bridge treats the canonical archive as read-only source of truth. It does
not create a new schema in that archive; it imports rows into MyChatArchive's
normal messages table so embeddings can hang from stable message/thread IDs.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from mychatarchive import db


CANONICAL_ALIASES: dict[str, tuple[str, ...]] = {
    "message_id": ("message_id", "id", "source_message_id"),
    "canonical_thread_id": ("canonical_thread_id", "thread_hash", "conversation_hash"),
    "source_thread_id": ("source_thread_id", "online_thread_id", "conversation_id", "thread_id"),
    "source_message_id": ("source_message_id", "upstream_message_id", "message_uuid"),
    "platform": ("platform", "provider", "source_platform"),
    "account_id": ("account_id", "account", "workspace", "user_id"),
    "ts": ("ts", "created_at", "create_time", "timestamp", "time"),
    "role": ("role", "author_role", "speaker"),
    "text": ("text", "content", "message", "body"),
    "title": ("title", "thread_title", "conversation_title"),
    "source_id": ("source_id", "import_id", "source"),
    "source_path": ("source_path", "path", "file_path"),
    "source_bucket": ("source_bucket", "bucket"),
    "provenance_json": ("provenance_json", "provenance", "metadata_json"),
    "meta": ("meta", "metadata"),
}

REQUIRED_FIELDS = ("canonical_thread_id", "ts", "role", "text")


@dataclass(frozen=True)
class BridgeResult:
    """Summary of a canonical archive import."""

    inserted: int
    duplicates: int
    skipped: int
    source_rows: int
    canonical_db: str
    mca_db: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "inserted": self.inserted,
            "duplicates": self.duplicates,
            "skipped": self.skipped,
            "source_rows": self.source_rows,
            "canonical_db": self.canonical_db,
            "mca_db": self.mca_db,
        }


def _sha1(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8", errors="ignore")).hexdigest()


def _json_loads(value: Any) -> Any:
    if value is None or isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": value}


def _iso_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return (
            dt.datetime.fromtimestamp(float(value), dt.timezone.utc)
            .replace(microsecond=0)
            .isoformat()
        )
    text = str(value).strip()
    if not text:
        return None
    try:
        numeric = float(text)
    except ValueError:
        normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
        try:
            parsed = dt.datetime.fromisoformat(normalized)
        except ValueError:
            return text
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat()
    return (
        dt.datetime.fromtimestamp(numeric, dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
    )


def _table_columns(con: sqlite3.Connection, table: str) -> set[str]:
    rows = con.execute(f"PRAGMA table_info({table})").fetchall()
    return {row["name"] for row in rows}


def _column_for(field: str, columns: set[str]) -> str | None:
    for candidate in CANONICAL_ALIASES[field]:
        if candidate in columns:
            return candidate
    return None


def _select_rows(
    con: sqlite3.Connection,
    columns: set[str],
    *,
    limit: int | None,
) -> Iterable[sqlite3.Row]:
    selects: list[str] = []
    for field in CANONICAL_ALIASES:
        column = _column_for(field, columns)
        if column:
            selects.append(f'"{column}" AS "{field}"')
        else:
            selects.append(f"NULL AS \"{field}\"")

    order_column = _column_for("ts", columns)
    sql = f"SELECT {', '.join(selects)} FROM messages"
    if order_column:
        sql += f' ORDER BY "canonical_thread_id", "{order_column}", rowid'
    if limit is not None:
        sql += " LIMIT ?"
        return con.execute(sql, (limit,))
    return con.execute(sql)


def validate_canonical_archive(con: sqlite3.Connection) -> None:
    """Raise ValueError if the source DB cannot provide canonical message rows."""
    row = con.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'messages'"
    ).fetchone()
    if row is None:
        raise ValueError("Canonical archive DB must contain a messages table.")

    columns = _table_columns(con, "messages")
    missing = [field for field in REQUIRED_FIELDS if _column_for(field, columns) is None]
    if missing:
        raise ValueError(
            "Canonical archive messages table is missing required field(s): "
            + ", ".join(missing)
        )


def _bridge_message_id(row: sqlite3.Row) -> str:
    message_id = str(row["message_id"] or "").strip()
    if message_id:
        return message_id
    parts = [
        "canonical_bridge",
        str(row["canonical_thread_id"] or ""),
        str(row["source_message_id"] or ""),
        str(row["role"] or ""),
        str(row["ts"] or ""),
        str(row["text"] or ""),
    ]
    return _sha1("|".join(parts))


def _provenance_payload(
    row: sqlite3.Row,
    *,
    canonical_db: Path,
    source_id: str,
) -> dict[str, Any]:
    existing = _json_loads(row["provenance_json"])
    payload = existing if isinstance(existing, dict) else {}
    payload.setdefault("bridge", {})
    payload["bridge"].update(
        {
            "kind": "canonical_archive_to_mychatarchive",
            "canonical_db": str(canonical_db.expanduser()),
            "source_id": source_id,
            "canonical_message_id": row["message_id"],
        }
    )
    return payload


def _meta_payload(
    row: sqlite3.Row,
    *,
    canonical_db: Path,
    source_id: str,
) -> dict[str, Any]:
    existing = _json_loads(row["meta"])
    meta = existing if isinstance(existing, dict) else {}
    meta.setdefault("canonical_bridge", {})
    meta["canonical_bridge"].update(
        {
            "canonical_db": str(canonical_db.expanduser()),
            "source_id": source_id,
            "canonical_message_id": row["message_id"],
            "canonical_thread_id": row["canonical_thread_id"],
            "source_thread_id": row["source_thread_id"],
            "source_message_id": row["source_message_id"],
        }
    )
    return meta


def import_canonical_archive(
    canonical_db_path: Path,
    mca_db_path: Path,
    *,
    default_platform: str = "canonical",
    default_account_id: str = "main",
    source_id: str | None = None,
    limit: int | None = None,
) -> BridgeResult:
    """Import rows from a canonical chat archive into MyChatArchive.

    Returns counts suitable for JSON CLI wrappers. Re-running is idempotent
    because the target message_id is deterministic and inserted with
    ``INSERT OR IGNORE`` by the active storage backend.
    """
    canonical_path = Path(canonical_db_path).expanduser()
    mca_path = Path(mca_db_path).expanduser()
    bridge_source_id = source_id or f"canonical_archive:{canonical_path.name}"

    source = sqlite3.connect(f"file:{canonical_path.resolve()}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    try:
        validate_canonical_archive(source)
        target = db.get_connection(mca_path)
        try:
            db.ensure_schema(target)
            columns = _table_columns(source, "messages")

            inserted = 0
            duplicates = 0
            skipped = 0
            source_rows = 0

            for row in _select_rows(source, columns, limit=limit):
                source_rows += 1
                ts = _iso_timestamp(row["ts"])
                text = str(row["text"] or "")
                canonical_thread_id = str(row["canonical_thread_id"] or "").strip()
                if not ts or not text.strip() or not canonical_thread_id:
                    skipped += 1
                    continue

                platform = str(row["platform"] or default_platform)
                account_id = str(row["account_id"] or default_account_id)
                message_id = _bridge_message_id(row)
                source_thread_id = str(row["source_thread_id"] or canonical_thread_id) or None
                source_message_id = str(row["source_message_id"] or row["message_id"] or "") or None

                was_inserted = db.insert_message(
                    target,
                    message_id=message_id,
                    canonical_thread_id=canonical_thread_id,
                    platform=platform,
                    account_id=account_id,
                    ts=ts,
                    role=str(row["role"] or ""),
                    text=text,
                    title=row["title"],
                    source_id=str(row["source_id"] or bridge_source_id),
                    source_thread_id=source_thread_id,
                    source_message_id=source_message_id,
                    source_path=row["source_path"],
                    source_bucket=row["source_bucket"],
                    provenance_json=_provenance_payload(
                        row, canonical_db=canonical_path, source_id=bridge_source_id
                    ),
                    meta=_meta_payload(
                        row, canonical_db=canonical_path, source_id=bridge_source_id
                    ),
                )
                if was_inserted:
                    inserted += 1
                else:
                    duplicates += 1

            target.commit()
            return BridgeResult(
                inserted=inserted,
                duplicates=duplicates,
                skipped=skipped,
                source_rows=source_rows,
                canonical_db=str(canonical_path),
                mca_db=str(mca_path),
            )
        finally:
            target.close()
    finally:
        source.close()


def _dry_run_result(canonical_db_path: Path, mca_db_path: Path, *, limit: int | None) -> BridgeResult:
    canonical_path = Path(canonical_db_path).expanduser()
    mca_path = Path(mca_db_path).expanduser()
    source = sqlite3.connect(f"file:{canonical_path.resolve()}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    try:
        validate_canonical_archive(source)
        columns = _table_columns(source, "messages")
        source_rows = 0
        skipped = 0
        for row in _select_rows(source, columns, limit=limit):
            source_rows += 1
            ts = _iso_timestamp(row["ts"])
            text = str(row["text"] or "")
            canonical_thread_id = str(row["canonical_thread_id"] or "").strip()
            if not ts or not text.strip() or not canonical_thread_id:
                skipped += 1
        return BridgeResult(
            inserted=0,
            duplicates=0,
            skipped=skipped,
            source_rows=source_rows,
            canonical_db=str(canonical_path),
            mca_db=str(mca_path),
        )
    finally:
        source.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Import canonical archive rows into MyChatArchive.")
    parser.add_argument("--canonical-db", required=True)
    parser.add_argument("--mca-db", required=True)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true", help="Emit JSON output.")
    args = parser.parse_args()

    if args.dry_run:
        result = _dry_run_result(Path(args.canonical_db), Path(args.mca_db), limit=args.limit)
    else:
        result = import_canonical_archive(
            Path(args.canonical_db),
            Path(args.mca_db),
            limit=args.limit,
        )
    payload = result.as_dict()
    if args.dry_run:
        payload["dry_run"] = True
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(payload)


if __name__ == "__main__":
    main()
