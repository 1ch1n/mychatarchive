"""Tests for parser auto-detection and parsing."""

from pathlib import Path

import pytest

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
