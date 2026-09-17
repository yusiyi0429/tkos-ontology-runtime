"""Current-authority projections for tkos.workspace/0.2 standalone scenes.

Every projection re-derives access from the immutable stream; no cached
authorization exists. Withdrawn or unshared material degrades to metadata or is
omitted entirely. No projection consults domains or roles.
"""
from dataclasses import replace

from . import db, method_access as access, workspace_v02_collaboration as collaboration
from . import workspace_v02_service as service
from .errors import GovernedError

CONTRACT = service.CONTRACT
SOURCE_WRITE_KINDS = {"source_add", "source_version", "source_correct",
                      "source_share", "source_unshare", "source_withdraw"}


def _visible_sources(history, seed, reader):
    states = collaboration.source_states(history)
    result = {}
    for source_id, state in states.items():
        if collaboration.source_owner(state) != reader and not any(
                collaboration.version_readable(state, version["event_id"], reader)
                for version in state["versions"]):
            continue
        result[source_id] = state
    return result


def _version_entry(state, version, reader):
    readable = collaboration.version_readable(state, version["event_id"], reader, version["payload_hash"])
    payload = version["payload"]
    entry = {"event_id": version["event_id"], "version_seq": payload["version_seq"],
             "payload_hash": version["payload_hash"], "fingerprint": payload["fingerprint"],
             "segments_hash": payload["segments_hash"], "media_type": payload["media_type"],
             "acquired_at": payload["acquired_at"], "origin_label": payload.get("origin_label"),
             "correction_of": payload.get("corrects_event_id"),
             "evidence_ref": payload.get("evidence_ref"),
             "evidence_basis": payload.get("evidence_basis"),
             "status": "available" if readable else "withdrawn",
             "segments": payload["segments"] if readable else None,
             "recorded_at": version["recorded_at"]}
    if collaboration.source_owner(state) == reader:
        entry["shares"] = [{"share_event_id": share["event"]["event_id"],
                            "version_event_id": share["version_event_id"],
                            "share_to_principal_id": share["grantee"],
                            "active": share["active"],
                            "note": share["event"]["payload"].get("note"),
                            "recorded_at": share["event"]["recorded_at"],
                            "unshared_at": share["unshare"]["recorded_at"] if share["unshare"] else None}
                           for share in state["shares"].values()]
    return entry


def _source_entry(source_id, state, reader):
    add = state["add"]["payload"]
    if collaboration.source_owner(state) == reader:
        versions = [_version_entry(state, version, reader) for version in state["versions"]]
    else:
        # A non-owner sees exactly the currently shared exact versions. Never-shared,
        # corrected-away or withdrawn revisions contribute neither ids, hashes nor origin.
        versions = [_version_entry(state, version, reader) for version in state["versions"]
                    if collaboration.version_readable(state, version["event_id"], reader)]
    return {"source_id": source_id, "system": add["system"], "external_id": add["external_id"],
            "title": add["title"], "media_type": add["media_type"], "acquired_at": add["acquired_at"],
            "origin_label": add.get("origin_label"), "source_revision": add.get("source_revision"),
            "sensitivity": add.get("sensitivity", "private"),
            "owner_principal_id": collaboration.source_owner(state),
            "recorded_by_principal_id": add.get("recorded_by_principal_id"),
            "status": "withdrawn" if state["withdrawn_source"] else "available",
            "access": "owner" if collaboration.source_owner(state) == reader else "exact_share",
            "versions": versions}


def _runs(history, states, reader):
    result = []
    for row in history:
        if row["kind"] != "agent_run":
            continue
        payload = row["payload"]
        inputs_readable = all(collaboration.ref_readable(states, ref, reader)
                              for ref in payload["input_refs"])
        # Free text on a run (purpose, error, external run id, model parameters)
        # may quote source content, so none of it is returned once any input
        # source version is no longer readable by this principal.
        entry = {"event_id": row["event_id"], "recorded_at": row["recorded_at"],
                 "actor_id": row["principal_id"],
                 "agent_principal_id": payload["agent_principal_id"],
                 "status": payload["status"], "started_at": payload["started_at"],
                 "finished_at": payload.get("finished_at"),
                 "input_withheld": not inputs_readable}
        if inputs_readable:
            entry.update(purpose=payload["purpose"], model=payload["model"],
                         external_run_id=payload.get("external_run_id"),
                         error=payload.get("error"), context_id=payload.get("context_id"),
                         input_refs=payload["input_refs"], output_refs=payload["output_refs"],
                         draft_event_id=payload.get("draft_event_id"))
        else:
            entry["reason"] = "input_source_access_revoked_or_withdrawn"
        result.append(entry)
    return result
    return result


def _drafts(history, states, reader):
    drafts, decisions = [], []
    for row in history:
        if row["kind"] != "followup_draft":
            continue
        payload = row["payload"]
        if not collaboration.draft_readable(states, payload, reader):
            drafts.append({"event_id": row["event_id"], "recorded_at": row["recorded_at"],
                           "actor_id": row["principal_id"], "status": "withheld",
                           "reason": "cited_source_not_currently_authorized"})
            continue
        effective = collaboration.latest_decisions(history, row["event_id"])
        items = []
        for index, item in enumerate(payload["items"]):
            decision = effective.get(index)
            items.append({**item, "decision": decision["payload"]["decision"] if decision else None,
                          "decision_note": decision["payload"].get("note") if decision else None,
                          "decision_event_id": decision["event_id"] if decision else None})
        drafts.append({"event_id": row["event_id"], "recorded_at": row["recorded_at"],
                       "actor_id": row["principal_id"], "status": "available",
                       "title": payload["title"], "run_event_id": payload.get("run_event_id"),
                       "items": items})
        decisions.extend({"event_id": row["event_id"], "recorded_at": row["recorded_at"],
                          "actor_id": row["principal_id"],
                          "draft_event_id": row["payload"]["draft_event_id"],
                          "item_index": row["payload"]["item_index"],
                          "decision": row["payload"]["decision"], "note": row["payload"].get("note")}
                         for row in effective.values())
    return drafts, decisions


def _operations(conn, ctx, reader, states):
    """Advisory action hints derived only from the reader's own authority.

    Availability never infers another person's private source or grant: unshare
    is offered only for grants on sources the reader owns, and the other edits
    are offered only for the reader's own sources/versions whose current state
    could actually accept the action. A withdrawn source is not advertised as
    writable, and a source whose versions are all withdrawn cannot be corrected
    or shared again until a new version exists.
    """
    owned = {source_id: state for source_id, state in states.items()
             if collaboration.source_owner(state) == reader}

    def has_open_version(state):
        return state["withdrawn_source"] is None and any(
            not collaboration.version_withdrawn(state, version["event_id"])
            for version in state["versions"])

    has_owned = bool(owned)
    has_open_source = any(state["withdrawn_source"] is None for state in owned.values())
    has_open_version = any(has_open_version(state) for state in owned.values())
    has_owned_grant = any(share["active"] for state in owned.values()
                          for share in state["shares"].values())
    has_readable = any(collaboration.version_readable(state, version["event_id"], reader)
                       for state in states.values() for version in state["versions"])
    result = []
    for kind in sorted(SOURCE_WRITE_KINDS | {"agent_run", "followup_draft", "draft_decision"}):
        reason = None
        if kind == "source_version" and not has_open_source:
            reason = "no_owned_source" if not has_owned else "owned_source_withdrawn"
        if kind in {"source_correct", "source_share"} and not has_open_version:
            if not has_owned:
                reason = "no_owned_source"
            elif not has_open_source:
                reason = "owned_source_withdrawn"
            else:
                reason = "no_active_owned_version"
        if kind == "source_withdraw" and not has_open_source:
            reason = "no_owned_source" if not has_owned else "owned_source_withdrawn"
        if kind == "source_unshare" and not has_owned_grant:
            reason = "no_owned_source" if not has_owned else "no_active_owned_grant"
        if kind == "followup_draft" and not has_readable:
            reason = "no_readable_source"
        if kind == "agent_run" and ctx.principal_type != "agent":
            reason = "agent_identity_required"
        if kind == "draft_decision" and ctx.principal_type != "human":
            reason = "human_decision_required"
        result.append({"kind": kind, "allowed": reason is None, "reason": reason,
                       "authority": "advisory_rechecked_on_submit"})
    return result


def _member_entry(conn, ctx, rows, bindings, principal_id, principal_type):
    row = rows.get(principal_id)
    current, status = False, "missing"
    if row is not None:
        display = row["display_name"]
        if not row["active"]:
            status = "inactive"
        else:
            try:
                if principal_type == "human":
                    current = bool(db._assignments(
                        conn, replace(ctx, principal_id=principal_id, principal_type="human")))
                else:
                    owner = next((item["owner_principal_id"] for item in bindings
                                  if item["agent_principal_id"] == principal_id), None)
                    if owner is not None:
                        access.personal_agent(conn, ctx, principal_id, owner)
                        current = True
            except GovernedError:
                current = False
            status = "current" if current else "not_currently_appointed"
    else:
        display = None
    return {"principal_id": principal_id, "principal_type": principal_type,
            "display_name": display, "current": current, "status": status}


def _member_directory(conn, ctx, seed):
    """Display names for exactly the members this projection already reveals.

    Bounded to the scene owner, participants and bound Agents: no scope-wide
    directory is queried. Names are display-only; ``current`` describes the
    principal's current appointment/binding and never grants authority.
    Missing or deactivated members keep an explicit null/status.
    """
    bindings = seed.get("agent_bindings", [])
    owner = seed["owner_principal_id"]
    participants = list(seed.get("participant_principal_ids", []))
    ids = list(dict.fromkeys([owner, *participants, *[item["agent_principal_id"] for item in bindings]]))
    rows = {}
    if ids:
        for row in conn.execute(
                """SELECT principal_id, display_name, active FROM gov_principals
                   WHERE scope_id=%s AND principal_id=ANY(%s::uuid[])""",
                (ctx.scope_id, ids)):
            rows[str(row["principal_id"])] = row
    return {"authority": "display_only_not_authorization",
            "owner": _member_entry(conn, ctx, rows, bindings, owner, "human"),
            "participants": [_member_entry(conn, ctx, rows, bindings, pid, "human")
                             for pid in participants],
            "agents": [{"agent": _member_entry(conn, ctx, rows, bindings,
                                                item["agent_principal_id"], "agent"),
                        "owner": _member_entry(conn, ctx, rows, bindings,
                                               item["owner_principal_id"], "human")}
                       for item in bindings]}


def read(conn, ctx, scene_id):
    history = service.rows(conn, ctx, scene_id)
    seed, reader = service.scene_of(conn, ctx, scene_id, history)
    states = collaboration.source_states(history)
    visible = _visible_sources(history, seed, reader)
    drafts, decisions = _drafts(history, states, reader)
    result = {"contract_version": CONTRACT, "scene_id": scene_id, "version": history[-1]["version"],
              "scene": {"scene_type": seed["scene_type"], "external_id": seed["external_id"],
                        "title": seed["title"], "owner_principal_id": seed["owner_principal_id"],
                        "participant_principal_ids": seed["participant_principal_ids"],
                        "agent_bindings": seed["agent_bindings"],
                        "members": _member_directory(conn, ctx, seed),
                        "created_event_id": history[0]["event_id"], "created_at": history[0]["recorded_at"]},
              "sources": [_source_entry(source_id, state, reader) for source_id, state in visible.items()],
              "runs": _runs(history, states, reader), "drafts": drafts, "decisions": decisions,
              "operations": _operations(conn, ctx, reader, states), "formal_effect": "none"}
    return db.jsonable(result)


def summary(history):
    seed = collaboration.scene_seed(history)
    return {"scene_id": history[0]["scene_id"], "scene_type": seed["scene_type"],
            "external_id": seed["external_id"], "title": seed["title"],
            "owner_principal_id": seed["owner_principal_id"],
            "participant_principal_ids": seed["participant_principal_ids"],
            "version": history[-1]["version"], "created_at": history[0]["recorded_at"]}


def list_scenes(conn, ctx, scene_type=None, after=None, limit=25):
    found = conn.execute("""SELECT scene_id FROM gov_workspace_v02_events
        WHERE scope_id=%s AND kind='scene_create'
        AND (%s::text IS NULL OR payload->>'scene_type'=%s)
        AND (%s::uuid IS NULL OR scene_id>%s::uuid) ORDER BY scene_id""",
        (ctx.scope_id, scene_type, scene_type, after, after)).fetchall()
    items = []
    for row in found:
        history = service.rows(conn, ctx, str(row["scene_id"]))
        try:
            service.scene_of(conn, ctx, str(row["scene_id"]), history)
        except GovernedError as exc:
            if exc.code == "NOT_FOUND":
                continue
            raise
        items.append(summary(history))
        if len(items) > limit:
            break
    return {"items": items[:limit],
            "next_after": items[limit - 1]["scene_id"] if len(items) > limit else None}


def context_view(conn, ctx, row):
    history = service.rows(conn, ctx, str(row["scene_id"]))
    seed, reader = service.scene_of(conn, ctx, str(row["scene_id"]), history)
    states = collaboration.source_states(history)
    creator = str(row["principal_id"]) == reader
    available, hidden = [], []
    for item in row["selected"]:
        if collaboration.context_item_available(states, item, reader):
            available.append({**db.jsonable(item), "status": "available", "reason": None})
        else:
            hidden.append(item)
    items = list(available)
    if creator:
        # The creator already knows the references it captured and may keep
        # per-item withdrawal metadata (still without any body).
        for item in hidden:
            items.append({"source_id": item.get("source_id"),
                          "version_event_id": item.get("version_event_id"),
                          "status": "withheld", "reason": "source_access_revoked_or_withdrawn",
                          "segments": None})
    complete = not hidden and bool(items)
    # ``purpose`` is caller-authored free text that can quote source content.
    # It is returned only while every captured item is currently readable;
    # otherwise it is withheld from every reader, including the creator.
    # Never-authorized readers additionally receive no per-item count, while a
    # partial reader receives only the available items plus one withheld flag.
    if not creator and not complete:
        items = list(available)
    return db.jsonable({"contract_version": CONTRACT, "context_id": row["context_id"],
                        "scene_id": row["scene_id"],
                        "purpose": row["purpose"] if complete else None,
                        "created_by_principal_id": row["principal_id"],
                        "recorded_at": row["recorded_at"],
                        "complete": complete, "withheld": bool(hidden), "items": items})


def read_context(conn, ctx, context_id):
    row = conn.execute("SELECT * FROM gov_workspace_v02_contexts WHERE scope_id=%s AND context_id=%s",
                       (ctx.scope_id, str(context_id))).fetchone()
    if row is None:
        service.fail("NOT_FOUND")
    history = service.rows(conn, ctx, str(row["scene_id"]))
    service.scene_of(conn, ctx, str(row["scene_id"]), history)
    return context_view(conn, ctx, row)
