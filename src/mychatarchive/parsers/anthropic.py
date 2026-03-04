"""Parser for Anthropic Claude conversation exports."""

import json
from datetime import datetime
from typing import Iterator


def parse(input_path: str) -> Iterator[dict]:
    with open(input_path, "r", encoding="utf-8", errors="ignore") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError("Anthropic export should be a JSON array")

    for convo in data:
        yield from _parse_conversation(convo)


def _parse_conversation(convo: dict) -> Iterator[dict]:
    thread_id = convo.get("uuid", "")
    title = convo.get("name", "")
    chat_messages = convo.get("chat_messages", [])

    for msg in chat_messages:
        sender = msg.get("sender", "").lower()
        role = "user" if sender == "human" else sender if sender else "unknown"

        content_list = msg.get("content", [])
        text = msg.get("text", "")

        if isinstance(content_list, list) and content_list:
            content = "\n".join(
                item.get("text", "")
                for item in content_list
                if isinstance(item, dict) and item.get("type") == "text"
            )
        else:
            content = text

        created_at_str = msg.get("created_at", "")
        try:
            dt = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
            created_at = dt.timestamp()
        except (ValueError, TypeError, AttributeError):
            created_at = 0.0

        yield {
            "thread_id": thread_id,
            "thread_title": title,
            "role": role,
            "content": content,
            "created_at": created_at,
        }
