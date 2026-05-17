from mychatarchive.hybrid_search import (
    HybridSearchResult,
    keyword_candidates,
    merge_candidates,
    search_hybrid,
)


class FakeConnection:
    def __init__(self, chunk_rows):
        self.chunk_rows = chunk_rows

    def execute(self, _sql, params):
        wanted = set(params)
        return FakeCursor([row for row in self.chunk_rows if row[0] in wanted])


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


def test_keyword_exact_identifier_beats_high_semantic_similarity():
    keyword = keyword_candidates(
        [
            (
                "msg-exact-42",
                "Short exact identifier hit",
                "thread-alpha",
                "2026-01-01T00:00:00Z",
                "assistant",
                "Exact Thread",
            )
        ],
        "msg-exact-42",
    )
    semantic = [
        HybridSearchResult(
            result_id="msg-semantic",
            match_key="message:msg-semantic",
            thread_id="thread-beta",
            message_id="msg-semantic",
            chunk_id="chunk-semantic",
            text="A vague but semantically close discussion",
            title="Semantic Thread",
            role="assistant",
            timestamp="2026-01-02T00:00:00Z",
            source=["semantic"],
            semantic_rank=1,
            semantic_score=0.98,
            distance=0.02,
        )
    ]

    results = merge_candidates(keyword, semantic, query="msg-exact-42", limit=2)

    assert results[0].message_id == "msg-exact-42"
    assert results[0].exact_score == 1.0
    assert results[1].message_id == "msg-semantic"


def test_merge_combines_keyword_and_semantic_for_same_message():
    keyword = keyword_candidates(
        [
            (
                "msg-shared",
                "The canonical archive bridge preserves source ids.",
                "thread-shared",
                "2026-01-01T00:00:00Z",
                "user",
                "Bridge",
            )
        ],
        "canonical archive bridge",
    )
    semantic = [
        HybridSearchResult(
            result_id="msg-shared",
            match_key="message:msg-shared",
            thread_id="thread-shared",
            message_id="msg-shared",
            chunk_id="chunk-shared",
            text="The canonical archive bridge preserves source ids.",
            title="Bridge",
            role="user",
            timestamp="2026-01-01T00:00:00Z",
            source=["semantic"],
            semantic_rank=1,
            semantic_score=0.9,
            distance=0.1,
        )
    ]

    results = merge_candidates(keyword, semantic, query="canonical archive bridge", limit=1)

    assert len(results) == 1
    assert set(results[0].source) == {"keyword", "semantic"}
    assert results[0].keyword_score > 0
    assert results[0].semantic_score == 0.9
    assert results[0].distance == 0.1


def test_search_hybrid_uses_injected_searchers_and_returns_json_ready_results():
    con = FakeConnection(
        [
            (
                "chunk-shared",
                "msg-shared",
                "thread-shared",
                "Hybrid retrieval should preserve canonical ids.",
                "2026-01-01T00:00:00Z",
                None,
                "assistant",
                "Hybrid",
            )
        ]
    )

    def keyword_search(*_args, **_kwargs):
        return [
            (
                "msg-shared",
                "Hybrid retrieval should preserve canonical ids.",
                "thread-shared",
                "2026-01-01T00:00:00Z",
                "assistant",
                "Hybrid",
            )
        ]

    def semantic_search(*_args, **_kwargs):
        return [("chunk-shared", 0.2)]

    results = search_hybrid(
        con,
        "canonical ids",
        [0.0, 1.0],
        limit=5,
        keyword_search=keyword_search,
        semantic_search=semantic_search,
    )

    assert len(results) == 1
    assert results[0]["message_id"] == "msg-shared"
    assert results[0]["thread_id"] == "thread-shared"
    assert results[0]["chunk_id"] == "chunk-shared"
    assert set(results[0]["source"]) == {"keyword", "semantic"}
    assert results[0]["scores"]["keyword"] > 0
    assert results[0]["scores"]["semantic"] == 0.8
