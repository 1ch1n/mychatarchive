"""Variable, context-aware text chunker for semantic embedding.

Strategy (hierarchical, no external deps):
  1. Short texts (< chunk_size) → single chunk, no splitting needed.
  2. Long texts → split on natural boundaries (paragraph → line → sentence → word),
     then greedily merge units back up to chunk_size.
  3. Adjacent chunks get overlap: the tail of chunk N is prepended to chunk N+1
     so that sentence-boundary context is preserved for search.

Character heuristic:  ~4 chars/token for English prose.
Default chunk_size = 1200 chars ≈ 300 tokens.
  - Fits within sentence-transformers/all-MiniLM-L6-v2 (256-token window) with
    minimal internal truncation, while capturing far more content than the old
    fixed 2 000-char hard truncation.
  - Well under OpenAI text-embedding-3-* 8 192-token limit.
Default overlap = 150 chars ≈ 37 tokens — enough to keep boundary context.
"""

import re

# --- Regex boundary patterns ---

# Two or more blank lines → paragraph break
_RE_PARA = re.compile(r"\n{2,}")
# Single newline
_RE_LINE = re.compile(r"\n")
# Sentence end: period/!/? followed by whitespace (lookbehind keeps the punctuation)
_RE_SENTENCE = re.compile(r"(?<=[.!?])\s+")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _split_sentences(text: str) -> list[str]:
    parts = _RE_SENTENCE.split(text)
    return [p.strip() for p in parts if p.strip()]


def _split_atoms(text: str, max_chars: int) -> list[str]:
    """Recursively split *text* into pieces each <= *max_chars*.

    Tries progressively finer boundaries:
      paragraph → line → sentence → word-boundary hard cut.
    Returns a flat list of non-empty strings.
    """
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    # 1. Paragraph split
    paras = [p.strip() for p in _RE_PARA.split(text) if p.strip()]
    if len(paras) > 1:
        result: list[str] = []
        for p in paras:
            result.extend(_split_atoms(p, max_chars))
        return result

    # 2. Line split
    lines = [ln.strip() for ln in _RE_LINE.split(text) if ln.strip()]
    if len(lines) > 1:
        result = []
        for ln in lines:
            result.extend(_split_atoms(ln, max_chars))
        return result

    # 3. Sentence split
    sentences = _split_sentences(text)
    if len(sentences) > 1:
        result = []
        for s in sentences:
            result.extend(_split_atoms(s, max_chars))
        return result

    # 4. Hard cut on word boundary (last resort)
    words = text.split()
    result = []
    current: list[str] = []
    current_len = 0
    for word in words:
        wlen = len(word) + (1 if current else 0)  # +1 for space separator
        if current_len + wlen > max_chars and current:
            result.append(" ".join(current))
            current = [word]
            current_len = len(word)
        else:
            current.append(word)
            current_len += wlen
    if current:
        result.append(" ".join(current))
    return result


def _merge_atoms(atoms: list[str], chunk_size: int) -> list[str]:
    """Greedily merge atoms into chunks, each <= chunk_size chars.

    Atoms are joined with a single space (whitespace structure is not
    preserved — only semantic content matters for embedding).
    """
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for atom in atoms:
        sep = 1 if current else 0
        atom_len = len(atom)
        if current_len + sep + atom_len > chunk_size and current:
            chunks.append(" ".join(current))
            current = [atom]
            current_len = atom_len
        else:
            current.append(atom)
            current_len += sep + atom_len

    if current:
        chunks.append(" ".join(current))

    return chunks


def _apply_overlap(chunks: list[str], overlap: int) -> list[str]:
    """Prepend a tail from chunk[i-1] to chunk[i] for each i >= 1.

    Trims the tail to start on a word boundary so we never embed a
    half-word at the start of the overlap region.
    """
    if overlap <= 0 or len(chunks) <= 1:
        return chunks

    result = [chunks[0]]
    for i in range(1, len(chunks)):
        tail = chunks[i - 1][-overlap:]
        # Advance to first word boundary inside the tail
        space = tail.find(" ")
        if 0 < space < len(tail) - 1:
            tail = tail[space + 1:]
        result.append((tail + " " + chunks[i]) if tail else chunks[i])

    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def chunk_text(text: str, chunk_size: int = 1200, overlap: int = 150) -> list[str]:
    """Split *text* into overlapping chunks suitable for semantic embedding.

    Short messages (len <= chunk_size) are returned as a single chunk.
    Long messages are split on natural boundaries and merged with overlap.

    Args:
        text:       Input text (may be any length).
        chunk_size: Target maximum characters per chunk.  Chunks may be
                    slightly larger when overlap is added (bounded by
                    chunk_size + overlap).
        overlap:    Characters of context carried from the end of chunk N
                    into the start of chunk N+1.

    Returns:
        Non-empty list of chunk strings.  Never empty if *text* has content.
    """
    text = text.strip()
    if not text:
        return []

    # Fast path: fits in a single chunk
    if len(text) <= chunk_size:
        return [text]

    atoms = _split_atoms(text, chunk_size)
    if not atoms:
        return []

    chunks = _merge_atoms(atoms, chunk_size)
    chunks = _apply_overlap(chunks, overlap)
    return chunks
