"""CLI commands for MyChatArchive memory provider.

Handles: hermes mychatarchive status | config | import
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def _hermes_home() -> str:
    """Return the active HERMES_HOME directory."""
    val = os.environ.get("HERMES_HOME", "").strip()
    if val:
        return val
    try:
        from hermes_constants import get_hermes_home
        return str(get_hermes_home())
    except Exception:
        return str(Path.home() / ".hermes")


def _load_config() -> dict:
    """Load the mychatarchive plugin config."""
    config_path = Path(_hermes_home()) / "mychatarchive.json"
    if config_path.exists():
        try:
            return json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _resolve_db_path(config: dict) -> Path:
    """Resolve the database path from config or defaults."""
    custom = config.get("db_path", "")
    if custom:
        return Path(custom).expanduser()
    return Path.home() / ".mychatarchive" / "archive.db"


def cmd_status(args) -> None:
    """Show MyChatArchive connection health and archive size."""
    config = _load_config()
    db_path = _resolve_db_path(config)
    recall_mode = config.get("recall_mode", "hybrid")
    prefetch_limit = config.get("prefetch_limit", "5")

    print(f"\nMyChatArchive status\n" + "-" * 40)
    print(f"  Config:        {Path(_hermes_home()) / 'mychatarchive.json'}")
    print(f"  Database:      {db_path}")
    print(f"  Recall mode:   {recall_mode}")
    print(f"  Prefetch:      {prefetch_limit} chunks/turn")

    if not db_path.exists():
        print(f"\n  Database not found at {db_path}")
        print("  Run 'mychatarchive sync && mychatarchive embed' to create it.\n")
        return

    print(f"\n  Connection... ", end="", flush=True)
    try:
        from mychatarchive import db
        con = db.get_connection(db_path)
        messages = db.message_count(con)
        chunks = db.chunk_count(con)
        thoughts = db.thought_count(con)
        threads = db.thread_count(con)
        summaries = db.summarized_thread_count(con)
        platforms = db.platform_counts(con)
        groups = db.group_count(con)
        con.close()
        print("OK")

        print(f"\n  Archive stats:")
        print(f"    Messages:    {messages:,}")
        print(f"    Threads:     {threads:,} ({summaries:,} summarized)")
        print(f"    Chunks:      {chunks:,}")
        print(f"    Thoughts:    {thoughts:,}")
        print(f"    Groups:      {groups:,}")
        if platforms:
            print(f"    Platforms:   {', '.join(f'{p} ({c:,})' for p, c in platforms)}")
        print()
    except ImportError:
        print("FAILED")
        print("  mychatarchive package not installed.")
        print("  Run: pip install git+https://github.com/1ch1n/mychatarchive\n")
    except Exception as e:
        print(f"FAILED")
        print(f"  Error: {e}\n")


def cmd_config(args) -> None:
    """Show current MyChatArchive plugin configuration."""
    config = _load_config()
    config_path = Path(_hermes_home()) / "mychatarchive.json"

    print(f"\nMyChatArchive config\n" + "-" * 40)
    print(f"  File: {config_path}")

    if not config:
        print("  (no config file -- using defaults)")
    else:
        print(f"  Contents:")
        for key, value in config.items():
            print(f"    {key}: {value}")
    print()

    print("  Defaults:")
    print("    db_path:        ~/.mychatarchive/archive.db")
    print("    recall_mode:    hybrid")
    print("    prefetch_limit: 5")
    print()
    print(f"  Edit with: hermes config edit")
    print(f"  Or directly: {config_path}\n")


def cmd_import(args) -> None:
    """Kick off a chat-export import into MyChatArchive."""
    config = _load_config()
    db_path = _resolve_db_path(config)

    print(f"\nMyChatArchive import\n" + "-" * 40)

    # Check if mychatarchive CLI is available
    try:
        from mychatarchive import db
    except ImportError:
        print("  mychatarchive package not installed.")
        print("  Run: pip install git+https://github.com/1ch1n/mychatarchive\n")
        return

    print("  This will run the MyChatArchive sync and embed pipeline:")
    print(f"    1. mychatarchive sync   -- import new conversations")
    print(f"    2. mychatarchive embed  -- generate vector embeddings")
    print(f"    Database: {db_path}")
    print()

    answer = input("  Proceed? [Y/n]: ").strip().lower()
    if answer and answer not in ("y", "yes"):
        print("  Skipped.\n")
        return

    db_args = ["--db", str(db_path)] if config.get("db_path") else []

    print("\n  Running sync...", flush=True)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "mychatarchive", "sync"] + db_args,
            capture_output=False,
            timeout=600,
        )
        if result.returncode != 0:
            print(f"  Sync exited with code {result.returncode}")
    except FileNotFoundError:
        print("  mychatarchive CLI not found. Run: pip install mychatarchive")
        return
    except subprocess.TimeoutExpired:
        print("  Sync timed out after 10 minutes.")
        return

    print("\n  Running embed...", flush=True)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "mychatarchive", "embed"] + db_args,
            capture_output=False,
            timeout=1800,
        )
        if result.returncode != 0:
            print(f"  Embed exited with code {result.returncode}")
    except subprocess.TimeoutExpired:
        print("  Embed timed out after 30 minutes.")
        return

    print("\n  Import complete. Restart your Hermes session to pick up new data.\n")


def mychatarchive_command(args) -> None:
    """Route mychatarchive subcommands."""
    sub = getattr(args, "mychatarchive_command", None)
    if sub is None or sub == "status":
        cmd_status(args)
    elif sub == "config":
        cmd_config(args)
    elif sub == "import":
        cmd_import(args)
    else:
        print(f"  Unknown mychatarchive command: {sub}")
        print("  Available: status, config, import\n")


def register_cli(subparser) -> None:
    """Build the ``hermes mychatarchive`` argparse subcommand tree.

    Called by the plugin CLI registration system during argparse setup.
    The *subparser* is the parser for ``hermes mychatarchive``.
    """
    subs = subparser.add_subparsers(dest="mychatarchive_command")

    subs.add_parser(
        "status",
        help="Show connection health and archive size",
    )
    subs.add_parser(
        "config",
        help="Show current plugin configuration",
    )
    subs.add_parser(
        "import",
        help="Run sync + embed to import new conversations",
    )

    subparser.set_defaults(func=mychatarchive_command)
