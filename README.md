# MyChatArchive

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)

**Your AI conversations, searchable by meaning.**

MyChatArchive turns your exported AI chat history into a local semantic memory that any AI tool can search. Import conversations from ChatGPT, Claude, Grok, Claude Code, and Cursor. Generate vector embeddings locally. Connect via MCP to Claude Desktop, Cursor, or any compatible client.

No cloud. No API keys. Everything runs on your machine by default - with optionality for cloud backends when you're ready.

---

## Quick Start

```bash
pip install .

# 1. Set up (creates drop folder, configures auto-discovery)
mychatarchive init

# 2. Drop your chat exports into the drop folder
#    Default: ~/.mychatarchive/imports/
#    Supports: ChatGPT, Claude, Grok JSON exports - auto-detected

# 3. Sync everything with one command
mychatarchive sync

# This automatically:
#  - Scans Claude Code sessions (~/.claude/)
#  - Scans Cursor conversations
#  - Imports any files in your drop folder
#  - Pulls from any named sources you've configured
#  - Deduplicates everything

# 4. Generate embeddings (one-time, runs locally)
mychatarchive embed

# 5. Start the MCP server
mychatarchive serve
```

Then connect from Claude Desktop, Cursor, or any MCP client. That's it.

## What You Get

Once running, any MCP-connected AI tool can:

| Tool | What it does |
|------|-------------|
| `search_brain` | Semantic search - find messages by *meaning*, not just keywords |
| `search_recent` | Get recent conversations and thoughts by time range |
| `get_context` | Given a topic, get a full context bundle (related threads, decisions, thoughts) |
| `capture_thought` | Save a new thought with auto-embedding for future retrieval |

**Example:** Ask Claude "What did I decide about the database architecture last month?" and it searches your actual conversation history semantically.

## Installation

### From source (recommended for now)

```bash
git clone https://github.com/1ch1n/mychatarchive.git
cd mychatarchive
pip install .
```

### Development install

```bash
pip install -e ".[dev]"
```

### Requirements

- Python 3.10+
- ~500MB disk for the embedding model (downloaded once)
- No API keys needed

## Usage

### Interactive setup

```bash
mychatarchive init
```

Walks you through configuring:
- **Drop folder** - where you place raw export files (default: `~/.mychatarchive/imports/`)
- **Auto-discovery** - toggle Claude Code and Cursor auto-import on/off
- **Storage, embeddings, transport** - defaults to local/zero-config if you just press Enter

### Sync (the one command you need)

```bash
mychatarchive sync           # import from all sources
mychatarchive sync --embed   # import + generate embeddings in one shot
```

This single command:
1. **Auto-discovers** Claude Code sessions from `~/.claude/projects/`
2. **Auto-discovers** Cursor conversations from local databases
3. **Scans the drop folder** for any JSON/JSONL files (auto-detects format)
4. **Pulls from named sources** (NAS shares, custom folders, etc.)
5. **Deduplicates** everything against what's already in the archive

Run it daily, weekly, whenever - dedup means it's always safe to re-run.

### Drop folder

The universal import method. Works on any machine, with any platform's exports.

1. Run `mychatarchive init` (creates the folder)
2. Drop export files into `~/.mychatarchive/imports/` (or wherever you configured it)
3. Run `mychatarchive sync`

Supports ChatGPT `conversations.json`, Claude exports, Grok exports - format is auto-detected. Subdirectories are scanned recursively.

### Manual imports

For one-off files or when you don't want to use the drop folder:

```bash
# Single file (auto-detects format)
mychatarchive import conversations.json

# Entire folder (recursive)
mychatarchive import ./exports/

# Force a specific format
mychatarchive import weird_file.json --format chatgpt
```

### Named sources

Set up persistent locations you pull from regularly - a NAS share, a downloads folder, etc.

```bash
# Add sources
mychatarchive sources add nas "\\\\server.local\\share\\exports"
mychatarchive sources add exports "~/Downloads/ai-exports" --format chatgpt
mychatarchive sources add work "D:\\work-chats" --account work

# See everything (auto-sources + drop folder + named sources)
mychatarchive sources list

# Import from a specific source
mychatarchive import --from nas

# Manage sources
mychatarchive sources rename nas home-nas
mychatarchive sources remove old-source
```

Named sources are included automatically when you run `mychatarchive sync`.

### Generate embeddings

```bash
mychatarchive embed              # embed new messages only
mychatarchive embed --force      # re-embed everything
```

Uses `all-MiniLM-L6-v2` (384 dimensions) locally via sentence-transformers. No data leaves your machine.

### Search from the command line

```bash
mychatarchive search "database architecture decisions"
mychatarchive search "what did I build last week" --limit 20
mychatarchive search "python error handling" --mode keyword
```

### Export your archive

Portable exports in three formats - take your data anywhere.

```bash
# JSON (full structured export, default)
mychatarchive export my_archive.json

# CSV (spreadsheet-friendly)
mychatarchive export my_archive.csv

# SQLite copy (full database clone with embeddings)
mychatarchive export my_archive.db

# Filter by platform
mychatarchive export chatgpt_only.json --platform chatgpt

# Include captured thoughts
mychatarchive export everything.json --include-thoughts
```

### Start the MCP server

```bash
mychatarchive serve                              # stdio (default, for local clients)
mychatarchive serve --transport sse --port 8420   # HTTP/SSE (for remote/mobile access)
```

### Check archive stats

```bash
mychatarchive info
```

```
MyChatArchive - ~/.mychatarchive/archive.db
----------------------------------------
  Messages:    47,832
  Threads:     1,204
  Embedded:    47,832
  Thoughts:    12
  Platforms:
    chatgpt: 38,541
    anthropic: 8,291
    grok: 1,000
```

## Connecting to AI Tools

### Claude Desktop

Run `mychatarchive mcp-config --client claude-desktop` and add the output to your config file:

**macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
**Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "mychatarchive": {
      "command": "mychatarchive",
      "args": ["serve"]
    }
  }
}
```

### Cursor

Run `mychatarchive mcp-config --client cursor` and add to your Cursor MCP settings.

### Custom database path

```json
{
  "mcpServers": {
    "mychatarchive": {
      "command": "mychatarchive",
      "args": ["--db", "/path/to/archive.db", "serve"]
    }
  }
}
```

### Remote access (SSE)

For mobile or multi-device access, run the SSE server on a NAS or always-on machine:

```bash
mychatarchive serve --transport sse --port 8420
```

Then connect via Tailscale/WireGuard from any device. Works with Claude mobile and any MCP client that supports remote servers.

## How It Works

```
┌─────────────────────┐     ┌──────────────────────┐     ┌─────────────────────┐
│ Chat Exports        │     │ MyChatArchive        │     │ MCP Clients         │
│                     │     │                      │     │                     │
│ ChatGPT  .json ─────┼────►│ Parse + Deduplicate  │     │ Claude Desktop      │
│ Claude   .json ─────┼────►│ Embed (local model)  │◄────┤ Cursor              │
│ Grok     .json ─────┼────►│ Store (SQLite + vec) │     │ Claude Code         │
│ Claude Code   ──────┼────►│ Export (JSON/CSV/DB) │     │ Claude Mobile       │
│ Cursor IDE    ──────┼────►│ Serve (MCP)          │     │ Any MCP client      │
└─────────────────────┘     └──────────────────────┘     └─────────────────────┘
```

### Pluggable backends

MyChatArchive ships with sensible local-first defaults but lets you swap backends when you need to scale or go remote:

| Component | Default | Available | Planned |
|-----------|---------|-----------|---------|
| **Storage** | SQLite + FTS5 + sqlite-vec | | PostgreSQL, Supabase |
| **Embeddings** | sentence-transformers (local) | | OpenAI, OpenRouter |
| **MCP Transport** | stdio (local pipe) | SSE/HTTP (remote) | |

Configure via `mychatarchive init` or edit `~/.mychatarchive/config.json` directly.

### Stack

| Component | Technology |
|-----------|-----------|
| **Storage** | SQLite + FTS5 (full-text) + sqlite-vec (vectors) |
| **Embeddings** | sentence-transformers `all-MiniLM-L6-v2` (384 dim, local) |
| **Interface** | MCP server (stdio + SSE transport) |
| **Deduplication** | SHA1-based stable message IDs |
| **CLI** | Python argparse + rich |
| **Export** | JSON, CSV, SQLite copy |

### Data stays local

- Embeddings generated by a local model - no OpenAI, no cloud
- Database is a single SQLite file on your disk
- MCP server runs locally over stdio
- No network calls, no telemetry, no tracking

## CLI Reference

| Command | Description |
|---------|-------------|
| `mychatarchive init` | Interactive setup (drop folder, auto-discovery, backends) |
| `mychatarchive sync` | Import from all sources (auto + drop folder + named) |
| `mychatarchive sync --embed` | Sync + generate embeddings in one shot |
| `mychatarchive import <file\|dir>` | Import a single file or directory |
| `mychatarchive import --from <name>` | Import from a named source |
| `mychatarchive sources add <name> <path>` | Add a named import source |
| `mychatarchive sources list` | Show all sources (auto + drop + named) |
| `mychatarchive sources remove <name>` | Remove a source |
| `mychatarchive sources rename <old> <new>` | Rename a source |
| `mychatarchive export <output>` | Export to JSON, CSV, or SQLite copy |
| `mychatarchive embed` | Generate vector embeddings |
| `mychatarchive serve` | Start MCP server |
| `mychatarchive search <query>` | Search from the terminal |
| `mychatarchive info` | Show archive statistics |
| `mychatarchive mcp-config` | Print MCP client configuration |

All commands accept `--db /path/to/archive.db` to override the default database location.

## Project Structure

```
mychatarchive/
├── src/mychatarchive/
│   ├── cli.py              # Unified CLI
│   ├── config.py           # Paths, constants, config management
│   ├── db.py               # Data access layer (delegates to backends)
│   ├── embeddings.py       # Embedding pipeline orchestration
│   ├── ingest.py           # Import engine with dedup
│   ├── parsers/
│   │   ├── __init__.py     # Auto-detection + registry
│   │   ├── chatgpt.py      # ChatGPT conversations.json
│   │   ├── anthropic.py    # Claude export format
│   │   ├── grok.py         # Grok/X.AI export format
│   │   ├── claude_code.py  # Claude Code JSONL sessions
│   │   └── cursor.py       # Cursor IDE SQLite databases
│   ├── backends/
│   │   ├── __init__.py     # Backend registry + factory
│   │   ├── storage/        # StorageBackend protocol + sqlite impl
│   │   ├── embeddings/     # EmbedderBackend protocol + local impl
│   │   └── transport/      # Transport type constants
│   └── mcp/
│       └── server.py       # MCP server (search_brain, capture_thought, etc.)
├── examples/               # Sample exports + MCP configs
├── tests/                  # Parser, DB, and backend tests
├── pyproject.toml          # Package config
├── ROADMAP.md              # Development roadmap
└── README.md
```

## Adding a New Parser

Create `src/mychatarchive/parsers/yourplatform.py`:

```python
from typing import Iterator

def parse(input_path: str) -> Iterator[dict]:
    """Yield normalized messages."""
    yield {
        "thread_id": "unique-thread-id",
        "thread_title": "Conversation Title",
        "role": "user",
        "content": "Message text",
        "created_at": 1700000000.0,
    }
```

Register it in `src/mychatarchive/parsers/__init__.py`:

```python
from mychatarchive.parsers import yourplatform

PARSERS = {
    ...
    "yourplatform": yourplatform,
}
```

## Default Data Location

```
~/.mychatarchive/
├── archive.db          # SQLite database (messages + vectors + thoughts)
├── config.json         # Backend + source configuration (optional)
└── imports/            # Drop folder - place export files here
```

Override with `--db /path/to/your.db` on any command, or set a custom drop folder path in `init`.

## Roadmap

- [x] Multi--platform import (ChatGPT, Claude, Grok, Claude Code, Cursor)
- [x] Local vector embeddings (sentence-transformers)
- [x] MCP server with semantic search + thought capture
- [x] Pluggable backend architecture (storage, embeddings, transport)
- [x] Export command (JSON, CSV, SQLite copy)
- [x] SSE transport for remote access
- [x] Named import sources with batch/folder import
- [x] One-command sync with auto-discovery + drop folder
- [ ] Additional parsers (Gemini, Perplexity, Copilot)
- [ ] Thread-level chunking for better context retrieval
- [ ] Analysis engine (life threads, decision tracking, pattern detection)
- [ ] Auto-sync (programmatic imports without manual exports)
- [ ] Web dashboard at [mychatarchive.com](https://mychatarchive.com)
- [ ] Docker image for one-command self-hosting

See [ROADMAP.md](ROADMAP.md) for the full phased plan.

## License

MIT - see [LICENSE](LICENSE).

---

**Built by [Channing Chasko](https://github.com/1ch1n)** · [mychatarchive.com](https://mychatarchive.com)
