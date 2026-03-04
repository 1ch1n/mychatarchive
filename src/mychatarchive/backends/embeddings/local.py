"""Local sentence-transformers embedding backend (default).

Uses the sentence-transformers library to generate embeddings locally.
No API calls, no data leaves the machine.
"""

import sys

_model = None
_model_name = None


def _get_default_model() -> str:
    from mychatarchive.config import get_embedding_model
    return get_embedding_model()


def get_model():
    global _model, _model_name
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model_name = _get_default_model()
        print(f"Loading model: {_model_name}", file=sys.stderr)
        _model = SentenceTransformer(_model_name)
        print("Model loaded.", file=sys.stderr)
    return _model


def embed_texts(texts: list[str]) -> list[list[float]]:
    model = get_model()
    embeddings = model.encode(texts, show_progress_bar=False, convert_to_numpy=True)
    return [e.tolist() for e in embeddings]


def embed_single(text: str) -> list[float]:
    return embed_texts([text])[0]


def dimension() -> int:
    from mychatarchive.config import get_embedding_dim
    return get_embedding_dim()
