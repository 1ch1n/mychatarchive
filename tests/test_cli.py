"""CLI regression tests for argparse wiring and dispatch."""

import sqlite3
import sys

import pytest

from mychatarchive import cli


def _run(argv, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["mychatarchive"] + argv)
    cli.main()


def test_bare_groups_without_db_attr_does_not_crash(tmp_path, monkeypatch, capsys):
    # Regression for issue #10 / PR #11: `mychatarchive groups` used to raise
    # AttributeError because the groups parser namespace had no `db` attribute.
    missing = tmp_path / "nope.db"
    monkeypatch.setattr(cli, "get_db_path", lambda: missing)
    with pytest.raises(SystemExit) as exc:
        _run(["groups"], monkeypatch)
    assert exc.value.code == 1
    assert "No database found" in capsys.readouterr().err


def test_groups_subcommands_accept_db_flag(tmp_path, monkeypatch, capsys):
    db_file = tmp_path / "archive.db"
    sqlite3.connect(db_file).close()  # _cmd_groups requires the file to exist

    _run(["groups", "create", "jarvis", "--db", str(db_file)], monkeypatch)
    assert "created" in capsys.readouterr().out

    _run(["groups", "list", "--db", str(db_file)], monkeypatch)
    assert "jarvis" in capsys.readouterr().out
