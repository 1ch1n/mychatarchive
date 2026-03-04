"""Embedder backend protocol.

Any embedder backend must implement these functions as module-level callables.
The default is 'local' which uses sentence-transformers.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbedderBackend(Protocol):
    """Defines the interface every embedder backend must satisfy."""

    def embed_texts(self, texts: list[str]) -> list[list[float]]: ...
    def embed_single(self, text: str) -> list[float]: ...
    def dimension(self) -> int: ...
