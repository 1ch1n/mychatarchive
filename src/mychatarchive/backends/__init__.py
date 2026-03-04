"""Backend registry. Reads config and returns the appropriate backend modules.

All backends are lazily imported -- heavy dependencies only load when selected.
"""

from __future__ import annotations

_storage_backend = None
_embedder_backend = None


def get_storage():
    """Return the active storage backend module."""
    global _storage_backend
    if _storage_backend is not None:
        return _storage_backend

    from mychatarchive.config import load_config
    cfg = load_config().get("storage", {})
    backend = cfg.get("backend", "sqlite")

    if backend == "sqlite":
        from mychatarchive.backends.storage import sqlite as mod
    else:
        raise ValueError(
            f"Unknown storage backend: '{backend}'. "
            f"Available: sqlite. "
            f"Run 'mychatarchive init' to configure."
        )

    _storage_backend = mod
    return mod


def get_embedder():
    """Return the active embedder backend module."""
    global _embedder_backend
    if _embedder_backend is not None:
        return _embedder_backend

    from mychatarchive.config import load_config
    cfg = load_config().get("embeddings", {})
    backend = cfg.get("backend", "local")

    if backend == "local":
        from mychatarchive.backends.embeddings import local as mod
    else:
        raise ValueError(
            f"Unknown embedder backend: '{backend}'. "
            f"Available: local. "
            f"Run 'mychatarchive init' to configure."
        )

    _embedder_backend = mod
    return mod


def get_transport() -> str:
    """Return the configured MCP transport type."""
    from mychatarchive.config import load_config
    cfg = load_config().get("transport", {})
    return cfg.get("type", "stdio")


def reset():
    """Reset cached backends (useful for testing)."""
    global _storage_backend, _embedder_backend
    _storage_backend = None
    _embedder_backend = None
