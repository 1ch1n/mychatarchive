"""Live source resolution and ChatGPT provider fetch helpers."""

from __future__ import annotations

import importlib
import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mychatarchive.config import get_re_gpt_roots

_ONLINE_THREAD_ID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_ONLINE_THREAD_ID_FROM_URL_RE = re.compile(
    r"/c/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)


@dataclass
class DbResolution:
    match_type: str
    canonical_thread_id: str
    source_thread_id: str | None
    title: str
    matched_thread_count: int
    latest_ts: str | None


def looks_like_online_thread_id(selector: str) -> bool:
    return bool(_ONLINE_THREAD_ID_RE.fullmatch(selector.strip()))


def looks_like_canonical_thread_id(selector: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{40}", selector.strip().lower()))


def extract_online_thread_id_from_url(selector: str) -> str | None:
    match = _ONLINE_THREAD_ID_FROM_URL_RE.search(selector)
    if match:
        return match.group(1)
    return None


def normalize_live_selector(selector: str) -> str:
    candidate = (selector or "").strip()
    online_id = extract_online_thread_id_from_url(candidate)
    if online_id:
        return online_id
    return candidate


def _fts_query(selector: str) -> str | None:
    tokens = re.findall(r"[A-Za-z0-9_]{2,}", selector.lower())
    if not tokens:
        return None
    seen: set[str] = set()
    uniq: list[str] = []
    for token in tokens:
        if token in seen:
            continue
        seen.add(token)
        uniq.append(token)
    if not uniq:
        return None
    return " OR ".join(f"{token}*" for token in uniq)


def resolve_selector_from_db(db_path: Path, selector: str) -> DbResolution | None:
    if not db_path.exists():
        return None

    con = sqlite3.connect(str(db_path))
    try:
        cur = con.cursor()
        cols = {
            row[1]
            for row in cur.execute("PRAGMA table_info(messages)").fetchall()
        }
        if not cols:
            return None
        normalized = normalize_live_selector(selector)

        if "source_thread_id" in cols:
            row = cur.execute(
                """
                SELECT canonical_thread_id, source_thread_id, COALESCE(NULLIF(title, ''), '(no title)'), MAX(ts)
                FROM messages
                WHERE LOWER(source_thread_id) = LOWER(?)
                GROUP BY canonical_thread_id, source_thread_id, title
                ORDER BY MAX(ts) DESC
                LIMIT 1
                """,
                (normalized,),
            ).fetchone()
            if row:
                return DbResolution("source_thread_id_exact", row[0], row[1], row[2], 1, row[3])

        if looks_like_canonical_thread_id(normalized):
            row = cur.execute(
                """
                SELECT canonical_thread_id, source_thread_id, COALESCE(NULLIF(title, ''), '(no title)'), MAX(ts)
                FROM messages
                WHERE LOWER(canonical_thread_id) = LOWER(?)
                GROUP BY canonical_thread_id, source_thread_id, title
                ORDER BY MAX(ts) DESC
                LIMIT 1
                """,
                (normalized,),
            ).fetchone()
            if row:
                return DbResolution("canonical_thread_id_exact", row[0], row[1], row[2], 1, row[3])

        row = cur.execute(
            """
            SELECT canonical_thread_id, source_thread_id, COALESCE(NULLIF(title, ''), '(no title)'), MAX(ts)
            FROM messages
            WHERE LOWER(title) = LOWER(?)
            GROUP BY canonical_thread_id, source_thread_id, title
            ORDER BY MAX(ts) DESC
            LIMIT 1
            """,
            (normalized,),
        ).fetchone()
        if row:
            matched = cur.execute(
                "SELECT COUNT(DISTINCT canonical_thread_id) FROM messages WHERE LOWER(title) = LOWER(?)",
                (normalized,),
            ).fetchone()[0]
            return DbResolution("title_exact", row[0], row[1], row[2], int(matched or 0), row[3])

        if len(normalized) >= 3:
            like = f"%{normalized.lower()}%"
            row = cur.execute(
                """
                SELECT canonical_thread_id, source_thread_id, COALESCE(NULLIF(title, ''), '(no title)'), MAX(ts)
                FROM messages
                WHERE LOWER(title) LIKE ?
                GROUP BY canonical_thread_id, source_thread_id, title
                ORDER BY MAX(ts) DESC
                LIMIT 1
                """,
                (like,),
            ).fetchone()
            if row:
                matched = cur.execute(
                    "SELECT COUNT(DISTINCT canonical_thread_id) FROM messages WHERE LOWER(title) LIKE ?",
                    (like,),
                ).fetchone()[0]
                return DbResolution("title_contains", row[0], row[1], row[2], int(matched or 0), row[3])

        if not looks_like_online_thread_id(normalized):
            fts_query = _fts_query(normalized)
            table_names = {
                row[0]
                for row in cur.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ('messages_fts', 'messages_fts_docids')"
                ).fetchall()
            }
            if fts_query and {"messages_fts", "messages_fts_docids"} <= table_names:
                row = cur.execute(
                    """
                    SELECT m.canonical_thread_id, m.source_thread_id,
                           COALESCE(NULLIF(m.title, ''), '(no title)') AS title,
                           MAX(m.ts) AS latest_ts,
                           COUNT(*) AS hit_count
                    FROM messages_fts f
                    JOIN messages_fts_docids d ON f.rowid = d.rowid
                    JOIN messages m ON m.message_id = d.message_id
                    WHERE messages_fts MATCH ?
                    GROUP BY m.canonical_thread_id, m.source_thread_id, title
                    ORDER BY hit_count DESC, latest_ts DESC
                    LIMIT 1
                    """,
                    (fts_query,),
                ).fetchone()
                if row:
                    matched = cur.execute(
                        """
                        SELECT COUNT(*) FROM (
                            SELECT DISTINCT m.canonical_thread_id
                            FROM messages_fts f
                            JOIN messages_fts_docids d ON f.rowid = d.rowid
                            JOIN messages m ON m.message_id = d.message_id
                            WHERE messages_fts MATCH ?
                        )
                        """,
                        (fts_query,),
                    ).fetchone()[0]
                    return DbResolution("fts_candidate", row[0], row[1], row[2], int(matched or 0), row[3])
        return None
    finally:
        con.close()


def choose_live_selector(db_path: Path, selector: str) -> tuple[str, dict[str, Any]]:
    normalized = normalize_live_selector(selector)
    resolution = resolve_selector_from_db(db_path, normalized)
    if looks_like_online_thread_id(normalized):
        return normalized, {"resolution": "selector_online_thread_id"}
    if resolution and resolution.source_thread_id and resolution.matched_thread_count == 1:
        return resolution.source_thread_id, {
            "resolution": resolution.match_type,
            "canonical_thread_id": resolution.canonical_thread_id,
            "title": resolution.title,
        }
    return normalized, {
        "resolution": resolution.match_type if resolution else "provider_fallback",
        "canonical_thread_id": resolution.canonical_thread_id if resolution else None,
        "title": resolution.title if resolution else None,
        "matched_thread_count": resolution.matched_thread_count if resolution else 0,
    }


def load_session_token() -> str | None:
    env_token = os.environ.get("CHATGPT_SESSION_TOKEN", "").strip()
    if env_token:
        return env_token
    session_file = Path.home() / ".chatgpt_session"
    if session_file.exists():
        lines = session_file.read_text(encoding="utf-8", errors="ignore").splitlines()
        if lines and lines[0].strip():
            return lines[0].strip()
    return None


def resolve_live_provider_root() -> Path:
    for root in get_re_gpt_roots():
        if (root / "re_gpt").is_dir():
            return root
    raise RuntimeError(
        "No reverse-engineered-chatgpt checkout found. Configure live.re_gpt_paths "
        "or MYCHATARCHIVE_RE_GPT_PATHS."
    )


def _load_re_gpt_modules(root: Path):
    original_sys_path = list(sys.path)
    try:
        root_str = str(root)
        if root_str not in sys.path:
            sys.path.insert(0, root_str)
        sync = importlib.import_module("re_gpt.sync_chatgpt")
        return sync.SyncChatGPT
    finally:
        sys.path[:] = original_sys_path


def _resolve_chatgpt_selector(chatgpt, selector: str) -> tuple[str, str | None, str]:
    normalized = normalize_live_selector(selector)
    if looks_like_online_thread_id(normalized):
        return normalized, None, "online_thread_id"

    target = normalized.lower()
    contains_match: tuple[str, str | None, str] | None = None
    offset = 0
    limit = 100
    while True:
        page = chatgpt.list_conversations_page(offset=offset, limit=limit)
        items = page.get("items", []) if isinstance(page, dict) else []
        if not items:
            break
        for item in items:
            title = str(item.get("title") or "").strip()
            conversation_id = str(item.get("id") or "").strip()
            if not title or not conversation_id:
                continue
            lowered = title.lower()
            if lowered == target:
                return conversation_id, title, "title_exact"
            if target in lowered and contains_match is None:
                contains_match = (conversation_id, title, "title_contains")
        offset += len(items)
        if len(items) < limit:
            break
    if contains_match:
        return contains_match
    raise RuntimeError(f"Unable to resolve live selector: {selector}")


def _convert_chat_payload(chat: dict[str, Any]) -> list[dict[str, Any]]:
    mapping = chat.get("mapping")
    title = str(chat.get("title") or "")
    conversation_id = str(chat.get("conversation_id") or chat.get("id") or "").strip()
    parsed: list[dict[str, Any]] = []
    if not isinstance(mapping, dict):
        return parsed

    for node_id, node in mapping.items():
        if not isinstance(node, dict):
            continue
        message = node.get("message")
        if not isinstance(message, dict):
            continue
        author_info = message.get("author") or {}
        role = str(author_info.get("role") or "")
        content_info = message.get("content") or {}
        raw_parts = content_info.get("parts") or []
        parts: list[str] = []
        if isinstance(raw_parts, list):
            for part in raw_parts:
                if isinstance(part, str) and part.strip():
                    parts.append(part.strip())
                elif isinstance(part, dict):
                    for key in ("text", "content", "title"):
                        value = part.get(key)
                        if value:
                            parts.append(str(value).strip())
                            break
        if not parts:
            continue
        created_at = message.get("create_time") or message.get("update_time") or 0
        try:
            created_at = float(created_at)
        except (TypeError, ValueError):
            created_at = 0.0
        parsed.append(
            {
                "thread_id": conversation_id,
                "thread_title": title,
                "role": role,
                "content": "\n".join(parts),
                "created_at": created_at,
                "source_message_id": str(message.get("id") or node_id or ""),
            }
        )

    parsed.sort(key=lambda item: item["created_at"])
    for idx, message in enumerate(parsed):
        if not message.get("source_message_id"):
            message["source_message_id"] = f"idx:{idx}"
    return parsed


def fetch_live_messages(provider: str, selector: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    provider_name = (provider or "chatgpt").strip().lower()
    if provider_name != "chatgpt":
        raise RuntimeError(f"Unsupported live provider: {provider}")

    root = resolve_live_provider_root()
    session_token = load_session_token()
    if not session_token:
        raise RuntimeError(
            "No ChatGPT session token found. Set CHATGPT_SESSION_TOKEN or create ~/.chatgpt_session."
        )

    SyncChatGPT = _load_re_gpt_modules(root)
    with SyncChatGPT(session_token=session_token) as chatgpt:
        conversation_id, title, provider_resolution = _resolve_chatgpt_selector(chatgpt, selector)
        chat = chatgpt.fetch_conversation(conversation_id)
    messages = _convert_chat_payload(chat)
    return messages, {
        "provider": provider_name,
        "provider_root": str(root),
        "selector": selector,
        "resolved_selector": conversation_id,
        "provider_resolution": provider_resolution,
        "title": title or chat.get("title"),
        "fetched_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
    }
