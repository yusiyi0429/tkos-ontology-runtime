"""Reusable narrow source-visibility guard for tkos.workspace/0.2 sources.

Contract
--------
A private 0.2 source may back its original artifact with an exact EvidenceAsset
revision (``source_version.evidence_ref``). The authoritative link is written to
``gov_workspace_v02_assets`` together with the source version event. Generic
reads that do not know about scenes must never bypass that link:

* ``enforce_object(conn, ctx, object_id, revision_id=None)``
  - objects without a 0.2 source link are untouched (no behavior change);
  - an exact linked revision is readable only when the caller currently owns
    the source or holds an active exact-version share;
  - an object-level read is readable only when *every* revision of the object
    is linked to at least one currently readable source version.
  Denied reads raise ``NOT_FOUND`` exactly like ordinary unauthorized reads.
* ``filter_context_items`` / ``filter_context_result`` / ``filter_snapshot``
  remove linked evidence that is no longer readable from generic Context
  results and historical snapshots. A selected item is withheld as a whole
  whenever it is itself a linked artifact or names any linked artifact in its
  ``source_refs``; derived bodies are never returned with only the revoked
  inputs stripped, because derivation is not independently verifiable. Live
  requests (``explicit=True``) get an ``object_id`` marker for the reference
  they named; frozen snapshots (``explicit=False``) are scrubbed silently and
  their stored ``excluded`` list is scrubbed too, so no withheld count, marker
  or hidden id leaks to a later reader.
* ``enforce_receipt`` denies a receipt read whose stored object references
  include a linked source artifact the caller may no longer read.

The guard does not consult domains, roles or Method contracts beyond the
current principal identity, so a CEO/domain-read grant cannot bypass an
unshared personal source. It performs no writes and never fetches object-store
bytes. Linking is owner-controlled: ``workspace_v02_service`` refuses to link
an EvidenceAsset whose earliest revision was recorded by anyone other than the
source owner (or that owner's currently bound Agent), so a source creator can
never fence someone else's existing object or launder it into a share. The one
required hook outside this module for method-bound objects is
``method_access.head_access`` (and callers such as the dashboard that use it
directly); API object/revision reads are hooked in ``readers.py`` and the
EvidenceAsset download in ``routes.py``.
"""
from __future__ import annotations

from . import db
from . import workspace_v02_collaboration as collaboration
from .errors import GovernedError


def _rows(conn, statement, params):
    return conn.execute(statement, params).fetchall()


def object_links(conn, ctx, object_id: str) -> list[dict]:
    return db.jsonable(_rows(conn, """SELECT object_id, revision_id, scene_id, source_id, version_event_id
        FROM gov_workspace_v02_assets WHERE scope_id=%s AND object_id=%s""",
                 (ctx.scope_id, str(object_id))))


def _revision_ids(conn, ctx, object_id: str) -> list[str]:
    return [str(row["revision_id"]) for row in _rows(conn, """SELECT revision_id FROM gov_object_revisions
        WHERE scope_id=%s AND object_id=%s ORDER BY recorded_at, revision_id""",
                                                      (ctx.scope_id, str(object_id)))]


def _history(conn, ctx, scene_id: str, cache: dict[str, list[dict]]) -> list[dict]:
    if scene_id not in cache:
        cache[scene_id] = db.jsonable(_rows(conn, """SELECT * FROM gov_workspace_v02_events
            WHERE scope_id=%s AND scene_id=%s ORDER BY version""",
                                (ctx.scope_id, scene_id)))
    return cache[scene_id]


def _link_allowed(conn, ctx, link: dict, cache: dict[str, list[dict]]) -> bool:
    history = _history(conn, ctx, str(link["scene_id"]), cache)
    seed = collaboration.scene_seed(history)
    if seed is None:
        return False
    # Current authority, not just the historical scene binding: a revoked
    # personal-agent binding or owner appointment must also close generic
    # object/revision/download paths.
    from . import workspace_v02_service as service
    reader = service.current_member(conn, ctx, seed)
    if reader is None:
        return False
    states = collaboration.source_states(history)
    state = states.get(str(link["source_id"]))
    if state is None:
        return False
    version = collaboration.version_by_id(state, str(link["version_event_id"]))
    if version is None:
        return False
    evidence = version["payload"].get("evidence_ref")
    if not evidence or str(evidence.get("object_id")) != str(link["object_id"]) \
            or str(evidence.get("revision_id")) != str(link["revision_id"]):
        return False
    return collaboration.version_readable(
        state, str(link["version_event_id"]), reader,
        version["payload_hash"],
    )


def object_allowed(conn, ctx, object_id: str, revision_id: str | None = None,
                   cache: dict[str, list[dict]] | None = None) -> bool:
    cache = {} if cache is None else cache
    links = object_links(conn, ctx, object_id)
    if not links:
        return True
    if revision_id is not None:
        target = str(revision_id)
        return any(str(link["revision_id"]) == target and _link_allowed(conn, ctx, link, cache)
                   for link in links)
    revisions = _revision_ids(conn, ctx, object_id)
    if not revisions:
        return False
    return all(any(str(link["revision_id"]) == rid and _link_allowed(conn, ctx, link, cache)
                   for link in links)
               for rid in revisions)


def enforce_object(conn, ctx, object_id: str, revision_id: str | None = None) -> None:
    if not object_allowed(conn, ctx, object_id, revision_id):
        raise GovernedError("NOT_FOUND")


def shared_revision(conn, ctx, object_id: str, revision_id: str):
    """Return (head, revision) when an active exact-version source share grants it.

    The exact-material grant is the authorization; no domain read, Method
    grant or anchor traversal is added or required. Returns None for every
    other case so callers keep their normal authorization path unchanged.
    """
    links = object_links(conn, ctx, object_id)
    if not links:
        return None
    cache: dict[str, list[dict]] = {}
    for link in links:
        if str(link["revision_id"]) != str(revision_id):
            continue
        if not _link_allowed(conn, ctx, link, cache):
            continue
        head = conn.execute("SELECT * FROM gov_objects WHERE scope_id=%s AND object_id=%s",
                            (ctx.scope_id, str(object_id))).fetchone()
        revision = conn.execute(
            "SELECT * FROM gov_object_revisions WHERE scope_id=%s AND object_id=%s AND revision_id=%s",
            (ctx.scope_id, str(object_id), str(revision_id))).fetchone()
        if head is None or revision is None:
            return None
        return db.jsonable(head), db.jsonable(revision)
    return None


def enforce_receipt(conn, ctx, receipt: dict) -> None:
    cache: dict[str, list[dict]] = {}
    targets: list[tuple[str, str | None]] = []
    for item in receipt.get("object_versions") or []:
        if isinstance(item, dict) and item.get("object_id"):
            targets.append((str(item["object_id"]), str(item["revision_id"]) if item.get("revision_id") else None))
    if receipt.get("target_object_id"):
        targets.append((str(receipt["target_object_id"]), None))
    for object_id, revision_id in _result_refs(receipt.get("result")):
        targets.append((object_id, revision_id))
    for object_id, revision_id in targets:
        if not object_allowed(conn, ctx, object_id, revision_id, cache):
            raise GovernedError("NOT_FOUND")


def _result_refs(value, path: str = "") -> list[tuple[str, str | None]]:
    """Collect exact/head object references recorded in a receipt result."""
    found: list[tuple[str, str | None]] = []
    if isinstance(value, dict):
        if value.get("object_id"):
            found.append((str(value["object_id"]), str(value["revision_id"]) if value.get("revision_id") else None))
        for item in value.values():
            found.extend(_result_refs(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_result_refs(item))
    return found


def _item_references(entry) -> list[tuple[str, str | None]]:
    """Exact/head references that make a Context item or exclusion guarded."""
    if not isinstance(entry, dict):
        return []
    refs: list[tuple[str, str | None]] = []
    if entry.get("object_id"):
        refs.append((str(entry["object_id"]),
                     str(entry["revision_id"]) if entry.get("revision_id") else None))
    for ref in entry.get("source_refs") or []:
        if isinstance(ref, dict) and ref.get("object_id"):
            refs.append((str(ref["object_id"]),
                         str(ref["revision_id"]) if ref.get("revision_id") else None))
    return refs


def _guarded(conn, ctx, entry, cache) -> bool:
    """True when any reference of this item is a linked, unreadable artifact."""
    return any(not object_allowed(conn, ctx, object_id, revision_id, cache)
               for object_id, revision_id in _item_references(entry))


def filter_context_items(conn, ctx, selected: list[dict], excluded: list[dict],
                         *, explicit: bool = False) -> tuple[list[dict], list[dict]]:
    """Withhold whole items whose linked source inputs are no longer readable.

    ``explicit=True`` is used for a live Context request where the caller named
    the references and may receive one marker naming what it asked for.
    ``explicit=False`` is used for frozen snapshots: denied selected items are
    dropped silently, and stored exclusions that reference a now-unreadable
    linked source are scrubbed rather than copied, so no hidden id, marker or
    count survives into the response.
    """
    cache: dict[str, list[dict]] = {}
    kept, moved = [], []
    for item in selected or []:
        if isinstance(item, dict) and _guarded(conn, ctx, item, cache):
            if explicit:
                object_id = item.get("object_id")
                marker = {"reason": "source_not_authorized"}
                if object_id:
                    marker["object_id"] = str(object_id)
                moved.append(marker)
            continue
        kept.append(item)
    for entry in excluded or []:
        if explicit or not isinstance(entry, dict) or not _guarded(conn, ctx, entry, cache):
            moved.append(entry)
    return kept, moved


def filter_context_result(conn, ctx, result: dict, *, explicit: bool = True) -> dict:
    selected, excluded = filter_context_items(conn, ctx, result.get("selected") or [],
                                              result.get("excluded") or [], explicit=explicit)
    return {**result, "selected": selected, "excluded": excluded}


def filter_snapshot(conn, ctx, row) -> dict:
    value = dict(row)
    selected, excluded = filter_context_items(conn, ctx, value.get("selected") or [],
                                              value.get("excluded") or [], explicit=False)
    value["selected"], value["excluded"] = selected, excluded
    return value
