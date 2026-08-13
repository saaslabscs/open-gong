"""Transcript prettify: skips clean text, reformats raw text, preserves line
count/numbers so evidence anchors stay valid, falls back to raw on trouble."""

import app.llm as llm_mod
from app.insights import _looks_clean, prettify_transcript

RAW = [
    {"line": 1, "speaker": "Speaker 1", "text": "hi tom this call is recorded is that okay"},
    {"line": 2, "speaker": "Speaker 2", "text": "yeah that's fine"},
]
CLEAN = [
    {"line": 1, "speaker": "Ana", "text": "Hi Tom, this call is recorded. Is that okay?"},
    {"line": 2, "speaker": "Bob", "text": "Yeah, that's fine."},
]


def test_looks_clean_detects_formatted_text():
    assert _looks_clean(CLEAN) is True
    assert _looks_clean(RAW) is False


def test_already_clean_skips_llm(monkeypatch):
    monkeypatch.setattr(llm_mod, "complete_json", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not call LLM")))
    out, cost = prettify_transcript(CLEAN)
    assert out is CLEAN and cost == 0.0


def test_prettify_reformats_and_preserves_anchors(monkeypatch):
    def fake(system, user, schema, max_tokens=4000):
        return {"lines": [
            {"line": 1, "speaker": "Tom", "text": "Hi Tom, this call is recorded. Is that okay?"},
            {"line": 2, "speaker": "Rep", "text": "Yeah, that's fine."},
        ]}, 0.003
    monkeypatch.setattr(llm_mod, "complete_json", fake)
    out, cost = prettify_transcript(RAW)
    assert cost == 0.003
    assert [l["line"] for l in out] == [1, 2]  # anchors preserved
    assert out[0]["text"].endswith("okay?")


def test_line_count_mismatch_falls_back_to_raw(monkeypatch):
    def fake(system, user, schema, max_tokens=4000):
        return {"lines": [{"line": 1, "speaker": "X", "text": "only one line"}]}, 0.002
    monkeypatch.setattr(llm_mod, "complete_json", fake)
    out, _ = prettify_transcript(RAW)
    assert out == RAW  # rejected: would break evidence anchors


def test_llm_failure_falls_back_to_raw(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("provider down")
    monkeypatch.setattr(llm_mod, "complete_json", boom)
    out, cost = prettify_transcript(RAW)
    assert out is RAW and cost == 0.0
