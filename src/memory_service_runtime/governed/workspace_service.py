"""Durable scene interactions under the existing current-authority transaction.

The scope authentication fence serializes CAS and idempotency. The append-only
event and ActionReceipt commit together; no Method state or external task writes.
"""
from dataclasses import replace
import hashlib
import json
from uuid import uuid4

from psycopg.types.json import Jsonb

from . import db, evidence, method_access as access, method_readers
from .errors import GovernedError

CONTRACT = "tkos.workspace/0.1"
PERSONAL = {"comment_anchor", "diff_response", "weekly_answer", "weekly_confirm",
            "read", "request_supplement", "bring_to_meeting", "refresh_sources"}
SCENE_KINDS = {
    "monthly": {"comment_anchor", "diff_response", "monthly_material"},
    "weekly": {"weekly_material", "weekly_answer", "weekly_confirm", "refresh_sources"},
    "meeting": {"meeting_start", "meeting_finish", "meeting_material", "meeting_publish"},
}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


def fail(code="INVALID_REQUEST", message=None):
    raise GovernedError(code, message) if message else GovernedError(code)


def exact(conn, ctx, ref, kinds=None):
    value = access.revision(conn, ctx, ref["object_id"], ref["revision_id"])
    if value["payload_hash"] != ref["payload_hash"]:
        fail("VERSION_CONFLICT")
    head = access.head(conn, ctx, ref["object_id"])
    if kinds and head["object_type"] not in kinds:
        fail("INVALID_REQUEST", "The referenced object has the wrong type.")
    return head, value


def check_refs(conn, ctx, value):
    for ref in method_readers.refs(value):
        exact(conn, ctx, ref)


def rows(conn, ctx, scene_id):
    return db.jsonable(conn.execute("SELECT * FROM gov_workspace_events WHERE scope_id=%s AND scene_id=%s ORDER BY version",
                                   (ctx.scope_id, scene_id)).fetchall())


def active(history):
    withdrawn = {e["payload"]["event_id"] for e in history if e["kind"] == "withdraw"}
    return [e for e in history if e["event_id"] not in withdrawn and e["kind"] != "withdraw"]


def latest(history, kind, principal=None):
    value = next((e for e in reversed(history) if e["kind"] == kind
                  and (principal is None or e["principal_id"] == principal)), None)
    withdrawn = {e["payload"]["event_id"] for e in history if e["kind"] == "withdraw"}
    return value if value and value["event_id"] not in withdrawn else None


def current_answers(history, material_id, principal_id):
    answers = {}
    for row in history:
        payload = row["payload"]
        if row["kind"] == "weekly_answer" and row["principal_id"] == principal_id and payload["material_event_id"] == material_id:
            answers[payload["question_id"]] = row
    withdrawn = {e["payload"]["event_id"] for e in history if e["kind"] == "withdraw"}
    return {key: row for key, row in answers.items() if row["event_id"] not in withdrawn}


def privileged(conn, ctx, domain):
    roles = {"CEO"} if ctx.principal_type == "human" else {"CO_AGENT"}
    return any(a["domain_id"] == domain and a["role"] in roles for a in db._assignments(conn, ctx))


def member_context(conn, ctx, assignment_id):
    assignment = access.assignment(conn, ctx, assignment_id)
    member = replace(ctx, principal_id=assignment["principal_id"], principal_type=assignment["principal_type"])
    return assignment, replace(member, assignments=db._assignments(conn, member))


def authorize_scene(conn, ctx, history):
    if not history:
        fail("NOT_FOUND")
    seed = history[0]["payload"]
    head, _ = exact(conn, ctx, seed["anchor_ref"])
    if privileged(conn, ctx, head["domain_id"]):
        db.authorize_domain(conn, ctx, head["domain_id"], "read")
        return seed, head
    for aid in [seed["owner_assignment_id"], *seed["participant_assignment_ids"]]:
        try:
            assignment = access.assignment(conn, ctx, aid, ctx.principal_id, ctx.principal_type)
            db.authorize_domain(conn, ctx, assignment["domain_id"], "read")
            return seed, head
        except GovernedError as exc:
            if exc.code != "FORBIDDEN":
                raise
    fail("NOT_FOUND")


def is_owner(conn, ctx, seed):
    try:
        access.assignment(conn, ctx, seed["owner_assignment_id"], ctx.principal_id, "human")
        return ctx.principal_type == "human"
    except GovernedError:
        return False


def authorize_write(conn, ctx, seed, head, kind):
    owner = is_owner(conn, ctx, seed)
    if kind == "create":
        if not privileged(conn, ctx, head["domain_id"]) and not (owner and seed["scene_type"] != "monthly"):
            fail("FORBIDDEN")
    elif kind in {"monthly_material", "weekly_material", "meeting_material"}:
        if not privileged(conn, ctx, head["domain_id"]) and not (owner and kind != "monthly_material"):
            fail("FORBIDDEN")
    elif kind in {"meeting_start", "meeting_finish", "meeting_publish"}:
        if not owner:
            fail("FORBIDDEN", "Only the current human meeting owner can perform this action.")
    elif kind in {"weekly_answer", "weekly_confirm"}:
        if not owner:
            fail("FORBIDDEN")
    elif ctx.principal_type != "human":
        fail("FORBIDDEN", "This record must be written by the person themselves.")


def validate_create(conn, ctx, seed):
    allowed = {"monthly": {"ReviewWindow"}, "weekly": {"Mission"},
               "meeting": {"StrategicIssue", "Mission", "ReviewWindow"}}
    head, revision = exact(conn, ctx, seed["anchor_ref"], allowed[seed["scene_type"]])
    if not access.is_method_object(conn, ctx, head["object_id"]):
        fail("PROTOCOL_NOT_SUPPORTED")
    ids = [seed["owner_assignment_id"], *seed["participant_assignment_ids"]]
    if len(ids) != len(set(ids)):
        fail("INVALID_REQUEST", "Scene responsibilities must be distinct.")
    for aid in ids:
        assignment, member = member_context(conn, ctx, aid)
        if assignment["principal_type"] != "human" or assignment["role"] not in {"CEO", "DOMAIN_DRI", "MISSION_DRI"}:
            fail("FORBIDDEN")
        exact(conn, member, seed["anchor_ref"])
        db.authorize_domain(conn, member, assignment["domain_id"], "read")
        if aid == seed["owner_assignment_id"] and seed["scene_type"] == "weekly":
            if assignment["principal_id"] != revision["payload"]["owner_principal_id"]:
                fail("FORBIDDEN", "Weekly materials belong to the Mission owner.")
    authorize_write(conn, ctx, seed, head, "create")
    duplicate = conn.execute("SELECT 1 FROM gov_workspace_events WHERE scope_id=%s AND kind='create' AND payload->>'scene_type'=%s AND payload->>'external_id'=%s",
                             (ctx.scope_id, seed["scene_type"], seed["external_id"])).fetchone()
    if duplicate:
        fail("VERSION_CONFLICT", "This external scene ID is already registered.")
    return head


def event_by_id(history, eid):
    event = next((e for e in history if e["event_id"] == eid), None)
    if event is None:
        fail("NOT_FOUND")
    return event


def material(conn, ctx, history, kind, event_id):
    value = latest(history, kind)
    if value is None:
        fail("DEPENDENCY_MISSING", "The required scene material has not been recorded.")
    if value["event_id"] != event_id:
        fail("VERSION_CONFLICT", "The material version has changed; reload before confirming.")
    check_refs(conn, ctx, value["payload"])
    return value["payload"]


def pointer(payload, path):
    value = payload
    try:
        for part in path[1:].split("/"):
            key = part.replace("~1", "/").replace("~0", "~")
            if isinstance(value, list):
                if not key.isdecimal() or (len(key) > 1 and key.startswith("0")):
                    fail()
                value = value[int(key)]
            else:
                value = value[key]
    except (KeyError, IndexError, TypeError, ValueError):
        fail("INVALID_REQUEST", "The field does not exist in the exact referenced payload.")
    return value


def validate_event(conn, ctx, seed, head, history, event):
    kind = event["kind"]
    if kind not in SCENE_KINDS[seed["scene_type"]] | {"read", "request_supplement", "bring_to_meeting", "withdraw"}:
        fail("INVALID_REQUEST", "This interaction is not part of this scene type.")
    authorize_write(conn, ctx, seed, head, kind)
    check_refs(conn, ctx, event)
    if kind == "comment_anchor":
        record = next((r for r in method_readers.review_records(conn, ctx, head["object_id"])["items"]
                       if r["record_id"] == event["review_record_id"]), None)
        if (not record or record["principal_id"] != ctx.principal_id
                or record["kind"] != "window_comment" or record["window_id"] != head["object_id"]):
            fail("FORBIDDEN")
        ref = event["target_ref"]
        if (record["target_object_id"], record["target_revision_id"]) != (ref["object_id"], ref["revision_id"]):
            fail()
        _, revision = exact(conn, ctx, ref)
        pointer(revision["payload"], event["field_path"])
    elif kind == "diff_response":
        exact(conn, ctx, event["candidate_ref"], {"CandidateSet"})
        state = method_readers.object_state(conn, ctx, head["object_id"])["method_state"]
        if state.get("candidate_ref") != event["candidate_ref"] or state.get("phase") not in {"resolved", "confirmed"}:
            fail("VERSION_CONFLICT")
        access.window_participant(conn, ctx, access.revision(conn, ctx, head["object_id"], seed["anchor_ref"]["revision_id"])["payload"])
    elif kind == "monthly_material":
        window = access.revision(conn, ctx, head["object_id"], seed["anchor_ref"]["revision_id"])["payload"]
        if event["target_ref"] not in window["target_refs"]:
            fail("INVALID_REQUEST", "Monthly material must describe a frozen window member.")
    elif kind == "weekly_material":
        _, mission = exact(conn, ctx, seed["anchor_ref"], {"Mission"})
        allowed_subjects = [seed["anchor_ref"], mission["payload"]["pco_ref"]]
        for ref in event["fact_refs"]:
            _, fact = exact(conn, ctx, ref, {"BusinessFact"})
            subject = fact["payload"]["subject_ref"]
            if not any(all(subject.get(k) == v for k, v in target.items()) for target in allowed_subjects):
                fail("INVALID_REQUEST", "Weekly facts must refer to this Mission or its exact PCO.")
        for ref in event["period_review_refs"]:
            _, review = exact(conn, ctx, ref, {"PeriodReview"})
            if seed["anchor_ref"] not in review["payload"]["target_refs"]:
                fail("INVALID_REQUEST", "Period review must include the exact Mission baseline.")
        for source in event["sources"]:
            if source["evidence_ref"]:
                source_head, source_revision = exact(conn, ctx, source["evidence_ref"], {"EvidenceAsset"})
                if source["state"] == "read":
                    evidence.fetch_payload(source_revision["payload"], scope_id=ctx.scope_id, domain_id=source_head["domain_id"])
    elif kind in {"weekly_answer", "weekly_confirm"}:
        pack = material(conn, ctx, history, "weekly_material", event["material_event_id"])
        questions = {q["question_id"] for q in pack["questions"]}
        if kind == "weekly_answer":
            if event["question_id"] not in questions:
                fail()
        else:
            answers = {key: row["event_id"] for key, row in current_answers(history, event["material_event_id"], ctx.principal_id).items()}
            if set(answers) != questions or set(event["answer_event_ids"]) != set(answers.values()) or len(event["answer_event_ids"]) != len(answers):
                fail("DEPENDENCY_MISSING", "Confirm the current personal answer for every material question.")
    elif kind == "meeting_start":
        if latest(history, "meeting_start"):
            fail("INVALID_STATE")
    elif kind == "meeting_finish":
        if not latest(history, "meeting_start") or latest(history, "meeting_finish"):
            fail("INVALID_STATE")
    elif kind == "meeting_material":
        if not latest(history, "meeting_finish"):
            fail("INVALID_STATE")
        source_head, revision = exact(conn, ctx, event["transcript_ref"], {"EvidenceAsset"})
        raw = evidence.fetch_payload(revision["payload"], scope_id=ctx.scope_id, domain_id=source_head["domain_id"])
        try:
            text = raw.decode("utf-8")
        except UnicodeError:
            fail("INVALID_REQUEST", "Transcript evidence must be UTF-8 text.")
        offset = 0
        for line in event["transcript"]:
            excerpt = line["speaker"] + ": " + line["text"]
            found = text.find(excerpt, offset)
            if found < 0:
                fail("INVALID_REQUEST", "Transcript lines must occur in order in the original evidence.")
            offset = found + len(excerpt)
    elif kind == "meeting_publish":
        pack = material(conn, ctx, history, "meeting_material", event["material_event_id"])
        for item in event["decisions"] + event["actions"] + event["open_questions"] + event["ceo_judgments"]:
            quote = item["quote"]
            if quote["line_index"] >= len(pack["transcript"]) or quote["text"] not in pack["transcript"][quote["line_index"]]["text"]:
                fail("INVALID_REQUEST", "Every published item must quote the exact transcript version.")
        required = {(k, i) for k, key in [("action", "actions"), ("ceo_judgment", "ceo_judgments")] for i in range(len(event[key]))}
        if {(r["item_kind"], r["item_index"]) for r in event["routes"]} != required or len(event["routes"]) != len(required):
            fail("INVALID_REQUEST", "Route each action and CEO judgment exactly once.")
        for route in event["routes"]:
            assignment, recipient = member_context(conn, ctx, route["recipient_assignment_id"])
            exact(conn, recipient, seed["anchor_ref"])
            authorize_scene(conn, recipient, history)
            if assignment["principal_type"] != "human":
                fail("FORBIDDEN")
            if route["item_kind"] == "ceo_judgment" and (assignment["role"] != "CEO" or assignment["domain_id"] != head["domain_id"]):
                fail("FORBIDDEN")
            if route["item_kind"] == "action":
                item = event["actions"][route["item_index"]]
                if route["recipient_assignment_id"] != (item["owner_assignment_id"] or seed["owner_assignment_id"]):
                    fail("INVALID_REQUEST", "Route actions to the named owner or the meeting owner fallback.")
    elif kind == "withdraw":
        row = event_by_id(active(history), event["event_id"])
        if row["principal_id"] != ctx.principal_id or row["kind"] not in PERSONAL | {"meeting_publish"}:
            fail("FORBIDDEN")


def authorize_event_read(conn, ctx, history, event):
    check_refs(conn, ctx, event["payload"])
    for key in ("material_event_id", "event_id"):
        if event["payload"].get(key):
            referenced = event_by_id(history, event["payload"][key])
            authorize_event_read(conn, ctx, history, referenced)


def authorize_receipt(conn, ctx, receipt, replay=False):
    history = rows(conn, ctx, str(receipt["result"]["scene_id"]))
    seed, head = authorize_scene(conn, ctx, history)
    event = event_by_id(history, str(receipt["result"]["event_id"]))
    authorize_event_read(conn, ctx, history, event)
    if replay:
        if str(receipt["principal_id"]) != ctx.principal_id:
            fail("FORBIDDEN")
        authorize_write(conn, ctx, seed, head, event["kind"])


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
    if event["kind"] == "create":
        head = validate_create(conn, ctx, event)
        seed = event
    else:
        seed, head = authorize_scene(conn, ctx, history)
        validate_event(conn, ctx, seed, head, history, event)
    if len(history) != body["expected_version"]:
        fail("VERSION_CONFLICT")
    receipt_id, event_id = str(uuid4()), str(uuid4())
    result = {"contract_version": CONTRACT, "scene_id": scene_id, "event_id": event_id,
              "version": len(history) + 1, "payload_hash": digest(event), "formal_effect": "none"}
    conn.execute("""INSERT INTO gov_workspace_events
        (scope_id,scene_id,event_id,version,anchor_object_id,anchor_revision_id,principal_id,action_id,kind,payload,payload_hash)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (ctx.scope_id, scene_id, event_id, result["version"], seed["anchor_ref"]["object_id"],
         seed["anchor_ref"]["revision_id"], ctx.principal_id, receipt_id, event["kind"], Jsonb(event), result["payload_hash"]))
    from .checkpoints import checkpoint
    checkpoint("workspace_before_receipt", {"scope_id": ctx.scope_id})
    receipt = conn.execute("""INSERT INTO gov_action_receipts
        (receipt_id,scope_id,principal_id,idempotency_key,request_hash,action_type,auth_epoch,result,object_versions,target_object_id)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'[]'::jsonb,%s) RETURNING *""",
        (receipt_id, ctx.scope_id, ctx.principal_id, body["idempotency_key"], request_hash,
         "workspace." + event["kind"], ctx.auth_epoch, Jsonb(result), head["object_id"])).fetchone()
    from .readers import frozen_receipt
    return frozen_receipt(receipt)
