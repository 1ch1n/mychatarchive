## What does this PR do?

Adds a MyChatArchive memory provider plugin that gives Hermes read access to a local [MyChatArchive](https://github.com/1ch1n/mychatarchive) database. MyChatArchive stores imported conversations from ChatGPT, Claude, Cursor, Grok, and other platforms in a single SQLite database with vector embeddings (sentence-transformers, 384-dim). This plugin bridges that archive into Hermes as persistent cross-session memory.

Purely local. No API keys, no cloud dependency. Same plugin pattern as Honcho, Hindsight, and Holographic.

## Related Issue

N/A (new plugin, no existing issue)

## Type of Change

- [ ] Bug fix (non-breaking change that fixes an issue)
- [x] New feature (non-breaking change that adds functionality)
- [ ] Security fix
- [ ] Documentation update
- [ ] Tests (adding or improving test coverage)
- [ ] Refactor (no behavior change)
- [ ] New skill (bundled or hub)

## Changes Made

- `plugins/memory/mychatarchive/__init__.py` -- `MyChatArchiveProvider` implementing `MemoryProvider` ABC (~805 lines)
- `plugins/memory/mychatarchive/cli.py` -- CLI subcommands: `hermes mychatarchive status|config|import`
- `plugins/memory/mychatarchive/plugin.yaml` -- plugin metadata and hooks list
- `plugins/memory/mychatarchive/README.md` -- setup instructions, config reference, tool reference

**Four tools:**

| Tool | What it does |
|------|-------------|
| `mca_search` | Semantic (vector), keyword (FTS5), or hybrid search across the archive |
| `mca_recall` | Multi-layer context retrieval: message chunks + thread summaries + thoughts |
| `mca_remember` | Capture a new thought/insight with embedding for future retrieval |
| `mca_provenance` | Trace a search result back to its source thread, platform, and timestamp |

**Hooks:** `system_prompt_block` (archive stats), `prefetch` (auto-inject top-K chunks), `sync_turn` (non-blocking daemon thread), `on_session_switch` (keeps session_id current)

**Other:** `post_setup()` wizard with auto-install of mychatarchive package, embedding dimension validation at startup, three recall modes (hybrid/context/tools)

## How to Test

1. Install mychatarchive: `pip install git+https://github.com/1ch1n/mychatarchive`
2. Populate an archive: `mychatarchive sync && mychatarchive embed`
3. Run `hermes memory setup` and select `mychatarchive`
4. Add `memory` to your platform's toolset if using a gateway:
   ```yaml
   platform_toolsets:
     telegram:
     - hermes-telegram
     - memory
   ```
5. Start a session and ask: "What have I discussed about [topic]?"
6. Verify the model calls `mca_recall` / `mca_search` (visible in tool call log)

## Checklist

### Code

- [x] I've read the [Contributing Guide](https://github.com/NousResearch/hermes-agent/blob/main/CONTRIBUTING.md)
- [x] My commit messages follow [Conventional Commits](https://www.conventionalcommits.org/)
- [x] I searched for [existing PRs](https://github.com/NousResearch/hermes-agent/pulls) to make sure this isn't a duplicate
- [x] My PR contains **only** changes related to this fix/feature (no unrelated commits)
- [x] I've run `pytest tests/ -q` and all tests pass
- [x] I've added tests for my changes (49 tests in the MCA repo, plugin tested end-to-end via Telegram)
- [x] I've tested on my platform: Windows 11 (24H2), Hermes v0.14.0, Python 3.13, Telegram gateway with NAS-hosted database

### Documentation & Housekeeping

- [x] I've updated relevant documentation (README with setup, config, tool reference)
- [x] I've updated `cli-config.yaml.example` if I added/changed config keys -- N/A (plugin uses its own `mychatarchive.json`)
- [x] I've updated `CONTRIBUTING.md` or `AGENTS.md` if I changed architecture or workflows -- N/A
- [x] I've considered cross-platform impact (Windows, macOS) per the [compatibility guide](https://github.com/NousResearch/hermes-agent/blob/main/CONTRIBUTING.md#cross-platform-compatibility) -- uses pathlib throughout, no platform-specific code
- [x] I've updated tool descriptions/schemas if I changed tool behavior -- N/A (new tools)
