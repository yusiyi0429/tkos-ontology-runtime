"""Pure analysis of the tkos.workspace/0.2 append-only stream.

This module performs no I/O and never consults domains or roles. Every rule is
derived only from the immutable scene stream, so the write path, the read
projections and the source fence in ``workspace_v02_guard`` share exactly one
interpretation. Source access is: scene membership first, then source ownership,
then an exact-version share; there is no traversal from a shared version to the
source's other versions and no role (including CEO) overrides privacy.
"""
from __future__ import annotations

SOURCE_VERSION_KINDS = {"source_version", "source_correct"}
FIELD_KINDS = {"source_add", "source_version", "source_correct", "source_withdraw",
               "source_share", "source_unshare"}


def event_index(history: list[dict]) -> dict[str, dict]:
    return {str(row["event_id"]): row for row in history}


def scene_seed(history: list[dict]) -> dict | None:
    if not history or history[0]["kind"] != "scene_create":
        return None
    return history[0]["payload"]


def members(seed: dict) -> list[str]:
    return [seed["owner_principal_id"], *seed.get("participant_principal_ids", [])]


def member_owner(seed: dict, principal_id: str, principal_type: str) -> str | None:
    """Return the human member whose access applies, or None for non-members.

    An explicitly bound Agent inherits exactly its owner's current access; the
    Agent never gains independent rights and never becomes a scene member.
    """
    people = set(members(seed))
    if principal_type == "human":
        return principal_id if principal_id in people else None
    if principal_type == "agent":
        for binding in seed.get("agent_bindings", []):
            if binding["agent_principal_id"] == principal_id and binding["owner_principal_id"] in people:
                return binding["owner_principal_id"]
    return None


def bound_agent(seed: dict, principal_id: str) -> dict | None:
    return next((item for item in seed.get("agent_bindings", [])
                 if item["agent_principal_id"] == principal_id), None)


def source_states(history: list[dict]) -> dict[str, dict]:
    """Fold the stream into append-only source states keyed by source_id.

    psycopg returns uuid columns as ``uuid.UUID``; every stream id is normalized
    to its canonical string here so the same fold works for SQL rows and for
    plain dicts.
    """
    states: dict[str, dict] = {}
    for row in history:
        kind, payload = row["kind"], row["payload"]
        if kind == "source_add":
            states[str(row["event_id"])] = {
                "add": row, "versions": [], "withdrawn_source": None,
                "withdrawn_versions": {}, "shares": {}, "corrections": {},
            }
            continue
        if kind == "source_version" or kind == "source_correct":
            state = states.get(str(payload["source_id"]))
            if state is None:
                continue
            state["versions"].append(row)
            if kind == "source_correct":
                state["corrections"][str(row["event_id"])] = str(payload["corrects_event_id"])
            continue
        if kind == "source_withdraw":
            state = states.get(str(payload["source_id"]))
            if state is None:
                continue
            if payload.get("version_event_id"):
                state["withdrawn_versions"][str(payload["version_event_id"])] = row
            else:
                state["withdrawn_source"] = row
            continue
        if kind == "source_share":
            state = states.get(str(payload["source_id"]))
            if state is None:
                continue
            state["shares"][str(row["event_id"])] = {
                "event": row, "active": True, "unshare": None,
                "version_event_id": str(payload["version_event_id"]),
                "grantee": str(payload["share_to_principal_id"]),
            }
            continue
        if kind == "source_unshare":
            share = next((item for state in states.values()
                          for item in state["shares"].values()
                          if str(item["event"]["event_id"]) == str(payload["share_event_id"])), None)
            if share is not None:
                share["active"] = False
                share["unshare"] = row
    return states


def version_by_id(state: dict, version_event_id: str) -> dict | None:
    return next((row for row in state["versions"]
                 if str(row["event_id"]) == str(version_event_id)), None)


def source_owner(state: dict) -> str:
    return state["add"]["payload"]["owner_principal_id"]


def version_withdrawn(state: dict, version_event_id: str) -> bool:
    return bool(state["withdrawn_source"] or str(version_event_id) in state["withdrawn_versions"])


def access_basis(state: dict, version_event_id: str, reader_human: str) -> str | None:
    """Owner / exact share / None. Withdrawn material always returns None."""
    if version_withdrawn(state, version_event_id):
        return None
    if source_owner(state) == reader_human:
        return "owner"
    for share in state["shares"].values():
        if (share["active"] and share["version_event_id"] == str(version_event_id)
                and share["grantee"] == str(reader_human)):
            return "share"
    return None


def version_readable(state: dict, version_event_id: str, reader_human: str,
                     payload_hash: str | None = None) -> bool:
    row = version_by_id(state, version_event_id)
    if row is None:
        return False
    if payload_hash is not None and row["payload_hash"] != payload_hash:
        return False
    return access_basis(state, version_event_id, reader_human) is not None


def ref_readable(states: dict[str, dict], ref: dict, reader_human: str) -> bool:
    state = states.get(ref.get("source_id"))
    if state is None:
        return False
    return version_readable(state, ref["version_event_id"], reader_human,
                            ref.get("payload_hash"))


def draft_readable(states: dict[str, dict], draft_payload: dict, reader_human: str) -> bool:
    for item in draft_payload.get("items", []):
        for citation in item.get("citations", []):
            if not ref_readable(states, {
                "source_id": citation["source_id"],
                "version_event_id": citation["version_event_id"],
                "payload_hash": citation["payload_hash"],
            }, reader_human):
                return False
    return True


def active_shares(state: dict, *, version_event_id: str | None = None,
                  grantee: str | None = None) -> list[dict]:
    result = []
    for share in state["shares"].values():
        if not share["active"]:
            continue
        if version_event_id is not None and share["version_event_id"] != version_event_id:
            continue
        if grantee is not None and share["grantee"] != grantee:
            continue
        result.append(share)
    return result


def latest_decisions(history: list[dict], draft_event_id: str) -> dict[int, dict]:
    decisions: dict[int, dict] = {}
    for row in history:
        payload = row["payload"]
        if row["kind"] == "draft_decision" and str(payload["draft_event_id"]) == str(draft_event_id):
            decisions[int(payload["item_index"])] = row
    return decisions


def context_item_available(states: dict[str, dict], item: dict, reader_human: str) -> bool:
    return ref_readable(states, item, reader_human)
