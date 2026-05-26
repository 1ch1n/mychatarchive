# PR: MyChatArchive Memory Provider Plugin for Hermes Agent

## Problem

Hermes Agent's memory providers (Honcho, Hindsight, Holographic, etc.) all
start from scratch or rely on external cloud services. Users who have years
of AI conversation history across ChatGPT, Claude, Cursor, and Grok have no
way to make that existing context available to Hermes as persistent memory.
MyChatArchive already stores and indexes this history locally, but there is
no bridge between the two systems.

## What this plugin does

A Hermes `MemoryProvider` plugin that gives Hermes Agent read access to a
local MyChatArchive database. Purely local, no API keys, no cloud dependency.

**Four tools exposed to the model:**

| Tool | What it does |
|------|-------------|
| `mca_search` | Semantic (vector), keyword (FTS5), or hybrid search across the archive |
| `mca_recall` | Multi-layer context retrieval: message chunks + thread summaries + thoughts |
| `mca_remember` | Capture a new thought/insight with embedding for future retrieval |
| `mca_provenance` | Trace a search result back to its source thread, platform, and timestamp |

**Three automatic hooks:**

- `system_prompt_block` -- injects archive stats (message count, platforms, date range)
- `prefetch` -- auto-injects top-K semantically similar chunks before each turn
- `sync_turn` -- non-blocking daemon thread (no-op body, ready for v2 expansion)

**Three recall modes:** hybrid (auto-injection + tools), context (auto-injection only), tools (tools only).

**CLI subcommands:** `hermes mychatarchive status`, `config`, `import`.

## How to test locally

### Prerequisites

- Hermes Agent v0.14.0+
- Python 3.10+
- A populated MyChatArchive database (`mychatarchive sync && mychatarchive embed`)

### Install

```bash
# Install mychatarchive into the Hermes venv
uv pip install --python /path/to/hermes/venv/python \
    git+https://github.com/1ch1n/mychatarchive

# Copy the plugin
cp -r hermes-plugin/plugins/memory/mychatarchive/ \
    /path/to/hermes-agent/plugins/memory/mychatarchive/
```

### Activate

```bash
hermes memory setup
# Select "mychatarchive" from the provider list
```

Or manually in `$HERMES_HOME/config.yaml`:

```yaml
memory:
  provider: mychatarchive
```

If using platform gateways (Telegram, Discord, etc.), add `memory` to
the platform's toolset in `config.yaml`:

```yaml
platform_toolsets:
  telegram:
  - hermes-telegram
  - memory
```

### Verify

```bash
hermes mychatarchive status
```

Should show connection OK, message count, platform breakdown.

### Test the tools

Start a Hermes session and ask:

```
What have I discussed about [topic] in my past conversations?
```

The model should call `mca_recall` and/or `mca_search` (visible in the tool
call log), not shell out to `sqlite3`.

### Run unit tests

```bash
pytest tests/test_mychatarchive_plugin.py -v
```

48 tests, all passing.

## Architecture decisions

- **Direct Python import, not MCP:** The plugin imports `mychatarchive` as a
  package and opens the SQLite DB directly. No subprocess, no HTTP server,
  no MCP. Avoids ~2s startup latency and process management overhead.

- **Read-heavy, narrow writes:** The archive is import-oriented (messages from
  external platforms). Hermes turns are NOT auto-synced into the `messages`
  table. Writes only go to the `thoughts` table via explicit `mca_remember`.

- **User-wide DB, profile-scoped config:** The archive lives at a stable
  user-wide path (default `~/.mychatarchive/archive.db` or configurable,
  including UNC/NAS paths). Plugin config (`mychatarchive.json`) is stored
  under `$HERMES_HOME` per Hermes convention.

- **Embedding model coupling:** The plugin must use the same embedding model
  as the stored vectors. MyChatArchive defaults to
  `sentence-transformers/all-MiniLM-L6-v2` (384-dim, cosine distance).

## Files

```
hermes-plugin/plugins/memory/mychatarchive/
    __init__.py       # MyChatArchiveProvider (805 lines)
    cli.py            # hermes mychatarchive status|config|import
    plugin.yaml       # metadata + hooks list
    README.md         # setup + config + tool reference

tests/
    test_mychatarchive_plugin.py   # 48 test cases

demo/
    record-demo.sh          # demo recording script
    demo-transcript.md      # sanitized demo transcript

docs/
    hermes-plugin-design.md       # design doc (pre-implementation)
    PR-description.md             # this file
```

## Built against

- Hermes Agent v0.14.0
- MyChatArchive v0.1.0
- Python 3.10+
- Tested end-to-end via Telegram gateway
