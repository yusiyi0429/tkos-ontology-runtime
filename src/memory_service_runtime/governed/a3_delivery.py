"""IC execution and independent delivery review in the fenced Action transaction."""
from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from psycopg.types.json import Jsonb

from . import a2_rounds, a3_governance as gov, checkpoints, db, evidence
from .errors import GovernedError


def fail(code="INVALID_STATE", message="The execution state does not permit this action."):
    raise GovernedError(code, message)


def exact(revision):
    return {k: revision[k] for k in ("object_id", "revision_id", "payload_hash")}


def commitment(ex, ref):
    obj = ex.add_dependency(ref["object_id"])
    ex.same_domain(obj)
    rev = ex.revision(obj["object_id"], ref["revision_id"])
    if (obj["object_type"] != "ExecutionCommitment" or obj["lifecycle_status"] != "active"
            or obj["effective_revision_id"] != rev["revision_id"] or exact(rev) != ref):
        fail("STALE_DEPENDENCY")
    payload = rev["payload"]
    baseline = gov.check_execution_baseline(ex, payload)
    gov.check_upstream_currency(ex, baseline)
    return {"commitment": obj, "commitment_revision": rev, "payload": payload,
            "commitment_object_id": obj["object_id"], "baseline": baseline,
            "recheck_baseline": True}


def actor(ex, assignment_id, principal_id, role):
    assignment = ex.assignment(assignment_id)
    if (assignment["principal_id"] != principal_id or principal_id != ex.ctx.principal_id
            or assignment["role"] != role or assignment["domain_id"] != ex.domain_id
            or assignment["principal_type"] != "human"):
        fail("FORBIDDEN", "The caller is not the person assigned this responsibility.")
    ex.required_assignments.add(assignment_id)
    return assignment


def execution(ex, context, authority_id, epoch, *, require_ic=True):
    authority = gov.require_current_authority(ex, context["commitment_object_id"], authority_id, epoch)
    if authority["commitment_revision_id"] != context["commitment_revision"]["revision_id"]:
        fail("STALE_DEPENDENCY")
    if require_ic:
        actor(ex, authority["ic_assignment_id"], authority["ic_principal_id"], "IC")
    context["authority"] = authority
    return authority


def work(ex, oid=None, *, received=False):
    obj = ex.add_dependency(oid) if oid else ex.target
    if obj is None or obj["object_type"] != "WorkItem":
        fail()
    ex.same_domain(obj)
    raw = ex.conn.execute("SELECT * FROM gov_a3_work_item_state WHERE scope_id=%s AND object_id=%s",
                          (ex.ctx.scope_id, obj["object_id"])).fetchone()
    if raw is None:
        fail()
    state = db.jsonable(raw)
    rev = ex.revision(obj["object_id"], state["work_item_revision_id"])
    if (obj["latest_revision_id"] != rev["revision_id"]
            or (ex.target and obj["object_id"] == ex.target["object_id"]
                and ex.target_revision["revision_id"] != rev["revision_id"])):
        fail("STALE_DEPENDENCY")
    context = commitment(ex, rev["payload"]["execution_commitment_ref"])
    if (state["commitment_object_id"] != context["commitment_object_id"]
            or state["commitment_revision_id"] != context["commitment_revision"]["revision_id"]):
        fail("STALE_DEPENDENCY")
    if received and state["accepted_by"] is None:
        fail("INVALID_STATE", "The designated IC must receive the task before executing it.")
    context.update(work=obj, work_revision=rev, state=state)
    ex.a3_context = context
    return context


def work_execution(ex, context, params):
    state = context["state"]
    if (params["execution_authority_id"] != state["authority_id"]
            or params["execution_epoch"] != state["execution_epoch"]):
        fail("STALE_DEPENDENCY")
    return execution(ex, context, state["authority_id"], state["execution_epoch"])


def collect_create_work_item(ex):
    payload = ex.payload
    context = commitment(ex, payload["execution_commitment_ref"])
    authority = execution(ex, context, payload["execution_authority_id"], payload["execution_epoch"], require_ic=False)
    dri = gov.raw_assignment(ex, context["payload"]["dri_assignment_id"])
    actor(ex, dri["assignment_id"], dri["principal_id"], "DOMAIN_DRI")
    what = context["payload"]["what"]
    if (gov._criteria_key(payload["acceptance_criteria"]) != gov._criteria_key(what["acceptance_criteria"])
            or datetime.fromisoformat(payload["due_at"]) != datetime.fromisoformat(what["hard_deadline"])):
        fail("INVALID_REQUEST", "A task must preserve the confirmed criteria and deadline.")
    context["authority"] = authority
    ex.a3_context = context
    return context


def create_work_item(ex):
    c = collect_create_work_item(ex)
    obj, rev = a2_rounds.new_object(ex, "WorkItem", ex.domain_id, ex.payload, "offered", effective=True)
    authority = c["authority"]
    ex.conn.execute(
        """INSERT INTO gov_a3_work_item_state
           (object_id,scope_id,work_item_revision_id,commitment_object_id,commitment_revision_id,
            authority_id,execution_epoch,ic_assignment_id,ic_principal_id)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (obj["object_id"], ex.ctx.scope_id, rev["revision_id"], c["commitment_object_id"],
         c["commitment_revision"]["revision_id"], authority["authority_id"], authority["execution_epoch"],
         authority["ic_assignment_id"], authority["ic_principal_id"]))
    return {"object_id": obj["object_id"], "revision_id": rev["revision_id"]}


def collect_accept_work_item(ex):
    c = work(ex)
    work_execution(ex, c, ex.params)
    if c["work"]["lifecycle_status"] != "offered" or c["state"]["accepted_by"] is not None:
        fail()
    return c


def accept_work_item(ex):
    c = collect_accept_work_item(ex)
    s = c["state"]
    receipt_id = str(uuid4())
    ex.conn.execute(
        """INSERT INTO gov_work_receipts
           (work_receipt_id,scope_id,work_item_object_id,work_item_revision_id,
            commitment_object_id,commitment_revision_id,authority_id,execution_epoch,
            ic_assignment_id,ic_principal_id,action_id)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (receipt_id, ex.ctx.scope_id, s["object_id"], s["work_item_revision_id"],
         s["commitment_object_id"], s["commitment_revision_id"], s["authority_id"], s["execution_epoch"],
         s["ic_assignment_id"], s["ic_principal_id"], ex.action_id))
    ex.conn.execute(
        """UPDATE gov_a3_work_item_state SET accepted_by=%s,accepted_at=clock_timestamp(),
           updated_at=clock_timestamp() WHERE scope_id=%s AND object_id=%s""",
        (ex.ctx.principal_id, ex.ctx.scope_id, s["object_id"]))
    ex.bump(c["work"], status="accepted", detail={"work_receipt_id": receipt_id})
    return {"object_id": s["object_id"], "work_receipt_id": receipt_id,
            "work_item_revision_id": s["work_item_revision_id"],
            "execution_authority_id": s["authority_id"], "execution_epoch": s["execution_epoch"]}


def collect_plan(ex, revising=False):
    payload = ex.payload
    if ex.params.get("bundle_id"):
        fail("ACTION_NOT_SUPPORTED_FOR_PROTOCOL")
    c = work(ex, payload["work_item_ref"]["object_id"], received=True)
    s = c["state"]
    execution(ex, c, s["authority_id"], s["execution_epoch"])
    if (payload["work_item_ref"] != exact(c["work_revision"])
            or payload["execution_commitment_ref"] != exact(c["commitment_revision"])):
        fail("STALE_DEPENDENCY")
    if c["work"]["lifecycle_status"] not in {"accepted", "changes_requested"}:
        fail()
    if revising:
        if (s["plan_object_id"] != ex.target["object_id"]
                or s["current_plan_revision_id"] != ex.target_revision["revision_id"]):
            fail("STALE_DEPENDENCY")
    elif s["plan_object_id"] is not None:
        fail("INVALID_STATE", "The task already has a plan; revise its current plan.")
    deadline = datetime.fromisoformat(c["payload"]["what"]["hard_deadline"])
    if any(step.get("due_at") and datetime.fromisoformat(step["due_at"]) > deadline for step in payload["steps"]):
        fail("INVALID_REQUEST", "A plan step cannot extend the confirmed deadline.")
    return c


def collect_create_plan(ex):
    return collect_plan(ex)


def collect_propose_plan(ex):
    return collect_plan(ex, revising=True)


def publish_plan(ex, revising=False):
    c = collect_plan(ex, revising)
    if revising:
        rev = ex.insert_revision(ex.target, ex.payload, version=ex.target["object_version"] + 1)
        obj = ex.bump(ex.target, latest=rev["revision_id"], effective=rev["revision_id"])
    else:
        obj, rev = a2_rounds.new_object(ex, "ExecutionPlan", ex.domain_id, ex.payload, "active", effective=True)
    ex.conn.execute(
        """UPDATE gov_a3_work_item_state SET plan_object_id=%s,current_plan_revision_id=%s,
           updated_at=clock_timestamp() WHERE scope_id=%s AND object_id=%s""",
        (obj["object_id"], rev["revision_id"], ex.ctx.scope_id, c["work"]["object_id"]))
    ex.bump(c["work"], detail={"plan_ref": exact(rev)})
    return {"object_id": obj["object_id"], "revision_id": rev["revision_id"],
            "work_item_object_id": c["work"]["object_id"]}


def create_plan(ex):
    return publish_plan(ex)


def propose_plan(ex):
    return publish_plan(ex, revising=True)


def verified_evidence(ex, revision_ids):
    authors = set()
    for rid in revision_ids:
        obj, rev = ex.add_revision_dependency(rid)
        ex.same_domain(obj)
        if obj["object_type"] != "EvidenceAsset":
            fail("INVALID_REQUEST", "Delivery sources must be uploaded evidence revisions.")
        evidence.fetch_payload(rev["payload"], scope_id=ex.ctx.scope_id, domain_id=obj["domain_id"])
        authors.add(rev["recorded_by"])
    return authors


def collect_submit_deliverable(ex):
    c = work(ex, received=True)
    s = c["state"]
    work_execution(ex, c, ex.params)
    if c["work"]["lifecycle_status"] not in {"accepted", "changes_requested"}:
        fail()
    ref = ex.params["plan_ref"]
    if s["plan_object_id"] != ref["object_id"] or s["current_plan_revision_id"] != ref["revision_id"]:
        fail("STALE_DEPENDENCY", "Submission requires the current published plan.")
    plan = ex.add_dependency(ref["object_id"])
    rev = ex.revision(plan["object_id"], ref["revision_id"])
    if plan["effective_revision_id"] != ref["revision_id"] or exact(rev) != ref:
        fail("STALE_DEPENDENCY")
    if s["deliverable_object_id"]:
        ex.add_dependency(s["deliverable_object_id"])
    previous = ex.params.get("responds_to_acceptance_id")
    if s["submission_seq"]:
        if previous != s["latest_review_id"]:
            fail("STALE_DEPENDENCY", "Resubmission must answer this task's exact current return.")
        review = ex.conn.execute(
            """SELECT * FROM gov_a3_delivery_acceptances WHERE scope_id=%s AND acceptance_id=%s
               AND work_item_object_id=%s AND deliverable_revision_id=%s
               AND verification_result='changes_requested'""",
            (ex.ctx.scope_id, previous, s["object_id"], s["latest_submission_revision_id"])).fetchone()
        if review is None:
            fail("STALE_DEPENDENCY")
    elif previous is not None:
        fail("INVALID_STATE", "A first submission cannot respond to another return.")
    c["authors"] = sorted(verified_evidence(ex, ex.params["evidence_revision_ids"]) | {ex.ctx.principal_id})
    return c


def submit_deliverable(ex):
    c = collect_submit_deliverable(ex)
    s = c["state"]
    seq = s["submission_seq"] + 1
    payload = {k: ex.params[k] for k in ("title", "summary", "evidence_revision_ids", "plan_ref",
                                        "execution_authority_id", "execution_epoch")}
    payload.update(work_item_ref=exact(c["work_revision"]),
                   execution_commitment_ref=exact(c["commitment_revision"]), submission_seq=seq,
                   submitted_by=ex.ctx.principal_id, author_principal_ids=c["authors"],
                   responds_to_acceptance_id=ex.params.get("responds_to_acceptance_id"))
    if s["deliverable_object_id"]:
        obj = ex.head(s["deliverable_object_id"])
        rev = ex.insert_revision(obj, payload, version=obj["object_version"] + 1)
        checkpoints.checkpoint("after_a3_submission_write", {"action_type": ex.kind, "receipt_id": ex.action_id})
        obj = ex.bump(obj, status="submitted", latest=rev["revision_id"], effective=None)
    else:
        obj, rev = a2_rounds.new_object(ex, "Deliverable", ex.domain_id, payload, "submitted",
                                       round_id=c["commitment_object_id"])
        checkpoints.checkpoint("after_a3_submission_write", {"action_type": ex.kind, "receipt_id": ex.action_id})
    ex.conn.execute(
        """UPDATE gov_a3_work_item_state SET deliverable_object_id=%s,submission_seq=%s,
           latest_submission_revision_id=%s,latest_review_id=NULL,updated_at=clock_timestamp()
           WHERE scope_id=%s AND object_id=%s""",
        (obj["object_id"], seq, rev["revision_id"], ex.ctx.scope_id, s["object_id"]))
    ex.bump(c["work"], status="submitted", detail={"deliverable_revision_id": rev["revision_id"], "submission_seq": seq})
    return {"object_id": s["object_id"], "deliverable_object_id": obj["object_id"],
            "deliverable_revision_id": rev["revision_id"], "submission_seq": seq, "payload_hash": rev["payload_hash"]}


def collect_review_deliverable(ex):
    c = work(ex, received=True)
    s = c["state"]
    appointment = gov.require_current_appointment(
        ex, c["commitment_object_id"], ex.params["appointment_id"], ex.params["appointment_version"],
        expected_mission_object_id=c["payload"]["mission_ref"]["object_id"])
    if (appointment["commitment_revision_id"] != c["commitment_revision"]["revision_id"]
            or appointment["standards_revision_id"] != c["payload"]["mission_ref"]["revision_id"]):
        fail("STALE_DEPENDENCY")
    actor(ex, appointment["acceptor_assignment_id"], appointment["acceptor_principal_id"], "VERIFIER")
    if c["work"]["lifecycle_status"] != "submitted" or not s["deliverable_object_id"]:
        fail()
    obj = ex.add_dependency(s["deliverable_object_id"])
    if (ex.params["deliverable_revision_id"] != s["latest_submission_revision_id"]
            or obj["latest_revision_id"] != s["latest_submission_revision_id"]
            or obj["lifecycle_status"] != "submitted"):
        fail("STALE_DEPENDENCY")
    rev = ex.revision(obj["object_id"], s["latest_submission_revision_id"])
    if ex.params["delivery_payload_hash"] != rev["payload_hash"]:
        fail("STALE_DEPENDENCY")
    authors = verified_evidence(ex, rev["payload"]["evidence_revision_ids"]) | {rev["recorded_by"]}
    people = gov.party_principals(ex, c["payload"])
    if ex.ctx.principal_id in authors | {people["dri"], people["ic"]}:
        fail("FORBIDDEN", "An accountable party or trusted delivery author cannot independently review it.")
    results = ex.params["criterion_results"]
    if {r["criterion_id"] for r in results} != {r["criterion_id"] for r in c["work_revision"]["payload"]["acceptance_criteria"]}:
        fail("INVALID_REQUEST", "Review must cover exactly the agreed criteria.")
    if (ex.params["verification_result"] == "accepted") != all(r["result"] == "passed" for r in results):
        fail("INVALID_REQUEST", "Accepted requires every criterion passed; a return requires a failed criterion.")
    c.update(appointment=appointment, deliverable=obj, delivery_revision=rev)
    return c


def review_deliverable(ex):
    c = collect_review_deliverable(ex)
    s, obj, rev, appointment = c["state"], c["deliverable"], c["delivery_revision"], c["appointment"]
    aid = str(uuid4())
    ex.conn.execute(
        """INSERT INTO gov_a3_delivery_acceptances
           (acceptance_id,scope_id,work_item_object_id,work_item_revision_id,deliverable_object_id,
            deliverable_revision_id,submission_seq,payload_hash,verification_result,criterion_results,
            review_note,appointment_id,appointment_version,verifier_assignment_id,verifier_principal_id,action_id)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (aid, ex.ctx.scope_id, s["object_id"], s["work_item_revision_id"], obj["object_id"],
         rev["revision_id"], s["submission_seq"], rev["payload_hash"], ex.params["verification_result"],
         Jsonb(ex.params["criterion_results"]), ex.params["review_note"], appointment["appointment_id"],
         appointment["appointment_version"], appointment["acceptor_assignment_id"], ex.ctx.principal_id, ex.action_id))
    checkpoints.checkpoint("after_a3_review_write", {"action_type": ex.kind, "receipt_id": ex.action_id})
    accepted = ex.params["verification_result"] == "accepted"
    ex.bump(obj, status="accepted" if accepted else "changes_requested", effective=rev["revision_id"] if accepted else None)
    ex.conn.execute(
        """UPDATE gov_a3_work_item_state SET latest_review_id=%s,updated_at=clock_timestamp()
           WHERE scope_id=%s AND object_id=%s""", (aid, ex.ctx.scope_id, s["object_id"]))
    ex.bump(c["work"], status="delivery_accepted" if accepted else "changes_requested",
            detail={"delivery_acceptance_id": aid})
    return {"object_id": s["object_id"], "deliverable_object_id": obj["object_id"],
            "deliverable_revision_id": rev["revision_id"], "payload_hash": rev["payload_hash"],
            "submission_seq": s["submission_seq"], "delivery_acceptance_id": aid,
            "verification_result": ex.params["verification_result"]}
