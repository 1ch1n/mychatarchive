# Contributing to MyChatArchive

Thank you for your interest in contributing. This guide will help you get
started.

## Getting Started

1. Fork the repository and clone your fork:

```bash
git clone https://github.com/<your-username>/mychatarchive.git
cd mychatarchive
```

2. Install in development mode:

```bash
pip install -e ".[dev]"
```

3. Verify that tests pass:

```bash
pytest
```

## Development Workflow

1. Create a branch from `main`:

```bash
git checkout -b your-branch-name
```

2. Make your changes. Follow the existing code style.

3. Run the linter and fix any issues:

```bash
ruff check src/ tests/
ruff format src/ tests/
```

4. Run tests:

```bash
pytest
```

5. Commit your changes with a clear message describing what you did and why.

6. Push to your fork and open a pull request against `main`.

## Code Style

- Python 3.10+ syntax
- [Ruff](https://github.com/astral-sh/ruff) for linting and formatting
- Line length limit: 100 characters
- Write clear, concise code. Favor readability over cleverness.

## Project Layout

```
src/mychatarchive/
    cli.py              # CLI entry point
    config.py           # Paths, constants, configuration
    db.py               # Data access layer
    embeddings.py       # Local embedding pipeline
    chunker.py          # Message chunking
    ingest.py           # Import engine with SHA1 dedup
    summarizer.py       # LLM thread summarization
    parsers/            # One module per chat platform
    backends/           # Pluggable storage, embeddings, transport
    mcp/
        server.py       # MCP server (6 tools)
tests/
```

## Adding a Parser

To add support for a new chat platform, create
`src/mychatarchive/parsers/yourplatform.py`:

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

Then register it in `src/mychatarchive/parsers/__init__.py`.

## What to Contribute

- **Bug fixes.** If you find a bug, please open an issue first. If you have a
  fix, include it in a pull request.
- **New parsers.** Gemini, Perplexity, Copilot, and others are on the roadmap.
- **Tests.** More test coverage is always welcome.
- **Documentation.** Improvements to the README, docstrings, or examples.

If you want to work on something larger (new features, architectural changes),
open an issue first so we can discuss the approach.

## Pull Request Guidelines

- Keep pull requests focused. One logical change per PR.
- Write a clear title and description. Explain what changed and why.
- Include tests for new functionality.
- Make sure all tests pass and the linter is clean before submitting.

## Reporting Bugs

Open an issue on
[GitHub Issues](https://github.com/1ch1n/mychatarchive/issues) with:

- A clear title describing the problem
- Steps to reproduce
- Expected behavior vs. actual behavior
- Your Python version and operating system

## License

By contributing, you agree that your contributions will be licensed under the
[AGPL-3.0 License](LICENSE).
