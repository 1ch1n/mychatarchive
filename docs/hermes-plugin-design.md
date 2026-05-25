# MyChatArchive Memory Provider Plugin for Hermes Agent

## Design Document

---

## 1. Source Material Summaries

### agent/memory_provider.py (the ABC)

- Defines `MemoryProvider` ABC with 6 core lifecycle methods: `name`, `is_available`, `initialize`, `get_tool_schemas`, `handle_tool_call`, `shutdown`.
- Provides optional hooks: `system_prompt_block`, `prefetch`, `queue_prefetch`, `sync_turn`, `on_turn_start`, `on_session_end`, `on_pre_compress`, `on_memory_write`, `on_delegation`, `on_session_switch`.
- Enforced one-external-provider limit via `MemoryManager` to prevent tool schema bloat.
- `initialize()` receives `session_id`, `hermes_home`, `platform`, and optional identity/workspace kwargs for profile scoping.
- `get_config_schema()` / `save_config()` power the `hermes memory setup` wizard; secrets route to `.env` via `env_var` field.

### plugins/memory/holographic/__init__.py (simplest reference)

- Local-only SQLite-backed fact store with entity resolution and HRR (Holographic Reduced Representation) retrieval.
- Exposes 2 tools: `fact_store` (add/search/probe/related/reason/contradict/update/remove/list) and `fact_feedback` (helpful/unhelpful trust scoring).
- `is_available()` always returns True (no external deps beyond SQLite/numpy).
- `sync_turn()` is a no-op; facts are stored exclusively via explicit tool calls. `on_session_end()` does optional regex-based auto-extraction.
- `on_memory_write()` mirrors built-in memory writes as facts, demonstrating the cross-provider bridge.

### plugins/memory/hindsight/__init__.py (closest in scope)

- External memory backend with cloud/local-embedded/local-external modes, knowledge graph, and multi-strategy retrieval.
- Exposes 3 tools: `hindsight_retain`, `hindsight_recall`, `hindsight_reflect` (synthesis).
- Heavy async machinery: shared event loop on a background thread, single-writer retain queue with sentinel-based shutdown, dedicated prefetch threads.
- `sync_turn()` auto-retains every N turns (configurable) by accumulating `_session_turns` and batch-posting to the Hindsight API with document_id scoping.
- Extensive config surface: 30+ fields in `get_config_schema()`, mode-dependent `when` clauses, `post_setup()` wizard with dependency installation.

### plugins/memory/honcho/__init__.py (most feature-complete)

- AI-native cross-session user modeling with dialectic Q&A, semantic search, peer cards, and persistent conclusions.
- Exposes 5 tools: `honcho_profile`, `honcho_search`, `honcho_reasoning`, `honcho_context`, `honcho_conclude`.
- Sophisticated prefetch with two layers: base context (representation + card, refreshed on `context_cadence`) and dialectic supplement (multi-pass `.chat()` calls with configurable depth/reasoning levels).
- Three recall modes: `hybrid` (auto-injection + tools), `context` (auto-injection only), `tools` (tools only, lazy session init).
- Cadence gating, empty-streak backoff, stale-thread recovery, trivial-prompt detection, and token budget enforcement.

### plugins/memory/honcho/cli.py (CLI surface)

- Full CLI subcommand tree: `hermes honcho setup|status|sessions|map|peer|mode|strategy|tokens|identity|migrate|enable|disable|sync`.
- `cmd_setup()` is an interactive wizard: deployment mode, API key, identity, observation mode, write frequency, recall mode, context tokens, dialectic cadence/reasoning, session strategy.
- `cmd_status()` shows full config and connection test; `--all` shows cross-profile overview.
- `cmd_migrate()` provides step-by-step OpenClaw-to-Honcho migration guide with auto-upload of USER.md/SOUL.md files.
- Profile-aware via `_host_key()` and `clone_honcho_for_profile()` for multi-profile Honcho config.

### tests/agent/test_memory_provider.py (testing patterns)

- `FakeMemoryProvider` is the canonical test double: implements all abstract methods, tracks calls via lists/booleans.
- `MemoryManager` tests cover: add/get provider, second-external-rejected, system prompt merging, prefetch merging, sync fan-out, tool schema collection, tool routing, lifecycle hooks, error resilience (one provider failing doesn't block others).
- Plugin discovery tests verify `discover_memory_providers()` and `load_memory_provider()` find bundled and user-installed providers, with bundled taking precedence on name conflict.
- `TestSequentialDispatchRouting` is a regression test for tool routing through `has_tool()` + `handle_tool_call()`.
- `TestOnMemoryWriteBridge` verifies the write-origin metadata bridge and backward compat with 3-arg providers.

---

## 2. Hermes Hooks: Day One vs. Deferred

### Day One (implement)

| Hook | Rationale |
|------|-----------|
| `name` | Required. Returns `"mychatarchive"`. |
| `is_available()` | Required. Check that `mychatarchive` package is importable and `~/.mychatarchive/archive.db` exists. No network calls. |
| `initialize(session_id, **kwargs)` | Required. Open DB connection, load embedder, store session metadata. |
| `get_tool_schemas()` | Required. Return schemas for the 4 tools. |
| `handle_tool_call()` | Required. Route to the 4 tool handlers. |
| `system_prompt_block()` | High value. Report archive stats (message count, thread count, date range) so the model knows what's available. |
| `prefetch(query)` | **Core value prop.** MCA *is* a retrieval system. Run `embed_single(query)` + `search_chunks()` and inject top-K results as context before each turn. This is why the plugin exists. |
| `shutdown()` | Required. Close the DB connection. |
| `get_config_schema()` / `save_config()` | Setup wizard integration. Fields: `db_path`, `prefetch_limit`, `recall_mode` (hybrid/tools/context). |

### Deferred (v2+)

| Hook | Why defer |
|------|-----------|
| `sync_turn()` | MCA's schema is import-oriented (messages come from external platforms with `source_id`, `platform`, etc.). Writing raw Hermes turns into `messages` would pollute the archive with a different data shape. The `thoughts` table (via `mca_remember`) is the correct write path. |
| `on_session_end()` | Could extract session summary into `thread_summaries`, but requires LLM call (OpenRouter) and schema alignment. Defer until sync_turn is solved. |
| `on_memory_write()` | Could mirror Hermes built-in memory writes as `thoughts`. Low priority since `mca_remember` covers explicit writes. |
| `on_pre_compress()` | Could preserve compressed context as a thought. Nice-to-have, not critical. |
| `on_delegation()` | Subagent observation. No clear MCA use case yet. |
| `queue_prefetch()` | Background pre-warming. Only needed if prefetch latency is a problem (unlikely for local SQLite + local embedder). |
| CLI surface (`cli.py`) | `hermes mychatarchive status|stats` would be nice but not blocking. |

---

## 3. Data Model Mapping: MCA -> Hermes

### MCA's model

MCA stores **imported** conversations from external platforms (ChatGPT, Claude, Cursor, Grok). The core entities:

- **messages**: Individual messages with `canonical_thread_id`, `platform`, `role`, `ts`, `text`
- **chunks**: Text chunks of messages, 1200-char windows with 150-char overlap, each with a 384-dim vector embedding
- **thoughts**: User-captured notes/insights, each with a vector embedding
- **thread_summaries**: LLM-generated summaries per thread (multi-segment for long threads)
- **thread_groups**: User-curated named collections of threads

### Hermes' model

Hermes has **sessions** (identified by `session_id`) and **turns** (user message + assistant response per turn).

### The mapping

MCA and Hermes are fundamentally different: MCA is a **read-heavy archive** of past conversations across platforms; Hermes is a **live agent** producing new conversations.

The plugin is therefore **asymmetric by design**:

- **Read path (dominant):** Hermes queries MCA's archive for context. A Hermes `session_id` has no structural equivalent in MCA — it's just a query-time filter hint, not a storage concept. The plugin searches across all MCA data regardless of session.
- **Write path (narrow):** Only explicit user-initiated writes via `mca_remember` → `thoughts` table. Hermes session_id is stored in the thought's `meta` JSON for provenance, not as a first-class field.
- **No auto-sync:** `sync_turn()` is a no-op. Hermes turns are not imported into MCA's `messages` table. If the user wants their Hermes conversations archived, they use `mychatarchive sync` after the session (which reads from Claude Code's JSONL logs, Cursor's DB, etc. via the existing parser pipeline).

This keeps the archive clean and the plugin simple.

---

## 4. Tool Design

### `mca_search`

**Purpose:** Semantic + keyword search across the entire archive.

**Parameters:**
- `query` (string, required): What to search for
- `mode` (string, optional): `"semantic"` (default), `"keyword"`, or `"hybrid"`
- `limit` (int, optional): Max results (default 10)
- `platform` (string, optional): Filter to one platform
- `group` (string, optional): Filter to a thread group
- `hours_back` (int, optional): Temporal filter

**MCA calls:**
- Semantic: `embeddings.embed_single(query)` -> `db.search_chunks(con, embedding, limit, platform, cutoff_iso, group_thread_ids)` -> `db.get_chunk_by_id()` for each result
- Keyword: `db.fts_search(con, query, limit, platform, cutoff_iso, group_thread_ids)`
- Hybrid: both, deduplicated and merged

**Returns:** JSON array of `{text, thread_id, platform, timestamp, title, score}`.

### `mca_recall`

**Purpose:** Rich contextual retrieval for a topic. Combines chunks, thread summaries, and thoughts — the equivalent of MCA's MCP `get_context` tool.

**Parameters:**
- `topic` (string, required): Topic to recall context about
- `limit` (int, optional): Max items per category (default 5)
- `platform` (string, optional): Platform filter
- `group` (string, optional): Group filter

**MCA calls:**
1. `embeddings.embed_single(topic)` -> `db.search_chunks()` for related messages
2. `db.search_thread_summaries(con, embedding)` for thread-level summaries, then `db.get_thread_summaries()` for full multi-segment content
3. `db.search_thoughts(con, embedding)` for related captured thoughts

**Returns:** JSON with `{messages: [...], summaries: [...], thoughts: [...]}` — a structured context bundle the model can reason over.

### `mca_remember`

**Purpose:** Capture a new thought/insight into the archive. Equivalent to MCA's MCP `capture_thought` tool.

**Parameters:**
- `thought` (string, required): The content to remember
- `tags` (string, optional): Comma-separated tags

**MCA calls:**
1. `embeddings.embed_single(thought)` to generate embedding
2. `db.insert_thought(con, thought_id, text, created_at, embedding, meta)` where `meta` includes `{"source": "hermes", "session_id": "...", "tags": [...]}`

**Returns:** JSON `{thought_id, created_at, status: "saved"}`.

### `mca_provenance`

**Purpose:** Given a chunk or thought ID (returned from search/recall results), retrieve the full source context: which thread it came from, the surrounding messages, timestamps, platform.

**Parameters:**
- `chunk_id` (string, optional): A chunk ID to look up
- `thought_id` (string, optional): A thought ID to look up
- Exactly one must be provided.

**MCA calls:**
- For chunks: `db.get_chunk_by_id(con, chunk_id)` -> returns `(text, thread_id, ts_start, ts_end, meta)`, then `db.get_thread_summary(con, thread_id)` for the thread's title and summary
- For thoughts: `db.get_thought_by_id(con, thought_id)` -> returns `(text, created_at, meta)`

**Returns:** JSON with full provenance: `{text, thread_id, thread_title, platform, ts_start, ts_end, thread_summary, meta}`.

---

## 5. Connection & Auth Model

### Decision: Direct Python import, shared SQLite file

The plugin imports `mychatarchive` as a Python package and opens `~/.mychatarchive/archive.db` directly via `mychatarchive.db.get_connection()`.

**Why not MCP-as-subprocess?**
- Adds ~2s startup latency per session (Python interpreter + model loading)
- Requires managing a child process lifecycle
- MCP's stdio transport is designed for IDE integration, not in-process library calls
- No benefit: both Hermes and MCA run locally on the same machine

**Why not HTTP to localhost?**
- MCA doesn't have a persistent HTTP server (SSE transport exists but is optional/experimental)
- Would require the user to run a separate daemon
- SQLite handles concurrent readers fine; the plugin only reads (writes are rare `insert_thought` calls)

**Why not direct DB read (bypassing the package)?**
- Tempting but fragile: schema changes in MCA would break the plugin silently
- The `db.py` facade provides stable function signatures
- Embedding queries require the same model/dimension as stored vectors; `embeddings.embed_single()` handles this

### Hard dependency

The plugin MUST use the same embedding model and dimension as the archive's stored vectors. MCA defaults to `sentence-transformers/all-MiniLM-L6-v2` (384-dim, cosine distance). The plugin reads `mychatarchive.config.get_embedding_model()` and `get_embedding_dim()` at `initialize()` time rather than hardcoding, so it tracks any user config changes.

### Profile scoping

The Hermes dev guide says to use `hermes_home` for storage paths. This plugin deliberately departs from that convention: MCA's archive lives at a stable user-wide path (`~/.mychatarchive/archive.db`) because it predates Hermes and is consumed by other tools (Claude Desktop via MCP, the `mychatarchive` CLI, Cursor). The archive is shared across all Hermes profiles, not profile-scoped. The `db_path` config field allows overriding if needed.

### Installation

The `mychatarchive` package must be pip-installable into Hermes' Python environment:

```
pip install git+https://github.com/1ch1n/mychatarchive
# or for local development:
pip install -e /path/to/mychatarchive
```

The plugin's `is_available()` checks `importlib.import_module("mychatarchive")` and `Path(db_path).exists()`.

### Config schema

```python
get_config_schema() -> [
    {"key": "db_path", "description": "Path to MCA database",
     "default": "~/.mychatarchive/archive.db"},
    {"key": "recall_mode", "description": "Memory integration mode",
     "default": "hybrid", "choices": ["hybrid", "context", "tools"]},
    {"key": "prefetch_limit", "description": "Max chunks injected per turn",
     "default": "5"},
]
```

No secrets needed. No API keys. Purely local.

---

## 6. Plugin File Layout

```
plugins/memory/mychatarchive/
  __init__.py     # MyChatArchiveMemoryProvider + register()
  plugin.yaml     # name, description, hooks
  README.md       # setup instructions
```

Optionally later:
```
  cli.py          # hermes mychatarchive status|stats
```

---

## 7. Open Questions

1. **Embedding model warm-up:** `sentence-transformers` first load takes ~3s. Should `initialize()` pre-load in a background thread, or accept the latency on first `prefetch()`?

2. **Prefetch budget:** How many chunks to inject per turn? Too few misses context; too many burns tokens. Start with 5, make configurable. Hindsight uses 4096 tokens; Honcho uses `context_tokens` config.

3. **Group-aware prefetch:** Should `prefetch()` respect a configured default group filter, or always search the full archive? Leaning toward full archive with group filtering reserved for explicit tool calls.

4. **Thread summary in prefetch:** Should prefetch also search `vec_thread_summaries` alongside `vec_chunks`? Thread summaries are higher-signal but lower-granularity. Could be a v2 enhancement.

5. **Hermes turn ingestion (v2):** If we eventually want `sync_turn()`, the cleanest path is: write Hermes turns to `thoughts` with `source=hermes` metadata, not to `messages`. This avoids schema mismatch and keeps the messages table clean for platform imports.
