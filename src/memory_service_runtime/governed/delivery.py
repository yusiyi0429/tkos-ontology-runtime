"""DRI delivery and Outcome judgments inside the existing fenced Action transaction.

WorkItem baselines are immutable. Submission revisions, delivery reviews and
Outcome assessments are three distinct records; none closes an MF thread.
"""
from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from psycopg.types.json import Jsonb

from . import db
from .errors import GovernedError


ACTIONS = {"accept_work_item", "submit_deliverable", "review_deliverable", "record_outcome_assessment"}


def fail(code, message):
    raise GovernedError(code, message)


def payload_references(payload):
    refs = list(payload.get("upstream_refs", []))
    refs.extend(payload[key] for key in ("execution_commitment_ref", "feedback_ref", "work_item_ref")
                if payload.get(key))
    return list({(ref["object_id"], ref["revision_id"]): ref for ref in refs}.values())


def commitment_chain(ex, reference, *, current=False):
    chain = []
    for expected_type in ("ExecutionCommitment", "BusinessCommitment", "CompanyOutcome"):
        obj = ex.add_dependency(reference["object_id"])
        ex.same_domain(obj)
        revision = ex.revision(obj["object_id"], reference["revision_id"])
        if obj["object_type"] != expected_type:
            fail("INVALID_STATE", "Work requires an ExecutionCommitment to CompanyOutcome chain.")
        if current:
            ex.check_reference(reference)
            if obj["lifecycle_status"] != ("confirmed" if expected_type == "CompanyOutcome" else "active"):
                fail("STALE_DEPENDENCY", "Work requires currently effective confirmed commitments.")
        chain.append((obj, revision))
        if expected_type != "CompanyOutcome":
            refs = revision["payload"].get("upstream_refs", [])
            if len(refs) != 1:
                fail("INVALID_STATE", "The delivery pilot requires one direct upstream per commitment.")
            reference = refs[0]
    return chain


def work_state(ex):
    if ex.target is None or ex.target["object_type"] != "WorkItem":
        fail("INVALID_STATE", "Delivery actions require a WorkItem target.")
    row = ex.conn.execute("SELECT * FROM gov_work_item_state WHERE scope_id=%s AND object_id=%s",
                          (ex.ctx.scope_id, ex.target["object_id"])).fetchone()
    if row is None:
        fail("INVALID_STATE", "WorkItem processing state is absent.")
    state = db.jsonable(row)
    if state["work_item_revision_id"] != ex.target["latest_revision_id"]:
        fail("INVALID_STATE", "The frozen WorkItem baseline does not match its head.")
    return state


def work_dependencies(ex, payload):
    commitment_chain(ex, payload["execution_commitment_ref"])
    if payload.get("feedback_ref"):
        ref = payload["feedback_ref"]
        obj = ex.add_dependency(ref["object_id"])
        ex.same_domain(obj)
        ex.revision(obj["object_id"], ref["revision_id"])
        if obj["object_type"] != "FeedbackThread":
            fail("INVALID_STATE", "MF provenance must reference a FeedbackThread revision.")


def validate_work(ex, payload, *, creating=False):
    chain = commitment_chain(ex, payload["execution_commitment_ref"], current=True)
    ec, ec_revision = chain[0]
    parties = ex.commitment_parties(ec, ec_revision)
    dri = ex.assignment(payload["dri_assignment_id"])
    acceptor = ex.assignment(payload["acceptor_assignment_id"])
    if (dri["role"] != "MISSION_DRI" or dri["assignment_id"] not in {p["assignment_id"] for p in parties}
            or dri["principal_type"] != "human" or acceptor["principal_type"] != "human"
            or acceptor["role"] not in {"CEO", "DOMAIN_DRI", "VERIFIER"}
            or dri["principal_id"] == acceptor["principal_id"]):
        fail("FORBIDDEN", "Work requires its commitment DRI and a different authorized human acceptor.")
    ex.required_assignments.update([dri["assignment_id"], acceptor["assignment_id"]])
    ex.required_assignments.update(p["assignment_id"] for p in parties)
    if creating:
        if not any(a["role"] in {"CEO", "DOMAIN_DRI"} for a in ex.action_assignments):
            fail("FORBIDDEN", "Only a CEO or domain DRI can issue this work baseline.")
        if payload.get("feedback_ref"):
            ref = payload["feedback_ref"]
            feedback = ex.head(ref["object_id"])
            if feedback["latest_revision_id"] != ref["revision_id"] or feedback["lifecycle_status"] in {"closed", "dismissed"}:
                fail("STALE_DEPENDENCY", "New work must bind the current open MF revision.")
    return dri, acceptor, chain


def collect_dependencies(ex):
    if ex.kind in {"accept_work_item", "submit_deliverable", "review_deliverable"}:
        state = work_state(ex)
        work_dependencies(ex, ex.target_revision["payload"])
        if state["deliverable_object_id"]:
            ex.add_dependency(state["deliverable_object_id"])
        if ex.kind == "submit_deliverable":
            for rid in ex.params["evidence_revision_ids"]:
                obj, _ = ex.add_revision_dependency(rid)
                ex.same_domain(obj)
        if ex.kind == "review_deliverable":
            if ex.params["deliverable_revision_id"] != state["latest_submission_revision_id"]:
                fail("STALE_DEPENDENCY", "Review must bind the current submitted revision.")
            obj, rev = ex.add_revision_dependency(ex.params["deliverable_revision_id"])
            ex.same_domain(obj)
            for rid in rev["payload"].get("evidence_revision_ids", []):
                asset, _ = ex.add_revision_dependency(rid)
                ex.same_domain(asset)
    elif ex.kind == "record_outcome_assessment":
        for rid in [*ex.params["observation_revision_ids"], *ex.params["evidence_revision_ids"]]:
            obj, rev = ex.add_revision_dependency(rid)
            ex.same_domain(obj)
            for ref in payload_references(rev["payload"]):
                parent = ex.add_dependency(ref["object_id"])
                ex.same_domain(parent)
                ex.revision(parent["object_id"], ref["revision_id"])
        for aid in ex.params["delivery_acceptance_ids"]:
            delivery_acceptance(ex, aid)


def initialize_work(ex, obj, revision):
    ex.conn.execute(
        """INSERT INTO gov_work_item_state
           (object_id,scope_id,work_item_revision_id,dri_assignment_id,acceptor_assignment_id)
           VALUES (%s,%s,%s,%s,%s)""",
        (obj["object_id"], ex.ctx.scope_id, revision["revision_id"],
         revision["payload"]["dri_assignment_id"], revision["payload"]["acceptor_assignment_id"]),
    )


def require_actor(ex, assignment):
    if (assignment["principal_id"] != ex.ctx.principal_id or ex.ctx.principal_type != "human"
            or assignment["assignment_id"] not in {a["assignment_id"] for a in ex.action_assignments}):
        fail("FORBIDDEN", "Only the named human with current action authority can perform this transition.")


def accept_work_item(ex):
    state = work_state(ex)
    dri, _, _ = validate_work(ex, ex.target_revision["payload"])
    require_actor(ex, dri)
    if ex.target["lifecycle_status"] != "offered" or state["accepted_at"] is not None:
        fail("INVALID_STATE", "Work has already been accepted or is not offered.")
    ex.conn.execute(
        """UPDATE gov_work_item_state SET accepted_by=%s,accepted_at=clock_timestamp(),updated_at=clock_timestamp()
           WHERE scope_id=%s AND object_id=%s""", (ex.ctx.principal_id, ex.ctx.scope_id, ex.target["object_id"]),
    )
    obj = ex.bump(ex.target, status="in_progress", effective=ex.target_revision["revision_id"])
    return {"object_id": obj["object_id"], "revision_id": obj["latest_revision_id"], "delivery_status": "in_progress"}


def submit_deliverable(ex):
    state = work_state(ex)
    dri, _, _ = validate_work(ex, ex.target_revision["payload"])
    require_actor(ex, dri)
    if ex.target["lifecycle_status"] not in {"in_progress", "changes_requested"} or state["accepted_by"] != ex.ctx.principal_id:
        fail("INVALID_STATE", "The named DRI must accept work before submitting or resubmitting it.")
    response = ex.params.get("responds_to_acceptance_id")
    if ex.target["lifecycle_status"] == "changes_requested":
        prior = ex.conn.execute(
            "SELECT * FROM gov_delivery_acceptances WHERE scope_id=%s AND acceptance_id=%s",
            (ex.ctx.scope_id, state["latest_acceptance_id"]),
        ).fetchone()
        if (prior is None or response != str(prior["acceptance_id"])
                or prior["verification_result"] != "changes_requested"
                or str(prior["deliverable_revision_id"]) != state["latest_submission_revision_id"]):
            fail("STALE_DEPENDENCY", "Resubmission must answer the current returned submission review.")
    elif response is not None or state["submission_seq"] != 0:
        fail("INVALID_STATE", "An initial submission cannot answer an unrelated review.")
    ex.verify_evidence(ex.params["evidence_revision_ids"])
    seq = state["submission_seq"] + 1
    evidence_refs = [{"object_id": ex.revision_by_id(rid)[0]["object_id"], "revision_id": rid}
                     for rid in ex.params["evidence_revision_ids"]]
    work_ref = {"object_id": ex.target["object_id"], "revision_id": state["work_item_revision_id"]}
    payload = {"title": ex.params["title"], "summary": ex.params["summary"], "submission_seq": seq,
               "work_item_ref": work_ref, "execution_commitment_ref": ex.target_revision["payload"]["execution_commitment_ref"],
               "evidence_revision_ids": ex.params["evidence_revision_ids"], "upstream_refs": [work_ref, *evidence_refs],
               "responds_to_acceptance_id": response}
    if state["deliverable_object_id"]:
        obj = ex.head(state["deliverable_object_id"])
        if obj["object_type"] != "Deliverable" or obj["lifecycle_status"] != "changes_requested":
            fail("INVALID_STATE", "Only the returned delivery can be resubmitted.")
        rev = ex.insert_revision(obj, payload, version=obj["object_version"] + 1)
        obj = ex.bump(obj, status="submitted", latest=rev["revision_id"], effective=None,
                      detail={"submission_seq": seq, "responds_to_acceptance_id": response})
    else:
        obj = db.jsonable(ex.conn.execute(
            """INSERT INTO gov_objects(object_id,scope_id,domain_id,object_type,lifecycle_status)
               VALUES (%s,%s,%s,'Deliverable','submitted') RETURNING *""",
            (str(uuid4()), ex.ctx.scope_id, ex.domain_id),
        ).fetchone())
        rev = ex.insert_revision(obj, payload, version=1)
        obj = db.jsonable(ex.conn.execute(
            "UPDATE gov_objects SET latest_revision_id=%s WHERE scope_id=%s AND object_id=%s RETURNING *",
            (rev["revision_id"], ex.ctx.scope_id, obj["object_id"]),
        ).fetchone())
        ex.heads[obj["object_id"]] = ex.changed[obj["object_id"]] = obj
        ex.event(obj, None, "submit_deliverable", {"submission_seq": seq})
    ex.conn.execute(
        """UPDATE gov_work_item_state SET deliverable_object_id=%s,submission_seq=%s,
           latest_submission_revision_id=%s,latest_acceptance_id=NULL,updated_at=clock_timestamp()
           WHERE scope_id=%s AND object_id=%s""",
        (obj["object_id"], seq, rev["revision_id"], ex.ctx.scope_id, ex.target["object_id"]),
    )
    work = ex.bump(ex.target, status="submitted", detail={"deliverable_revision_id": rev["revision_id"], "submission_seq": seq})
    return {"object_id": work["object_id"], "deliverable_object_id": obj["object_id"],
            "deliverable_revision_id": rev["revision_id"], "submission_seq": seq, "payload_hash": rev["payload_hash"]}


def review_deliverable(ex):
    state = work_state(ex)
    dri, acceptor, _ = validate_work(ex, ex.target_revision["payload"])
    require_actor(ex, acceptor)
    if ex.target["lifecycle_status"] != "submitted" or not state["deliverable_object_id"]:
        fail("INVALID_STATE", "Only the latest submitted delivery can be reviewed.")
    obj = ex.head(state["deliverable_object_id"])
    if (ex.params["deliverable_revision_id"] != state["latest_submission_revision_id"]
            or obj["latest_revision_id"] != state["latest_submission_revision_id"] or obj["lifecycle_status"] != "submitted"):
        fail("STALE_DEPENDENCY", "Review must bind the current submitted revision.")
    rev = ex.revision(obj["object_id"], state["latest_submission_revision_id"])
    if rev["recorded_by"] == ex.ctx.principal_id or dri["principal_id"] == ex.ctx.principal_id:
        fail("FORBIDDEN", "A delivery author cannot accept their own delivery.")
    if ex.params["delivery_payload_hash"] != rev["payload_hash"]:
        fail("STALE_DEPENDENCY", "Review must bind the submitted payload hash.")
    results = ex.params["criterion_results"]
    criteria = {c["criterion_id"] for c in ex.target_revision["payload"]["acceptance_criteria"]}
    if {r["criterion_id"] for r in results} != criteria:
        fail("INVALID_REQUEST", "Review must cover exactly the frozen acceptance criteria.")
    accepted = ex.params["verification_result"] == "accepted"
    if accepted != all(r["result"] == "passed" for r in results):
        fail("INVALID_REQUEST", "Acceptance requires every criterion passed; a return identifies a failed criterion.")
    ex.verify_evidence(rev["payload"]["evidence_revision_ids"])
    aid = str(uuid4())
    ex.conn.execute(
        """INSERT INTO gov_delivery_acceptances
           (acceptance_id,scope_id,work_item_object_id,work_item_revision_id,deliverable_object_id,
            deliverable_revision_id,submission_seq,payload_hash,verification_result,criterion_results,
            review_note,verifier_assignment_id,verifier_principal_id,action_id)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (aid, ex.ctx.scope_id, ex.target["object_id"], state["work_item_revision_id"], obj["object_id"],
         rev["revision_id"], state["submission_seq"], rev["payload_hash"], ex.params["verification_result"],
         Jsonb(results), ex.params["review_note"], acceptor["assignment_id"], ex.ctx.principal_id, ex.action_id),
    )
    ex.bump(obj, status="accepted" if accepted else "changes_requested", effective=rev["revision_id"] if accepted else None,
            detail={"delivery_acceptance_id": aid, "submission_seq": state["submission_seq"]})
    ex.conn.execute(
        "UPDATE gov_work_item_state SET latest_acceptance_id=%s,updated_at=clock_timestamp() WHERE scope_id=%s AND object_id=%s",
        (aid, ex.ctx.scope_id, ex.target["object_id"]),
    )
    work = ex.bump(ex.target, status="delivery_accepted" if accepted else "changes_requested",
                   detail={"delivery_acceptance_id": aid, "verification_result": ex.params["verification_result"]})
    return {"object_id": work["object_id"], "deliverable_object_id": obj["object_id"],
            "deliverable_revision_id": rev["revision_id"], "submission_seq": state["submission_seq"],
            "payload_hash": rev["payload_hash"], "acceptance_id": aid, "verification_result": ex.params["verification_result"]}


def delivery_acceptance(ex, acceptance_id):
    row = ex.conn.execute("SELECT * FROM gov_delivery_acceptances WHERE scope_id=%s AND acceptance_id=%s",
                          (ex.ctx.scope_id, acceptance_id)).fetchone()
    if row is None:
        fail("NOT_FOUND", "Delivery review was not found.")
    row = db.jsonable(row)
    work = ex.add_dependency(row["work_item_object_id"])
    ex.same_domain(work)
    rev = ex.revision(work["object_id"], row["work_item_revision_id"])
    deliverable = ex.add_dependency(row["deliverable_object_id"])
    ex.same_domain(deliverable)
    ex.revision(deliverable["object_id"], row["deliverable_revision_id"])
    chain = commitment_chain(ex, rev["payload"]["execution_commitment_ref"])
    outcome, outcome_revision = chain[-1]
    if (row["verification_result"] != "accepted" or outcome["object_id"] != ex.target["object_id"]
            or outcome_revision["revision_id"] != ex.target_revision["revision_id"]):
        fail("INVALID_STATE", "Delivery evidence must be an accepted submission for this exact Outcome version.")
    return row


def record_outcome_assessment(ex):
    obj, rev = ex.target, ex.target_revision
    if (obj["object_type"] != "CompanyOutcome" or obj["lifecycle_status"] != "confirmed"
            or obj["effective_revision_id"] != rev["revision_id"]):
        fail("INVALID_STATE", "Assessment requires the currently effective confirmed Outcome revision.")
    assessor = next((a for a in ex.action_assignments if a["role"] == "CEO"), None)
    if assessor is None:
        fail("FORBIDDEN", "The current CEO decision right is required for CompanyOutcome assessment.")
    require_actor(ex, assessor)
    ex.required_assignments.add(assessor["assignment_id"])
    observed_objects = set()
    now = ex.conn.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
    for rid in ex.params["observation_revision_ids"]:
        observation, observation_rev = ex.revision_by_id(rid)
        ex.same_domain(observation)
        refs = observation_rev["payload"].get("upstream_refs", [])
        if (observation["object_type"] != "MetricObservation" or observation["effective_revision_id"] != rid
                or observation["object_id"] in observed_objects
                or not any(r["object_id"] == obj["object_id"] and r["revision_id"] == rev["revision_id"] for r in refs)):
            fail("INVALID_STATE", "Assessment requires distinct current observations of this exact Outcome.")
        valid_from = datetime.fromisoformat(observation_rev["valid_from"].replace("Z", "+00:00"))
        valid_to = observation_rev.get("valid_to")
        if valid_from > now or (valid_to and datetime.fromisoformat(valid_to.replace("Z", "+00:00")) <= now):
            fail("INVALID_STATE", "A future or expired observation cannot support a present Outcome judgment.")
        observed_objects.add(observation["object_id"])
    ex.verify_evidence(ex.params["evidence_revision_ids"])
    aid = str(uuid4())
    ex.conn.execute(
        """INSERT INTO gov_outcome_assessments
           (assessment_id,scope_id,outcome_object_id,outcome_revision_id,assessment_result,
            observation_revision_ids,evidence_revision_ids,delivery_acceptance_ids,assessment_note,
            assessor_assignment_id,assessor_principal_id,action_id)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (aid, ex.ctx.scope_id, obj["object_id"], rev["revision_id"], ex.params["assessment_result"],
         Jsonb(ex.params["observation_revision_ids"]), Jsonb(ex.params["evidence_revision_ids"]),
         Jsonb(ex.params["delivery_acceptance_ids"]), ex.params["assessment_note"],
         assessor["assignment_id"], ex.ctx.principal_id, ex.action_id),
    )
    ex.bump(obj, detail={"outcome_assessment_id": aid, "assessment_result": ex.params["assessment_result"]})
    return {"object_id": obj["object_id"], "revision_id": rev["revision_id"], "assessment_id": aid,
            "assessment_result": ex.params["assessment_result"]}


def run_action(ex):
    return {"accept_work_item": accept_work_item, "submit_deliverable": submit_deliverable,
            "review_deliverable": review_deliverable, "record_outcome_assessment": record_outcome_assessment}[ex.kind](ex)


def read_work_item(conn, ctx, obj):
    row = conn.execute("SELECT * FROM gov_work_item_state WHERE scope_id=%s AND object_id=%s",
                       (ctx.scope_id, obj["object_id"])).fetchone()
    state = db.jsonable(row)
    if state is None:
        fail("INVALID_STATE", "WorkItem processing state is absent.")
    baseline = db.revision_row(conn, ctx, obj["object_id"], state["work_item_revision_id"])
    for ref in payload_references(baseline["payload"]):
        db.revision_row(conn, ctx, ref["object_id"], ref["revision_id"])
    submissions = []
    if state["deliverable_object_id"]:
        db.object_row(conn, ctx, state["deliverable_object_id"])
        submissions = conn.execute(
            "SELECT * FROM gov_object_revisions WHERE scope_id=%s AND object_id=%s ORDER BY object_version",
            (ctx.scope_id, state["deliverable_object_id"]),
        ).fetchall()
        for revision in submissions:
            for ref in payload_references(revision["payload"]):
                db.revision_row(conn, ctx, ref["object_id"], ref["revision_id"])
    reviews = conn.execute(
        "SELECT * FROM gov_delivery_acceptances WHERE scope_id=%s AND work_item_object_id=%s ORDER BY submission_seq",
        (ctx.scope_id, obj["object_id"]),
    ).fetchall()
    return db.jsonable({"state": state, "submissions": submissions, "acceptances": reviews})


def read_outcome_assessment(conn, ctx, object_id, revision_id, *, known_at=None, valid_at=None):
    row = conn.execute(
        """SELECT * FROM gov_outcome_assessments WHERE scope_id=%s AND outcome_object_id=%s
           AND outcome_revision_id=%s AND recorded_at<=COALESCE(%s::timestamptz,clock_timestamp())
           AND recorded_at<=COALESCE(%s::timestamptz,clock_timestamp())
           ORDER BY recorded_at DESC,assessment_id DESC LIMIT 1""",
        (ctx.scope_id, object_id, revision_id, known_at, valid_at),
    ).fetchone()
    if row is None:
        return None
    for rid in [*row["observation_revision_ids"], *row["evidence_revision_ids"]]:
        linked = conn.execute("SELECT object_id FROM gov_object_revisions WHERE scope_id=%s AND revision_id=%s",
                              (ctx.scope_id, rid)).fetchone()
        if linked is None:
            fail("NOT_FOUND", "Assessment evidence is unavailable.")
        db.revision_row(conn, ctx, str(linked["object_id"]), rid)
    for aid in row["delivery_acceptance_ids"]:
        linked = conn.execute("SELECT work_item_object_id,deliverable_object_id FROM gov_delivery_acceptances WHERE scope_id=%s AND acceptance_id=%s",
                              (ctx.scope_id, aid)).fetchone()
        if linked is None:
            fail("NOT_FOUND", "Assessment delivery review is unavailable.")
        for key in ("work_item_object_id", "deliverable_object_id"):
            db.object_row(conn, ctx, str(linked[key]))
    return db.jsonable(row)


def read_delivery_review(conn, ctx, object_id, revision_id, *, known_at=None, valid_at=None):
    row = conn.execute(
        """SELECT * FROM gov_delivery_acceptances WHERE scope_id=%s AND deliverable_object_id=%s
           AND deliverable_revision_id=%s AND recorded_at<=COALESCE(%s::timestamptz,clock_timestamp())
           AND recorded_at<=COALESCE(%s::timestamptz,clock_timestamp())""",
        (ctx.scope_id, object_id, revision_id, known_at, valid_at),
    ).fetchone()
    if row is not None:
        db.revision_row(conn, ctx, str(row["work_item_object_id"]), str(row["work_item_revision_id"]))
    return db.jsonable(row)
