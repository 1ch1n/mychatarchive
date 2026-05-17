"""Tests for parser auto-detection and parsing."""

from pathlib import Path

import pytest

import mychatarchive.parsers as parser_registry
from mychatarchive.parsers import detect_format, parse

EXAMPLES_DIR = Path(__file__).parent.parent / "examples"


def test_detect_chatgpt():
    assert detect_format(EXAMPLES_DIR / "sample_chatgpt.json") == "chatgpt"


def test_detect_anthropic():
    assert detect_format(EXAMPLES_DIR / "sample_anthropic.json") == "anthropic"


def test_detect_grok():
    assert detect_format(EXAMPLES_DIR / "sample_grok.json") == "grok"


def test_parse_chatgpt():
    messages = list(parse(EXAMPLES_DIR / "sample_chatgpt.json", "chatgpt"))
    assert len(messages) > 0
    for msg in messages:
        assert "thread_id" in msg
        assert "role" in msg
        assert "content" in msg
        assert "created_at" in msg


def test_parse_anthropic():
    messages = list(parse(EXAMPLES_DIR / "sample_anthropic.json", "anthropic"))
    assert len(messages) > 0


def test_parse_grok():
    messages = list(parse(EXAMPLES_DIR / "sample_grok.json", "grok"))
    assert len(messages) > 0


def test_auto_detect_parse():
    messages = list(parse(EXAMPLES_DIR / "sample_chatgpt.json"))
    assert len(messages) > 0


def test_parse_preserves_optional_archive_truth_fields(monkeypatch, tmp_path):
    class StubParser:
        @staticmethod
        def parse(_input_path):
            yield {
                "thread_id": "thread-1",
                "role": "user",
                "content": "hello",
                "created_at": 1700000000.0,
                "source_path": "/exports/chatgpt.json",
                "source_bucket": "dropbox",
                "provenance_json": {"origin": "test"},
                "content_blocks": [{"kind": "text", "text": "hello"}],
                "provenance_refs": [{"type": "file", "id": "x1"}],
            }

    monkeypatch.setitem(parser_registry.PARSERS, "stub_format", StubParser)
    messages = list(parse(tmp_path / "stub.json", "stub_format"))

    assert len(messages) == 1
    msg = messages[0]
    assert msg["source_path"] == "/exports/chatgpt.json"
    assert msg["source_bucket"] == "dropbox"
    assert msg["provenance_json"] == {"origin": "test"}
    assert msg["content_blocks"] == [{"kind": "text", "text": "hello"}]
    assert msg["provenance_refs"] == [{"type": "file", "id": "x1"}]
    assert msg["thread_title"] == ""
    assert msg["source_message_id"] == ""
