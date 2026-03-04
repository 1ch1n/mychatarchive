"""MCP server exposing MyChatArchive as tools for Claude Desktop, Cursor, and other MCP clients.

Tools: search_brain, search_recent, get_context, capture_thought
Transport: stdio (default) or sse (for remote access)

IMPORTANT: Never print() to stdout -- it corrupts the JSON-RPC stream.
Use sys.stderr or logging for any debug output.
"""

import datetime
import hashlib
import json
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from mychatarchive import db
from mychatarchive.config import get_db_path

mcp = FastMCP("mychatarchive")

_con = None


def _get_con():
    global _con
    if _con is None:
        db_path = get_db_path()
        if not db_path.exists():
            print(
                f"Database not found at {db_path}. Run 'mychatarchive import' first.",
                file=sys.stderr,
            )
            raise FileNotFoundError(f"No database at {db_path}")
        _con = db.get_connection(db_path)
    return _con


def _lazy_embed(text: str) -> list[float]:
    from mychatarchive.embeddings import embed_single
    return embed_single(text)


@mcp.tool()
def search_brain(
    query: str,
    limit: int = 10,
    platform: str | None = None,
) -> str:
    """Semantic search across all chat history by meaning.

    Finds messages most similar to the query using vector embeddings.
    Returns relevant messages with metadata (thread, timestamp, role).

    Args:
        query: What to search for -- use natural language
        limit: Maximum number of results (default 10)
        platform: Filter by platform (chatgpt, anthropic, grok, claude_code, cursor).
            Omit to search all. Comma-separated for multiple: "chatgpt,anthropic,grok".
    """
    con = _get_con()
    embedding = _lazy_embed(query)
    platforms = [p.strip() for p in platform.split(",")] if platform else None
    results = db.search_chunks(con, embedding, limit=limit, platform=platforms)

    if not results:
        return json.dumps({"query": query, "count": 0, "results": []})

    output = []
    for chunk_id, distance in results:
        row = db.get_chunk_by_id(con, chunk_id)
        if row:
            meta = json.loads(row[4]) if row[4] else {}
            output.append({
                "text": row[0][:1000],
                "thread_id": row[1],
                "timestamp": row[2],
                "role": meta.get("role", ""),
                "title": meta.get("title", ""),
                "similarity": round(1.0 - distance, 4),
            })

    return json.dumps({"query": query, "count": len(output), "results": output}, indent=2)


@mcp.tool()
def search_recent(
    hours: int = 24,
    limit: int = 20,
    platform: str | None = None,
) -> str:
    """Retrieve recent conversations and captured thoughts by time range.

    Args:
        hours: How many hours back to look (default 24)
        limit: Maximum results per category (default 20)
        platform: Filter by platform (chatgpt, anthropic, grok, claude_code, cursor).
            Comma-separated for multiple. Omit for all.
    """
    con = _get_con()
    cutoff = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours)
    ).isoformat()
    platforms = [p.strip() for p in platform.split(",")] if platform else None

    chunk_rows = db.get_recent_chunks(con, cutoff, limit, platform=platforms)
    thought_rows = db.get_recent_thoughts(con, cutoff, limit)

    messages = []
    for row in chunk_rows:
        meta = json.loads(row[4]) if row[4] else {}
        messages.append({
            "text": row[1][:500],
            "thread_id": row[2],
            "timestamp": row[3],
            "role": meta.get("role", ""),
            "title": meta.get("title", ""),
        })

    thoughts = []
    for row in thought_rows:
        thoughts.append({"text": row[1], "created_at": row[2]})

    return json.dumps(
        {"hours": hours, "messages": messages, "thoughts": thoughts},
        indent=2,
    )


@mcp.tool()
def get_context(
    topic: str,
    limit: int = 10,
    platform: str | None = None,
) -> str:
    """Given a topic, return a comprehensive context bundle.

    Gathers related conversations, captured thoughts, and thread summaries
    to provide full context about a subject from your chat history.

    Args:
        topic: The topic to gather context about
        limit: Maximum results per category (default 10)
        platform: Filter by platform (chatgpt, anthropic, grok, claude_code, cursor).
            Comma-separated for multiple. Omit for all.
    """
    con = _get_con()
    embedding = _lazy_embed(topic)
    platforms = [p.strip() for p in platform.split(",")] if platform else None

    chunk_results = db.search_chunks(con, embedding, limit=limit, platform=platforms)
    thought_results = db.search_thoughts(con, embedding, limit=5)

    related_messages = []
    thread_ids = set()
    for chunk_id, distance in chunk_results:
        row = db.get_chunk_by_id(con, chunk_id)
        if row:
            meta = json.loads(row[4]) if row[4] else {}
            related_messages.append({
                "text": row[0][:500],
                "thread_id": row[1],
                "timestamp": row[2],
                "role": meta.get("role", ""),
                "title": meta.get("title", ""),
                "similarity": round(1.0 - distance, 4),
            })
            thread_ids.add(row[1])

    related_thoughts = []
    for thought_id, distance in thought_results:
        row = db.get_thought_by_id(con, thought_id)
        if row:
            related_thoughts.append({
                "text": row[0],
                "created_at": row[1],
                "similarity": round(1.0 - distance, 4),
            })

    return json.dumps({
        "topic": topic,
        "related_messages": related_messages,
        "related_thoughts": related_thoughts,
        "unique_threads": len(thread_ids),
    }, indent=2)


@mcp.tool()
def capture_thought(thought: str, tags: str = "") -> str:
    """Capture a new thought or note into your archive with auto-embedding.

    The thought is stored and embedded so it's retrievable via search_brain later.
    Use this to save insights, decisions, ideas, or anything worth remembering.

    Args:
        thought: The thought or note to capture
        tags: Optional comma-separated tags for organization
    """
    con = _get_con()
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    thought_id = hashlib.sha1(f"{now}|{thought[:64]}".encode()).hexdigest()

    embedding = _lazy_embed(thought)
    meta = {"tags": [t.strip() for t in tags.split(",") if t.strip()]} if tags else None

    db.insert_thought(con, thought_id, thought, now, embedding, meta)
    con.commit()

    return json.dumps({
        "status": "captured",
        "thought_id": thought_id,
        "created_at": now,
        "preview": thought[:200],
    }, indent=2)


def run(db_path: Path | None = None, transport: str = "stdio", port: int = 8420):
    """Start the MCP server."""
    if db_path:
        global _con
        _con = db.get_connection(db_path)

    if transport == "sse":
        print(f"Starting MCP server with SSE transport on port {port}", file=sys.stderr)
        mcp.run(transport="sse", port=port)
    else:
        mcp.run(transport="stdio")
