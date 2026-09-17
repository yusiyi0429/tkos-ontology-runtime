"""Durable tkos.workspace/0.2 writes under the current-authority transaction.

The scope authentication fence serializes CAS and idempotency. The append-only
event, optional source-fence link and ActionReceipt commit together. No Method
object, MethodRun, task or formal business effect is written here.
"""
from dataclasses import replace
import hashlib
import json
from uuid import uuid4

from psycopg.types.json import Jsonb

from . import db, method_access as access
from . import workspace_v02_collaboration as collaboration
from .errors import GovernedError

CONTRACT = "tkos.workspace/0.2"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


def fail(code="INVALID_REQUEST", message=None):
    raise GovernedError(code, message) if message else GovernedError(code)


def rows(conn, ctx, scene_id):
    # One normalization boundary: psycopg UUID/datetime values become canonical
    # strings so every fold, projection and id comparison sees the same shapes.
    return db.jsonable(conn.execute(
        "SELECT * FROM gov_workspace_v02_events WHERE scope_id=%s AND scene_id=%s ORDER BY version",
        (ctx.scope_id, scene_id)).fetchall())


def event_by_id(history, event_id):
    row = next((item for item in history if str(item["event_id"]) == str(event_id)), None)
    if row is None:
        fail("NOT_FOUND")
    return row


def scene_of(conn, ctx, scene_id, history):
    seed = collaboration.scene_seed(history)
    if seed is None:
        fail("NOT_FOUND")
    reader = current_member(conn, ctx, seed)
    if reader is None:
        fail("NOT_FOUND")
    return seed, reader


def current_member(conn, ctx, seed):
    """Resolve scene membership against *current* authority.

    Historical scene/agent bindings alone never keep access alive: an Agent
    member must still have its current personal-agent binding and its owner's
    current appointment, exactly like named responsibility checks elsewhere.
    """
    reader = collaboration.member_owner(seed, ctx.principal_id, ctx.principal_type)
    if reader is None:
        return None
    if ctx.principal_type == "agent":
        try:
            _current_agent(conn, ctx, ctx.principal_id, reader)
        except GovernedError:
            return None
    else:
        try:
            assignments = db._assignments(conn, ctx)
        except GovernedError:
            return None
        if not assignments:
            return None
    return reader


def _current_human(conn, ctx, principal_id):
    principal = conn.execute("SELECT principal_type, active FROM gov_principals WHERE scope_id=%s AND principal_id=%s",
                             (ctx.scope_id, str(principal_id))).fetchone()
    if principal is None or not principal["active"] or principal["principal_type"] != "human":
        fail("INVALID_REQUEST", "Scene members must be current human principals.")
    member = replace(ctx, principal_id=str(principal_id), principal_type="human")
    try:
        assignments = db._assignments(conn, member)
    except GovernedError:
        fail("INVALID_REQUEST", "Scene members require a current assignment.")
    if not assignments:
        fail("INVALID_REQUEST", "Scene members require a current assignment.")


def _current_agent(conn, ctx, agent_id, owner_id):
    try:
        access.personal_agent(conn, ctx, str(agent_id), str(owner_id))
    except GovernedError:
        fail("FORBIDDEN", "The Agent binding is not current for this owner.")


def validate_create(conn, ctx, history, event):
    if history:
        fail("VERSION_CONFLICT", "This scene already exists.")
    if ctx.principal_type != "human":
        fail("FORBIDDEN", "Scenes are created by the responsible person, never by an Agent.")
    if event["owner_principal_id"] != ctx.principal_id:
        fail("FORBIDDEN", "A scene owner must be the authenticated creator.")
    for principal_id in event["participant_principal_ids"]:
        _current_human(conn, ctx, principal_id)
    for binding in event["agent_bindings"]:
        if binding["owner_principal_id"] not in collaboration.members(event):
            fail("INVALID_REQUEST", "Agent bindings must name a scene member as owner.")
        _current_agent(conn, ctx, binding["agent_principal_id"], binding["owner_principal_id"])
    duplicate = conn.execute("""SELECT 1 FROM gov_workspace_v02_events WHERE scope_id=%s AND kind='scene_create'
        AND payload->>'scene_type'=%s AND payload->>'external_id'=%s""",
        (ctx.scope_id, event["scene_type"], event["external_id"])).fetchone()
    if duplicate:
        fail("VERSION_CONFLICT", "This external scene ID is already registered.")
    return dict(event)


def _source_state(history, source_id):
    state = collaboration.source_states(history).get(str(source_id))
    if state is None:
        fail("NOT_FOUND", "The source was not found in this scene.")
    return state


def _require_source_owner(state, reader):
    if collaboration.source_owner(state) != reader:
        fail("FORBIDDEN", "Only the source owner or their bound Agent may change this source.")


def _require_exact_evidence(conn, ctx, ref):
    """Protocol-neutral exact EvidenceAsset check (0.3+ or later bindings allowed)."""
    head = db.object_row(conn, ctx, str(ref["object_id"]))
    if head["object_type"] != "EvidenceAsset":
        fail("INVALID_REQUEST", "evidence_ref must name an EvidenceAsset.")
    revision = db.revision_row(conn, ctx, str(ref["object_id"]), str(ref["revision_id"]))
    if revision["payload_hash"] != ref["payload_hash"]:
        fail("VERSION_CONFLICT", "The evidence revision does not match its payload hash.")
    return head


def evidence_owner_allowed(conn, ctx, object_id, reader) -> bool:
    """Only the EvidenceAsset's own human creator (or their bound Agent) may fence it.

    Otherwise a source creator could link someone else's existing object and
    both censor it for its owner and launder it into a private share.
    """
    row = conn.execute("""SELECT r.recorded_by, p.principal_type FROM gov_object_revisions r
        JOIN gov_principals p ON p.scope_id=r.scope_id AND p.principal_id=r.recorded_by
        WHERE r.scope_id=%s AND r.object_id=%s ORDER BY r.recorded_at, r.revision_id LIMIT 1""",
        (ctx.scope_id, str(object_id))).fetchone()
    if row is None:
        return False
    creator = str(row["recorded_by"])
    if creator == reader:
        return True
    if row["principal_type"] == "agent":
        try:
            access.personal_agent(conn, ctx, creator, reader)
            return True
        except GovernedError:
            return False
    return False


def _link_evidence(conn, ctx, stored, scene_id, version_event_id, reader):
    ref = stored.get("evidence_ref")
    if not ref:
        return False
    _require_exact_evidence(conn, ctx, ref)
    if not evidence_owner_allowed(conn, ctx, ref["object_id"], reader):
        fail("FORBIDDEN", "Only the owner of the existing EvidenceAsset may link it to a private source.")
    conn.execute("""INSERT INTO gov_workspace_v02_assets
        (scope_id, object_id, revision_id, scene_id, source_id, version_event_id)
        VALUES (%s, %s, %s, %s, %s, %s)""",
        (ctx.scope_id, str(ref["object_id"]), str(ref["revision_id"]), scene_id,
         str(stored["source_id"]), version_event_id))
    return True


def _version_payload(stored, event, state):
    stored = {**event, "version_seq": len(state["versions"]) + 1,
              "segments_hash": digest(event["segments"]),
              "recorded_by_principal_id": stored.get("recorded_by_principal_id")}
    if event.get("evidence_ref"):
        # Explicit semantics: linked bytes were already intentionally readable
        # under the domain protocol before this link; linking applies the
        # source fence. Private ingestion must not use evidence_ref.
        stored["evidence_basis"] = "domain_shared_original"
    return stored


def validate_event(conn, ctx, seed, reader, history, event):
    kind = event["kind"]
    states = collaboration.source_states(history)
    stored = {**event, "recorded_by_principal_id": ctx.principal_id}
    if kind == "source_add":
        duplicate = conn.execute("""SELECT 1 FROM gov_workspace_v02_events WHERE scope_id=%s AND kind='source_add'
            AND payload->>'system'=%s AND payload->>'external_id'=%s""",
            (ctx.scope_id, event["system"], event["external_id"])).fetchone()
        if duplicate:
            fail("VERSION_CONFLICT", "This external source ID is already registered in this scope.")
        stored["owner_principal_id"] = reader
        return stored
    if kind == "source_version":
        state = _source_state(history, event["source_id"])
        _require_source_owner(state, reader)
        if state["withdrawn_source"]:
            fail("INVALID_STATE", "A withdrawn source cannot receive new versions.")
        return _version_payload(stored, event, state)
    if kind == "source_correct":
        state = _source_state(history, event["source_id"])
        _require_source_owner(state, reader)
        if state["withdrawn_source"]:
            fail("INVALID_STATE", "A withdrawn source cannot receive corrections.")
        corrected = collaboration.version_by_id(state, str(event["corrects_event_id"]))
        if corrected is None:
            fail("NOT_FOUND", "The corrected version was not found in this source.")
        if collaboration.version_withdrawn(state, str(event["corrects_event_id"])):
            fail("INVALID_STATE", "A withdrawn version cannot be corrected.")
        return _version_payload(stored, event, state)
    if kind == "source_withdraw":
        state = _source_state(history, event["source_id"])
        _require_source_owner(state, reader)
        if event.get("version_event_id"):
            if collaboration.version_by_id(state, str(event["version_event_id"])) is None:
                fail("NOT_FOUND", "The version was not found in this source.")
            if collaboration.version_withdrawn(state, str(event["version_event_id"])):
                fail("INVALID_STATE", "This source version is already withdrawn.")
        elif state["withdrawn_source"]:
            fail("INVALID_STATE", "This source is already withdrawn.")
        stored["source_owner_principal_id"] = collaboration.source_owner(state)
        return stored
    if kind == "source_share":
        state = _source_state(history, event["source_id"])
        _require_source_owner(state, reader)
        version = collaboration.version_by_id(state, str(event["version_event_id"]))
        if version is None:
            fail("NOT_FOUND", "The shared version was not found in this source.")
        if collaboration.version_withdrawn(state, str(event["version_event_id"])):
            fail("INVALID_STATE", "A withdrawn version cannot be shared.")
        if version["payload_hash"] != event["payload_hash"]:
            fail("VERSION_CONFLICT", "The shared version has changed.")
        if event["share_to_principal_id"] not in collaboration.members(seed):
            fail("INVALID_REQUEST", "Exact-material grants are limited to current scene members.")
        if event["share_to_principal_id"] == collaboration.source_owner(state):
            fail("INVALID_REQUEST", "The source owner already reads their own source.")
        if any(share["active"] and share["version_event_id"] == event["version_event_id"]
               and share["grantee"] == event["share_to_principal_id"]
               for share in state["shares"].values()):
            fail("INVALID_STATE", "This exact version is already shared with that person.")
        stored["shared_by_principal_id"] = ctx.principal_id
        stored["shared_by_human_principal_id"] = reader
        return stored
    if kind == "source_unshare":
        share = next((item for state in states.values() for item in state["shares"].values()
                      if str(item["event"]["event_id"]) == str(event["share_event_id"])), None)
        if share is None:
            fail("NOT_FOUND", "The grant was not found in this scene.")
        if not share["active"]:
            fail("INVALID_STATE", "This grant is already withdrawn.")
        if share["event"]["payload"].get("shared_by_human_principal_id") != reader:
            fail("FORBIDDEN", "Only the granting owner or their bound Agent may withdraw this grant.")
        stored["revoked_by_principal_id"] = ctx.principal_id
        return stored
    if kind == "agent_run":
        if ctx.principal_type != "agent" or event["agent_principal_id"] != ctx.principal_id:
            fail("FORBIDDEN", "Agent runs must be recorded by that exact Agent identity.")
        _current_agent(conn, ctx, ctx.principal_id, reader)
        context = conn.execute("SELECT * FROM gov_workspace_v02_contexts WHERE scope_id=%s AND context_id=%s",
                               (ctx.scope_id, str(event["context_id"]))).fetchone()
        context_principal = str(context["principal_id"]) if context is not None else None
        # The snapshot must belong to this run's owner or to the acting Agent
        # itself (an Agent-created snapshot with its current binding); another
        # person's or another Agent's snapshot is never accepted.
        owned = context_principal == reader or (
            ctx.principal_type == "agent" and context_principal == ctx.principal_id)
        if context is None or str(context["scene_id"]) != str(history[-1]["scene_id"]) or not owned:
            fail("NOT_FOUND", "The run Context is unavailable.")
        snapshot = {(str(item.get("source_id")), str(item.get("version_event_id"))):
                    str(item.get("payload_hash")) for item in context["selected"]}
        for item in context["selected"]:
            if not collaboration.context_item_available(states, item, reader):
                fail("FORBIDDEN", "The run Context contains material that is no longer readable.")
        for ref in event["input_refs"]:
            # A run may only cite exact inputs captured in its own immutable
            # Context snapshot; it cannot claim an arbitrary readable ref.
            if snapshot.get((str(ref["source_id"]), str(ref["version_event_id"]))) != ref["payload_hash"]:
                fail("INVALID_REQUEST", "Run inputs must be exact items of its Context snapshot.")
            state = states.get(str(ref["source_id"]))
            if state is None or not collaboration.version_readable(
                    state, str(ref["version_event_id"]), reader, ref["payload_hash"]):
                fail("FORBIDDEN", "Every run input must be a currently readable exact source version.")
        stored["owner_principal_id"] = reader
        stored["input_snapshot_id"] = str(event["context_id"])
        return stored
    if kind == "followup_draft":
        states_local = states
        refs_by_hash = {}
        for item in event["items"]:
            for citation in item["citations"]:
                state = states_local.get(str(citation["source_id"]))
                if state is None:
                    fail("NOT_FOUND", "A cited source was not found in this scene.")
                version = collaboration.version_by_id(state, str(citation["version_event_id"]))
                if version is None:
                    fail("NOT_FOUND", "A cited source version was not found in this scene.")
                if version["payload_hash"] != citation["payload_hash"]:
                    fail("VERSION_CONFLICT", "A citation does not match its immutable source version.")
                if not collaboration.version_readable(state, str(citation["version_event_id"]), reader,
                                                       citation["payload_hash"]):
                    fail("FORBIDDEN", "Every cited source version must be currently readable by the author.")
                segments = version["payload"]["segments"]
                if citation["segment_index"] >= len(segments):
                    fail("INVALID_REQUEST", "A citation segment index is out of range.")
                if citation["quote"] not in segments[citation["segment_index"]]["text"]:
                    fail("INVALID_REQUEST", "Every citation must quote the exact source segment.")
                refs_by_hash[(citation["source_id"], citation["version_event_id"])] = citation
        if event.get("run_event_id"):
            run = event_by_id(history, str(event["run_event_id"]))
            if run["kind"] != "agent_run" or run["payload"].get("owner_principal_id") != reader:
                fail("INVALID_REQUEST", "The referenced run must belong to this human owner.")
            run_inputs = {(ref["source_id"], ref["version_event_id"]) for ref in run["payload"]["input_refs"]}
            if not set(refs_by_hash) <= run_inputs:
                fail("INVALID_REQUEST", "Draft citations must come from the exact run inputs.")
        stored["owner_principal_id"] = reader
        return stored
    if kind == "draft_decision":
        if ctx.principal_type != "human":
            fail("FORBIDDEN", "Draft decisions are personal human actions.")
        draft = event_by_id(history, str(event["draft_event_id"]))
        if draft["kind"] != "followup_draft":
            fail("INVALID_REQUEST", "Only a follow-up draft item can be decided.")
        if event["item_index"] >= len(draft["payload"]["items"]):
            fail("INVALID_REQUEST", "The draft item index is out of range.")
        for item in draft["payload"]["items"]:
            for citation in item["citations"]:
                state = states.get(str(citation["source_id"]))
                if state is None or not collaboration.version_readable(
                        state, str(citation["version_event_id"]), reader, citation["payload_hash"]):
                    fail("DEPENDENCY_MISSING", "A cited source is no longer readable; the draft body is withheld.")
        stored["owner_principal_id"] = reader
        return stored
    fail("INVALID_REQUEST", "This interaction is not part of workspace/0.2.")


def _insert_event(conn, ctx, scene_id, event_id, version, stored, payload_hash, receipt_id):
    conn.execute("""INSERT INTO gov_workspace_v02_events
        (scope_id,scene_id,event_id,version,principal_id,action_id,kind,payload,payload_hash)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (ctx.scope_id, scene_id, event_id, version, ctx.principal_id, receipt_id,
         stored["kind"], Jsonb(stored), payload_hash))


def execute(conn, ctx, command):
    body = command.model_dump(mode="json")
    event, scene_id = body["event"], body["scene_id"]
    request_hash = digest(body)
    receipt = conn.execute("SELECT * FROM gov_action_receipts WHERE scope_id=%s AND principal_id=%s AND idempotency_key=%s",
                           (ctx.scope_id, ctx.principal_id, body["idempotency_key"])).fetchone()
    if receipt:
        if receipt["request_hash"] != request_hash:
            fail("IDEMPOTENCY_CONFLICT")
        authorize_receipt(conn, ctx, receipt, replay=True)
        from .readers import frozen_receipt
        return frozen_receipt(receipt)
    history = rows(conn, ctx, scene_id)
    reader = None
    if event["kind"] == "scene_create":
        stored = validate_create(conn, ctx, history, event)
    else:
        seed, reader = scene_of(conn, ctx, scene_id, history)
        stored = validate_event(conn, ctx, seed, reader, history, event)
    if len(history) != body["expected_version"]:
        fail("VERSION_CONFLICT")
    receipt_id, event_id = str(uuid4()), str(uuid4())
    payload_hash = digest(stored)
    version = len(history) + 1
    _insert_event(conn, ctx, scene_id, event_id, version, stored, payload_hash, receipt_id)
    linked = False
    if stored["kind"] in collaboration.SOURCE_VERSION_KINDS and stored.get("evidence_ref"):
        linked = _link_evidence(conn, ctx, stored, scene_id, event_id, reader)
    result = {"contract_version": CONTRACT, "scene_id": scene_id, "event_id": event_id,
              "version": version, "kind": stored["kind"], "payload_hash": payload_hash,
              "formal_effect": "none", "evidence_linked": linked}
    source_id = stored.get("source_id") or (event_id if stored["kind"] == "source_add" else None)
    if source_id:
        result["source_id"] = str(source_id)
    from .checkpoints import checkpoint
    checkpoint("workspace_v02_before_receipt", {"scope_id": ctx.scope_id})
    receipt = conn.execute("""INSERT INTO gov_action_receipts
        (receipt_id,scope_id,principal_id,idempotency_key,request_hash,action_type,auth_epoch,result,object_versions,target_object_id)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'[]'::jsonb,NULL) RETURNING *""",
        (receipt_id, ctx.scope_id, ctx.principal_id, body["idempotency_key"], request_hash,
         "workspace_v02." + stored["kind"], ctx.auth_epoch, Jsonb(result))).fetchone()
    from .readers import frozen_receipt
    return frozen_receipt(receipt)


def create_context(conn, ctx, body):
    request = body.model_dump(mode="json")
    request_hash = digest(request)
    existing = conn.execute("SELECT * FROM gov_workspace_v02_contexts WHERE scope_id=%s AND principal_id=%s AND idempotency_key=%s",
                            (ctx.scope_id, ctx.principal_id, request["idempotency_key"])).fetchone()
    if existing:
        if existing["request_hash"] != request_hash:
            fail("IDEMPOTENCY_CONFLICT")
        return context_view(conn, ctx, existing)
    history = rows(conn, ctx, request["scene_id"])
    seed, reader = scene_of(conn, ctx, request["scene_id"], history)
    states = collaboration.source_states(history)
    from datetime import datetime, timezone
    captured_at = datetime.now(timezone.utc).isoformat()
    selected = []
    for item in request["items"]:
        state = states.get(item["source_id"])
        if state is None:
            fail("INVALID_REQUEST", "The exact source version was not found in this scene.")
        version = collaboration.version_by_id(state, item["version_event_id"])
        if version is None or version["kind"] not in collaboration.SOURCE_VERSION_KINDS:
            fail("INVALID_REQUEST", "The exact source version was not found in this scene.")
        if version["payload_hash"] != item["payload_hash"]:
            fail("VERSION_CONFLICT", "The exact source version has changed.")
        if not collaboration.version_readable(state, item["version_event_id"], reader, item["payload_hash"]):
            fail("FORBIDDEN", "A requested source version is not currently readable.")
        payload = version["payload"]
        selected.append({"source_id": item["source_id"], "version_event_id": item["version_event_id"],
                         "payload_hash": item["payload_hash"], "version_seq": payload["version_seq"],
                         "fingerprint": payload["fingerprint"], "segments_hash": payload["segments_hash"],
                         "media_type": payload["media_type"], "acquired_at": payload["acquired_at"],
                         "origin_label": payload.get("origin_label"), "segments": payload["segments"],
                         "evidence_ref": payload.get("evidence_ref"), "captured_at": captured_at})
    context_id = str(uuid4())
    inserted = conn.execute("""INSERT INTO gov_workspace_v02_contexts
        (context_id,scope_id,scene_id,principal_id,idempotency_key,request_hash,purpose,selected,excluded)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'[]'::jsonb)
        ON CONFLICT (scope_id,principal_id,idempotency_key) DO NOTHING
        RETURNING context_id""",
        (context_id, ctx.scope_id, request["scene_id"], ctx.principal_id,
         request["idempotency_key"], request_hash, request["purpose"], Jsonb(selected))).fetchone()
    if inserted is None:
        existing = conn.execute("SELECT * FROM gov_workspace_v02_contexts WHERE scope_id=%s AND principal_id=%s AND idempotency_key=%s",
                                (ctx.scope_id, ctx.principal_id, request["idempotency_key"])).fetchone()
        if existing is None or existing["request_hash"] != request_hash:
            fail("IDEMPOTENCY_CONFLICT")
        return context_view(conn, ctx, existing)
    row = conn.execute("SELECT * FROM gov_workspace_v02_contexts WHERE scope_id=%s AND context_id=%s",
                       (ctx.scope_id, context_id)).fetchone()
    return context_view(conn, ctx, row)


def context_view(conn, ctx, row):
    from . import workspace_v02_readers as readers
    return readers.context_view(conn, ctx, row)


def authorize_receipt(conn, ctx, receipt, replay=False):
    result = receipt["result"] if isinstance(receipt["result"], dict) else json.loads(receipt["result"])
    scene_id = str(result["scene_id"])
    history = rows(conn, ctx, scene_id)
    seed, reader = scene_of(conn, ctx, scene_id, history)
    event = event_by_id(history, str(result["event_id"]))
    if replay and str(receipt["principal_id"]) != ctx.principal_id:
        fail("FORBIDDEN")
    if event["kind"] not in collaboration.FIELD_KINDS:
        return None
    kind = event["kind"]
    if kind == "source_add":
        # A source identity is its creation event.
        source_id = str(event["event_id"])
    elif kind == "source_unshare":
        share_event_id = str(event["payload"]["share_event_id"])
        source_id = next((source_id for source_id, state in collaboration.source_states(history).items()
                          if any(str(item["event"]["event_id"]) == share_event_id
                                 for item in state["shares"].values())), None)
    else:
        source_id = str(event["payload"]["source_id"])
    if source_id is None:
        fail("NOT_FOUND")
    state = _source_state(history, source_id)
    if collaboration.source_owner(state) == reader:
        return None
    if event["kind"] in collaboration.SOURCE_VERSION_KINDS:
        # Withdrawn versions are metadata-only for their owner; no other reader
        # keeps audit access to the content event.
        if collaboration.version_readable(state, str(event["event_id"]), reader):
            return None
        fail("NOT_FOUND")
    if event["kind"] == "source_share" and event["payload"].get("share_to_principal_id") == reader:
        return None
    if event["kind"] == "source_unshare":
        share = next((item for item in state["shares"].values()
                      if str(item["event"]["event_id"]) == str(event["payload"]["share_event_id"])), None)
        if share is not None and share["grantee"] == reader:
            return None
    if replay:
        return None
    fail("NOT_FOUND")
