"""Parser for ChatGPT conversation exports (conversations.json)."""

import json
from typing import Iterator

import ijson


def extract_text_from_content(content) -> str:
    if not isinstance(content, dict):
        return ""
    parts = content.get("parts", [])
    if not isinstance(parts, list):
        return ""
    return "\n".join(p for p in parts if isinstance(p, str) and p)


def parse(input_path: str) -> Iterator[dict]:
    with open(input_path, "r", encoding="utf-8", errors="ignore") as f:
        first = f.read(4096)
    start = first.lstrip()[:1]

    if start == "[":
        with open(input_path, "rb") as f:
            for convo in ijson.items(f, "item"):
                if isinstance(convo, dict):
                    yield from _parse_conversation(convo)
    elif start == "{":
        with open(input_path, "r", encoding="utf-8", errors="ignore") as f:
            obj = json.load(f)
        if isinstance(obj, dict):
            yield from _parse_conversation(obj)
    else:
        raise ValueError(f"Unexpected JSON start character in {input_path}")


def _parse_conversation(convo: dict) -> Iterator[dict]:
    thread_id = convo.get("id") or convo.get("conversation_id", "")
    title = convo.get("title", "")
    mapping = convo.get("mapping", {})

    messages = []
    for node in mapping.values():
        m = node.get("message")
        if not m:
            continue

        role = (m.get("author") or {}).get("role") or ""
        content = extract_text_from_content(m.get("content") or {})
        ts = m.get("create_time") or m.get("update_time")
        if not ts:
            continue

        messages.append({
            "thread_id": thread_id,
            "thread_title": title,
            "role": role,
            "content": content,
            "created_at": float(ts),
            "source_message_id": m.get("id") or node_id,
        })

    messages.sort(key=lambda x: x["created_at"])
    yield from messages
