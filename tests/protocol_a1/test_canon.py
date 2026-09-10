"""Offline golden tests for tkos-json-v1 canonicalization (no database)."""
from __future__ import annotations

import hashlib

import pytest

from memory_service_runtime.governed import canon


def test_canonicalize_sorts_keys_recursively_and_uses_compact_separators():
    value = {"b": 1, "a": [{"d": True, "c": None}]}
    assert canon.canonicalize(value) == b'{"a":[{"c":null,"d":true}],"b":1}'


def test_canonicalize_does_not_escape_non_ascii():
    assert canon.canonicalize({"k": "中文"}) == '{"k":"中文"}'.encode("utf-8")


def test_digest_golden_vectors():
    assert canon.digest({}) == hashlib.sha256(b"{}").hexdigest()
    assert canon.digest({"a": 1}) == hashlib.sha256(b'{"a":1}').hexdigest()
    assert len(canon.digest({"x": "y"})) == 64


def test_digest_excluding_removes_only_top_level_keys():
    value = {"keep": 1, "canonical_hash": "0" * 64, "nested": {"canonical_hash": "stay"}}
    filtered = {"keep": 1, "nested": {"canonical_hash": "stay"}}
    assert canon.digest_excluding(value, frozenset({"canonical_hash"})) == canon.digest(filtered)


def test_non_finite_numbers_rejected():
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(canon.CanonError):
            canon.canonicalize({"n": bad})
    for text in ("NaN", "Infinity", "-Infinity", "1e999"):
        with pytest.raises(canon.CanonError):
            canon.load_json_strict(text)


def test_duplicate_keys_rejected():
    with pytest.raises(canon.CanonError):
        canon.load_json_strict('{"a": 1, "a": 2}')


def test_lone_surrogate_rejected():
    with pytest.raises(canon.CanonError):
        canon.load_json_strict('"\\ud800"')


def test_malformed_json_and_bad_utf8_rejected():
    with pytest.raises(canon.CanonError):
        canon.load_json_strict("{not json")
    with pytest.raises(canon.CanonError):
        canon.load_json_bytes(b"\xff\xfe{}")


def test_non_string_key_rejected():
    with pytest.raises(canon.CanonError):
        canon.canonicalize({1: "x"})
