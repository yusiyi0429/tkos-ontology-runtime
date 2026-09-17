"""Structural parser tests for the private source import driver.

Synthetic wrapper shapes only; no private source content is used or printed.
"""
import json

from acceptance.workspace_v02.import_sources import parse_json, parse_source, parse_text


def test_text_parser_merges_continuations_and_never_invents_speakers():
    segments, stats = parse_text("Alice: first line\ncontinued text\n\nBob: second line\n")
    assert [item["speaker"] for item in segments] == ["Alice", "Bob"]
    assert segments[0]["text"] == "first line\ncontinued text"
    assert stats["dialogue_lines"] == 2 and stats["continuation_lines"] == 1


def test_document_wrapper_parses_known_content_and_preserves_identity():
    payload = {"ok": True, "identity": {"org": "x"},
               "data": {"document": {"document_id": "doc-007", "revision_id": 3,
                                     "content": "<p>Alice: hello</p><p>Bob: world</p>",
                                     "updated_at": "2026-01-02T03:04:05+00:00"}}}
    segments, stats = parse_json(json.dumps(payload))
    assert [item["speaker"] for item in segments] == ["Alice", "Bob"]
    assert stats["document_id"] == "doc-007" and stats["document_revision"] == 3
    assert stats["document_time"] == "2026-01-02T03:04:05+00:00"
    assert stats["format"] == "markup"
    assert stats["lines"] == 2 and stats["parsed"] == 2


def test_document_plain_text_is_not_marked_as_markup():
    payload = {"data": {"document": {"content": "Alice: plain\nBob: text"}}}
    segments, stats = parse_json(json.dumps(payload))
    assert stats["format"] == "text" and len(segments) == 2


def test_source_stats_expose_every_key_for_json_and_text(tmp_path):
    path = tmp_path / "doc.json"
    path.write_text(json.dumps({"data": {"document": {"content": "Alice: one"}}}))
    segments, stats = parse_source(path)
    assert segments and {"lines", "dialogue_lines", "continuation_lines", "json_lines",
                         "segments", "speakers", "occurred_at_values", "format"} <= set(stats)
    text_path = tmp_path / "doc.txt"
    text_path.write_text("Alice: one\n")
    _, text_stats = parse_source(text_path)
    assert {"lines", "dialogue_lines", "continuation_lines", "segments"} <= set(text_stats)
