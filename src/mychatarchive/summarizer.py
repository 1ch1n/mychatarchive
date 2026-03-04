"""Thread summarization pipeline.

Reads threads from the archive, generates LLM summaries, and stores them in
thread_summaries. Optionally embeds summaries for thread-level semantic search.

Pipeline slot: after Sync, before Chunk + Embed.
    mychatarchive sync
    mychatarchive summarize          <-- this module
    mychatarchive embed

Usage:
    mychatarchive summarize [--model MODEL] [--force] [--limit N]
    mychatarchive summarize --base-url https://openrouter.ai/api/v1 --key sk-or-...

The LLM API must be OpenAI-compatible (OpenRouter, Anthropic-compatible, Ollama, etc.).
Defaults: OpenRouter, anthropic/claude-haiku-4-5 (fast, cheap, good summarizer).

Resolution order (CLI overrides config overrides env over default):
  - Model:    --model  > config.json summarize.model  > anthropic/claude-haiku-4-5
  - Base URL: --base-url > config.json summarize.base_url > https://openrouter.ai/api/v1
  - API key:  --key > OPENROUTER_API_KEY > ANTHROPIC_API_KEY > OPENAI_API_KEY > config.json summarize.api_key
"""

from __future__ import annotations

import datetime
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

try:
    from tqdm import tqdm
    _HAS_TQDM = True
except ImportError:
    _HAS_TQDM = False

_DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
_DEFAULT_MODEL = "anthropic/claude-haiku-4-5"
_MAX_CONTEXT_CHARS = 6000   # ~1500 tokens; keeps API cost low
_BATCH_COMMIT = 20          # commit every N summaries

_SYSTEM_PROMPT = """\
You are summarizing an AI conversation for personal archival and retrieval.
Given a thread title and its messages, return a JSON object with exactly two keys:
  "summary"    – 2-4 sentence description of the main topics, decisions, and outcomes.
                 Be specific: name tools, projects, decisions, and conclusions discussed.
  "key_topics" – array of 3-8 keyword strings (project names, tools, concepts, people, places).
                 Use specific terms, not generic ones like "AI" or "chat".

Return ONLY valid JSON. No markdown fences, no explanation."""


def _resolve_api_key(cli_key: str | None) -> str:
    """Return API key from CLI flag, env vars, or config."""
    if cli_key:
        return cli_key
    for env in ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        v = os.environ.get(env)
        if v:
            return v
    # Check config
    try:
        from mychatarchive.config import load_config
        cfg = load_config()
        key = cfg.get("summarize", {}).get("api_key")
        if key:
            return key
    except Exception:
        pass
    return ""


def _format_thread(thread_meta: dict, messages: list[dict]) -> str:
    """Format thread messages into a single string, truncated to _MAX_CONTEXT_CHARS."""
    title = thread_meta.get("title") or "Untitled"
    parts = [f"Thread: {title}\n---"]
    chars = len(parts[0])
    for msg in messages:
        role = msg.get("role", "?")
        text = (msg.get("text") or "").strip()
        if not text:
            continue
        line = f"\n[{role}]: {text}"
        if chars + len(line) > _MAX_CONTEXT_CHARS:
            parts.append(f"\n[... {len(messages)} messages total, truncated ...]")
            break
        parts.append(line)
        chars += len(line)
    return "".join(parts)


def _call_api(prompt: str, api_key: str, base_url: str, model: str) -> dict:
    """Call an OpenAI-compatible chat completions endpoint. Returns parsed JSON response."""
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 400,
        "temperature": 0.2,
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("HTTP-Referer", "https://mychatarchive.com")
    req.add_header("X-Title", "MyChatArchive Summarizer")

    with urllib.request.urlopen(req, timeout=45) as resp:
        return json.loads(resp.read())


def _parse_response(raw: dict) -> tuple[str, list[str]]:
    """Extract (summary, key_topics) from the API response."""
    text = raw["choices"][0]["message"]["content"].strip()
    # Strip markdown fences if model added them anyway
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.rstrip("`").strip()
    parsed = json.loads(text)
    summary = str(parsed.get("summary", "")).strip()
    topics = [str(t) for t in parsed.get("key_topics", []) if t]
    return summary, topics


def run(
    db_path: Path,
    model: str = _DEFAULT_MODEL,
    base_url: str = _DEFAULT_BASE_URL,
    api_key: str = "",
    force: bool = False,
    limit: int | None = None,
    embed_summaries: bool = True,
) -> dict:
    """Generate LLM summaries for all unsummarized threads.

    Args:
        db_path:         Path to archive.db
        model:           LLM model identifier (OpenAI-compatible name)
        base_url:        API base URL (default: OpenRouter)
        api_key:         API key (falls back to env vars if empty)
        force:           Re-summarize already-summarized threads
        limit:           Max threads to process in this run
        embed_summaries: Also embed summaries for thread-level semantic search

    Returns:
        {"processed": N, "skipped": N, "errors": N, "total_threads": N}
    """
    from mychatarchive import db

    if not db_path.exists():
        raise FileNotFoundError(
            f"Archive not found at {db_path}. Run 'mychatarchive import' first."
        )

    api_key = api_key or _resolve_api_key(None)
    if not api_key:
        raise ValueError(
            "No API key found. Set OPENROUTER_API_KEY, or pass --key.\n"
            "  export OPENROUTER_API_KEY=sk-or-..."
        )

    con = db.get_connection(db_path)
    db.ensure_schema(con)

    now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()

    # Collect threads to process
    threads = list(db.iter_threads(con))
    total = len(threads)
    print(f"  [summarize] {total:,} threads in archive", file=sys.stderr)

    if not force:
        threads = [t for t in threads if not db.has_thread_summary(con, t["canonical_thread_id"])]
        print(f"  [summarize] {len(threads):,} without summaries", file=sys.stderr)
    else:
        print(f"  [summarize] --force: re-summarizing all", file=sys.stderr)

    if limit:
        threads = threads[:limit]

    if not threads:
        print("  [summarize] Nothing to do.", file=sys.stderr)
        con.close()
        return {"processed": 0, "skipped": total - 0, "errors": 0, "total_threads": total}

    print(f"  [summarize] Processing {len(threads):,} threads with {model}", file=sys.stderr)

    # Optionally embed summaries
    embedder = None
    if embed_summaries:
        try:
            from mychatarchive.embeddings import embed_single
            embedder = embed_single
        except Exception:
            print("  [summarize] Warning: embeddings not available, skipping summary embeddings.",
                  file=sys.stderr)

    processed = errors = 0
    iterator = tqdm(threads, desc="Summarizing", unit="thread") if _HAS_TQDM else threads

    for i, thread_meta in enumerate(iterator):
        thread_id = thread_meta["canonical_thread_id"]
        try:
            messages = db.get_thread_messages(con, thread_id)
            if not messages:
                continue

            prompt = _format_thread(thread_meta, messages)
            raw = _call_api(prompt, api_key, base_url, model)
            summary, key_topics = _parse_response(raw)

            if not summary:
                raise ValueError("Empty summary returned")

            db.insert_thread_summary(
                con,
                canonical_thread_id=thread_id,
                title=thread_meta.get("title"),
                platform=thread_meta.get("platform"),
                message_count=thread_meta.get("message_count", len(messages)),
                ts_start=thread_meta.get("ts_start"),
                ts_end=thread_meta.get("ts_end"),
                summary=summary,
                key_topics=key_topics,
                summary_model=model,
                now=now_str,
            )

            if embedder:
                try:
                    emb = embedder(summary)
                    db.insert_thread_summary_embedding(con, thread_id, emb)
                except Exception as e:
                    print(f"  [summarize] Embedding failed for {thread_id[:8]}: {e}",
                          file=sys.stderr)

            processed += 1

            if processed % _BATCH_COMMIT == 0:
                con.commit()

        except urllib.error.HTTPError as e:
            errors += 1
            body = e.read().decode(errors="replace")[:200] if e.fp else ""
            print(f"  [summarize] HTTP {e.code} for {thread_id[:8]}: {body}", file=sys.stderr)
        except Exception as e:
            errors += 1
            print(f"  [summarize] Error on {thread_id[:8]}: {e}", file=sys.stderr)

    con.commit()
    con.close()

    skipped = total - len(threads)
    print(
        f"  [summarize] Done: {processed:,} processed, {skipped:,} skipped, {errors} errors",
        file=sys.stderr,
    )
    return {"processed": processed, "skipped": skipped, "errors": errors, "total_threads": total}
