"""Parser for Grok (X.AI) conversation exports.

Grok exports use this structure:
{
  "conversations": [
    {
      "conversation": { "id": ..., "title": ..., "create_time": ... },
      "responses": [
        { "response": { "message": ..., "sender": ..., "create_time": {"$date": {"$numberLong": "..."}} } }
      ]
    }
  ]
}
"""

import json
from datetime import datetime
from typing import Iterator


def parse(input_path: str) -> Iterator[dict]:
    with open(input_path, "r", encoding="utf-8", errors="ignore") as f:
        data = json.load(f)

    if isinstance(data, dict) and "conversations" in data:
        for item in data["conversations"]:
            yield from _parse_wrapped_conversation(item)
    elif isinstance(data, list):
        for item in data:
            if "conversation" in item and "responses" in item:
                yield from _parse_wrapped_conversation(item)
            else:
                yield from _parse_flat_conversation(item)
    elif isinstance(data, dict):
        if "conversation" in data and "responses" in data:
            yield from _parse_wrapped_conversation(data)
        else:
            yield from _parse_flat_conversation(data)


def _parse_wrapped_conversation(item: dict) -> Iterator[dict]:
    """Parse Grok's wrapped format: {conversation: {...}, responses: [...]}."""
    convo = item.get("conversation", {})
    thread_id = convo.get("id", "")
    title = convo.get("title", "")

    for resp_wrapper in item.get("responses", []):
        resp = resp_wrapper.get("response", resp_wrapper)
        message = resp.get("message", "")
        sender = resp.get("sender", "unknown")
        role = "user" if sender == "human" else sender

        created_at = _extract_timestamp(resp.get("create_time"))
        if created_at is None:
            continue

        yield {
            "thread_id": thread_id,
            "thread_title": title,
            "role": role,
            "content": message if isinstance(message, str) else str(message),
            "created_at": created_at,
        }


def _parse_flat_conversation(convo: dict) -> Iterator[dict]:
    """Parse a simpler flat format as fallback."""
    thread_id = convo.get("id") or convo.get("conversationId", "")
    title = convo.get("title") or convo.get("name", "")
    messages = convo.get("messages", [])

    for msg in messages:
        role = msg.get("role") or msg.get("sender", "unknown")
        content = msg.get("content") or msg.get("text", "")
        created_at = _extract_timestamp(msg.get("created_at") or msg.get("timestamp") or msg.get("createdAt"))
        if created_at is None:
            continue

        yield {
            "thread_id": thread_id,
            "thread_title": title,
            "role": role,
            "content": content if isinstance(content, str) else str(content),
            "created_at": created_at,
        }


def _extract_timestamp(ts) -> float | None:
    """Handle Grok's various timestamp formats."""
    if ts is None:
        return None

    # MongoDB-style: {"$date": {"$numberLong": "1737381600000"}}
    if isinstance(ts, dict):
        date_val = ts.get("$date")
        if isinstance(date_val, dict):
            number_long = date_val.get("$numberLong")
            if number_long:
                return float(number_long) / 1000.0
        if isinstance(date_val, str):
            try:
                dt = datetime.fromisoformat(date_val.replace("Z", "+00:00"))
                return dt.timestamp()
            except (ValueError, TypeError):
                pass
        return None

    # ISO string
    if isinstance(ts, str):
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return dt.timestamp()
        except (ValueError, TypeError):
            pass
        try:
            return float(ts)
        except (ValueError, TypeError):
            return None

    # Numeric
    try:
        val = float(ts)
        if val > 1e12:
            return val / 1000.0
        return val
    except (ValueError, TypeError):
        return None
