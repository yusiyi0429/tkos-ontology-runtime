"""Private source import driver for tkos.workspace/0.2.

The two real meeting snapshots are private. This module may read their bytes
only to (a) compute their sha256 fingerprint, (b) derive bounded speaker/text
segments for the Runtime, and (c) report counts, never content. Every reported
field is a hash or a count; parsed text is sent only to the Runtime HTTP API
and never printed, logged or written to public artifacts.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import re

TEXT_KEYS = ("text", "content", "sentence", "utterance", "body", "message", "line")
SPEAKER_KEYS = ("speaker", "speaker_name", "name", "author", "from", "sender", "role")
TIME_KEYS = ("occurred_at", "timestamp", "time", "start_time", "start", "datetime", "create_time")
DOCUMENT_TIME_KEYS = ("occurred_at", "created_at", "updated_at", "modified_at", "create_time", "update_time")
LIST_KEYS = ("segments", "transcript", "lines", "dialogue", "messages", "items", "content", "records", "list")

SPEAKER_RE = re.compile(r"^\s*(?:[\[(]\s*\d{1,2}:\d{2}(?::\d{2})?\s*[\])]\s*)?([^:：\n]{1,80})[:：]\s*(.+)$")
MARKUP_RE = re.compile(r"</?[a-zA-Z][^>]*>")

STAT_KEYS = ("lines", "dialogue_lines", "continuation_lines", "json_lines", "parsed")


def empty_stats():
    return {key: 0 for key in STAT_KEYS}


def _strip_markup(text: str) -> str:
    # Structural extraction only: block ends become newlines, tags/entities removed.
    text = re.sub(r"<\s*br\s*/?\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</\s*(p|div|li|h[1-6]|tr)\s*>", "\n", text, flags=re.IGNORECASE)
    text = MARKUP_RE.sub("", text)
    return html.unescape(text)


def file_report(path: Path) -> dict:
    raw = path.read_bytes()
    return {"file": path.name, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}


def parse_text(text: str) -> tuple[list[dict], dict]:
    segments, stats = [], empty_stats()
    for raw in text.splitlines():
        stats["lines"] += 1
        line = raw.strip()
        if not line:
            continue
        match = SPEAKER_RE.match(line)
        if match:
            segments.append({"speaker": match.group(1).strip(), "text": match.group(2).strip(),
                             "occurred_at": None})
            stats["dialogue_lines"] += 1
        elif segments:
            segments[-1]["text"] += "\n" + line
            stats["continuation_lines"] += 1
        else:
            segments.append({"speaker": None, "text": line, "occurred_at": None})
    return segments, stats


def _first(entry: dict, keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _document(payload) -> dict | None:
    """Known external wrapper: {ok, identity, data:{document:{content,...}}}"""
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("document"), dict):
        return data["document"]
    if isinstance(payload.get("document"), dict):
        return payload["document"]
    return None


def _parse_document(payload) -> tuple[list[dict], dict] | None:
    document = _document(payload)
    if document is None:
        return None
    stats = empty_stats()
    content = document.get("content")
    markup = isinstance(content, str) and bool(MARKUP_RE.search(content))
    body = _strip_markup(content) if markup else content
    segments: list[dict] = []
    if isinstance(body, str) and body.strip():
        segments, text_stats = parse_text(body)
        stats.update({key: text_stats[key] for key in ("lines", "dialogue_lines", "continuation_lines")})
    elif isinstance(body, list):
        for candidate in _candidate_lists(body):
            parsed = _parse_entries(candidate)
            if len(parsed) > len(segments):
                segments = parsed
    stats["parsed"] = len(segments)
    stats["format"] = "markup" if markup else "text"
    stats["document_id"] = document.get("document_id")
    stats["document_revision"] = document.get("revision_id")
    stats["document_time"] = next((document[key] for key in DOCUMENT_TIME_KEYS
                                   if isinstance(document.get(key), str) and document[key].strip()), None)
    return segments, stats


def _parse_entries(entries: list) -> list[dict]:
    parsed: list[dict] = []
    for entry in entries:
        if isinstance(entry, str) and entry.strip():
            parsed.append({"speaker": None, "text": entry.strip(), "occurred_at": None})
        elif isinstance(entry, dict):
            body = _first(entry, TEXT_KEYS)
            if body:
                parsed.append({"speaker": _first(entry, SPEAKER_KEYS), "text": body,
                               "occurred_at": _first(entry, TIME_KEYS)})
    return parsed


def _candidate_lists(value, depth=0) -> list[list]:
    if depth > 6:
        return []
    found: list[list] = []
    if isinstance(value, list):
        found.append(value)
    elif isinstance(value, dict):
        for key, item in value.items():
            if key in LIST_KEYS and isinstance(item, list):
                found.append(item)
        for item in value.values():
            found.extend(_candidate_lists(item, depth + 1))
    return found


def parse_json(text: str) -> tuple[list[dict], dict]:
    payload = None
    stats = empty_stats()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            stats["json_lines"] += 1
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if payload is None:
                payload = []
            payload.append(item)
    if isinstance(payload, dict):
        document = _parse_document(payload)
        if document is not None:
            return document
    segments: list[dict] = []
    for candidate in _candidate_lists(payload):
        parsed = _parse_entries(candidate)
        if len(parsed) > len(segments):
            segments = parsed
    stats["parsed"] = len(segments)
    stats["format"] = "text"
    if not segments:
        # A whole-document fallback that never prints the text.
        if isinstance(payload, str) and payload.strip():
            segments, text_stats = parse_text(payload)
            stats.update({key: text_stats[key] for key in ("lines", "dialogue_lines", "continuation_lines")})
        elif isinstance(payload, dict):
            for value in payload.values():
                if isinstance(value, str) and len(value.splitlines()) > 1:
                    segments, text_stats = parse_text(value)
                    stats.update({key: text_stats[key] for key in ("lines", "dialogue_lines", "continuation_lines")})
                    break
    stats["parsed"] = len(segments)
    return segments, stats


def parse_source(path: Path) -> tuple[list[dict], dict]:
    raw = path.read_bytes()
    if path.suffix.lower() in {".json", ".jsonl"}:
        segments, stats = parse_json(raw.decode("utf-8", errors="replace"))
    else:
        segments, stats = parse_text(raw.decode("utf-8", errors="replace"))
    for key in STAT_KEYS:
        stats.setdefault(key, 0)
    stats["segments"] = len(segments)
    stats["speakers"] = len({item["speaker"] for item in segments if item["speaker"]})
    stats["occurred_at_values"] = sum(1 for item in segments if item["occurred_at"])
    return segments, stats


SOURCE_FILES = (
    {"name": "main-transcript.txt", "system": "feishu", "external_id": "main-transcript",
     "media_type": "text/plain", "origin_label": "meeting-main-transcript"},
    {"name": "followup-document.json", "system": "feishu", "external_id": "followup-document",
     "media_type": "application/json", "origin_label": "meeting-followup-document"},
)


def verify_no_private_text(transcript: Path, sources_dir: Path, window: int = 40) -> bool:
    """True when no private source slice appears in the public transcript.

    Reports only a boolean; never prints or returns source content.
    """
    if not transcript.exists():
        return True
    haystack = transcript.read_text(encoding="utf-8", errors="replace")
    for descriptor in SOURCE_FILES:
        path = sources_dir / descriptor["name"]
        if not path.exists():
            continue
        candidates = [path.read_bytes().decode("utf-8", errors="replace")]
        segments, _ = parse_source(path)
        candidates.extend(item["text"] for item in segments)
        for text in candidates:
            for start in range(0, max(len(text) - window, 0) + 1, window):
                slice_ = text[start:start + window]
                if slice_.strip() and slice_ in haystack:
                    return False
    return True


def import_private_sources(scenes, actor: str, scene_id: str, sources_dir: Path) -> list[dict]:
    """Import the private snapshots through legitimate HTTP actions.

    Private ingestion is segment-only: the raw bytes never become a
    domain-readable EvidenceAsset, so no domain reader can observe the private
    text before any source fence exists. ``fingerprint`` remains the sha256 of
    the original local artifact for provenance. Transport logging carries only
    hashes/counts. Returns hash/count reports only; parsed text travels to the
    Runtime and is not returned to the caller.

    ``acquired_at`` uses the external document's own timestamp when present;
    otherwise the local copy's file mtime is recorded with an explicit basis
    label and is never presented as the original meeting/event time.
    """
    reports = []
    for descriptor in SOURCE_FILES:
        path = sources_dir / descriptor["name"]
        if not path.exists():
            continue
        report = file_report(path)
        segments, stats = parse_source(path)
        if not segments:
            raise AssertionError("private source parsed no segments; format support must be fixed")
        document_time = stats.get("document_time")
        if isinstance(document_time, str) and document_time.strip():
            acquired_at = document_time.strip()
            acquired_at_basis = "external-document-metadata"
        else:
            acquired_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
            acquired_at_basis = "local-copy-file-mtime"
        document_id = stats.get("document_id")
        external_id = str(document_id) if document_id else descriptor["external_id"]
        document_revision = stats.get("document_revision")
        source_revision = str(document_revision) if document_revision not in (None, "") else None
        source_id, source_receipt = scenes.add_source(
            actor, scene_id, system=descriptor["system"], external_id=external_id,
            title="Private source " + descriptor["name"], media_type=descriptor["media_type"],
            acquired_at=acquired_at, origin_label=descriptor["origin_label"],
            source_revision=source_revision, private=True)
        version_receipt = scenes.add_version(
            actor, scene_id, source_id, segments=segments, fingerprint=report["sha256"],
            acquired_at=acquired_at, media_type=descriptor["media_type"],
            origin_label=descriptor["origin_label"], private=True)
        reports.append({**report, **{key: stats.get(key, 0) for key in
                                     ("lines", "dialogue_lines", "continuation_lines",
                                      "segments", "speakers", "occurred_at_values")},
                        "system": descriptor["system"], "external_id": external_id,
                        "source_revision": source_revision, "format": stats.get("format", "text"),
                        "acquired_at": acquired_at, "acquired_at_basis": acquired_at_basis,
                        "source_id": source_id,
                        "source_receipt_id": source_receipt["receipt_id"],
                        "version_receipt_id": version_receipt["receipt_id"],
                        "version_event_id": version_receipt["result"]["event_id"],
                        "evidence_bytes_exposed": False})
    return reports
