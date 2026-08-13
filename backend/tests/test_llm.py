"""The JSON gate: parse, unwrap schema-envelope echoes, validate against schema."""

import pytest

from app.llm import _parse

SCHEMA = {
    "type": "object",
    "properties": {
        "value": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": ["value", "confidence"],
}


def test_clean_json_passes():
    assert _parse('{"value": "sales", "confidence": 0.9}', SCHEMA)["value"] == "sales"


def test_markdown_fences_stripped():
    assert _parse('```json\n{"value": "sales", "confidence": 0.9}\n```', SCHEMA)["confidence"] == 0.9


def test_schema_envelope_echo_unwrapped():
    # the exact failure observed live: model echoes the schema wrapper
    wrapped = '{"type": "object", "properties": {"value": "sales", "confidence": 0.95}}'
    parsed = _parse(wrapped, SCHEMA)
    assert parsed == {"value": "sales", "confidence": 0.95}


def test_schema_violation_rejected():
    with pytest.raises(ValueError, match="schema violation"):
        _parse('{"value": 42, "confidence": "high"}', SCHEMA)


def test_missing_required_rejected():
    with pytest.raises(ValueError, match="schema violation"):
        _parse('{"value": "sales"}', SCHEMA)


def test_garbage_rejected():
    with pytest.raises(ValueError):
        _parse("Sure! Here's my analysis of the call...", SCHEMA)
