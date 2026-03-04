"""Tests for the pluggable backend registry."""

import tempfile
from pathlib import Path

import pytest

from mychatarchive.backends import get_storage, get_embedder, get_transport, reset


@pytest.fixture(autouse=True)
def _reset_backends():
    """Reset cached backends between tests."""
    reset()
    yield
    reset()


def test_default_storage_is_sqlite():
    storage = get_storage()
    assert hasattr(storage, "get_connection")
    assert hasattr(storage, "ensure_schema")
    assert hasattr(storage, "insert_message")
    assert hasattr(storage, "search_chunks")


def test_default_embedder_is_local():
    embedder = get_embedder()
    assert hasattr(embedder, "embed_texts")
    assert hasattr(embedder, "embed_single")
    assert hasattr(embedder, "dimension")


def test_default_transport_is_stdio():
    assert get_transport() == "stdio"


def test_storage_backend_works():
    """Verify the sqlite backend can create a DB and insert data."""
    storage = get_storage()
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as f:
        db_path = Path(f.name)
    try:
        con = storage.get_connection(db_path)
        storage.ensure_schema(con)
        result = storage.insert_message(
            con, "msg1", "thread1", "test", "main",
            "2026-01-01T00:00:00Z", "user", "Hello", "Test", "src1",
        )
        assert result is True
        assert storage.message_count(con) == 1
        con.close()
    finally:
        db_path.unlink(missing_ok=True)


def test_embedder_dimension():
    embedder = get_embedder()
    assert embedder.dimension() == 384


def test_unknown_storage_backend_raises(tmp_path, monkeypatch):
    """Verify that an unknown backend name raises a clear error."""
    config_file = tmp_path / "config.json"
    config_file.write_text('{"storage": {"backend": "nonexistent"}}')

    import mychatarchive.config as config_mod
    monkeypatch.setattr(config_mod, "get_config_path", lambda: config_file)

    with pytest.raises(ValueError, match="Unknown storage backend"):
        get_storage()


def test_unknown_embedder_backend_raises(tmp_path, monkeypatch):
    config_file = tmp_path / "config.json"
    config_file.write_text('{"embeddings": {"backend": "nonexistent"}}')

    import mychatarchive.config as config_mod
    monkeypatch.setattr(config_mod, "get_config_path", lambda: config_file)

    with pytest.raises(ValueError, match="Unknown embedder backend"):
        get_embedder()
