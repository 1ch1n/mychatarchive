"""Parser registry and auto-detection."""

import json
from pathlib import Path
from typing import Iterator

from mychatarchive.parsers import chatgpt, anthropic, grok, claude_code, cursor

PARSERS = {
    "chatgpt": chatgpt,
    "anthropic": anthropic,
    "grok": grok,
    "claude_code": claude_code,
    "cursor": cursor,
}

DIRECTORY_PARSERS = {"claude_code", "cursor"}


def detect_format(file_path: Path) -> str | None:
    """Auto-detect export format by inspecting the JSON structure.

    For file-based exports (ChatGPT, Anthropic, Grok), inspects the JSON.
    For directory-based sources (Claude Code, Cursor), use --format explicitly
    or pass "auto" as the path.
    """
    p = Path(file_path)

    if p.is_dir():
        if (p / "projects").is_dir() or p.name == ".claude":
            return "claude_code"
        if (p / "globalStorage" / "state.vscdb").exists():
            return "cursor"
        return None

    if p.suffix == ".jsonl":
        with open(p, "r", encoding="utf-8", errors="ignore") as f:
            first_line = f.readline().strip()
        if first_line:
            try:
                record = json.loads(first_line)
                if isinstance(record, dict) and record.get("type") in ("user", "assistant", "file-history-snapshot"):
                    return "claude_code"
            except json.JSONDecodeError:
                pass

    if p.name == "state.vscdb":
        return "cursor"

    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        head = f.read(8192)

    stripped = head.lstrip()

    if stripped.startswith("["):
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                data = json.load(f)
            if not isinstance(data, list) or len(data) == 0:
                return None
            first = data[0]
        except (json.JSONDecodeError, IndexError):
            return None
    elif stripped.startswith("{"):
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                first = json.load(f)
        except json.JSONDecodeError:
            return None
    else:
        return None

    if not isinstance(first, dict):
        return None

    if "mapping" in first and "title" in first:
        return "chatgpt"
    if "chat_messages" in first and "uuid" in first:
        return "anthropic"
    if "conversations" in first:
        peek = first["conversations"]
        if isinstance(peek, list) and len(peek) > 0:
            item = peek[0]
            if isinstance(item, dict) and "responses" in item:
                return "grok"
    if "conversation" in first and "responses" in first:
        return "grok"
    if "messages" in first or ("id" in first and "text" in first):
        return "grok"

    return None


def parse(file_path: Path, format_name: str | None = None) -> Iterator[dict]:
    """Parse an export file, auto-detecting format if not specified."""
    if format_name is None:
        format_name = detect_format(file_path)
        if format_name is None:
            raise ValueError(
                f"Could not auto-detect format for {file_path}. "
                f"Use --format with one of: {', '.join(PARSERS.keys())}"
            )

    if format_name not in PARSERS:
        raise ValueError(f"Unknown format '{format_name}'. Options: {', '.join(PARSERS.keys())}")

    yield from PARSERS[format_name].parse(str(file_path))
