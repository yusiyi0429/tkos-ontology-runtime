"""Independent assessment of the explicit synthetic customer activation metric."""
from __future__ import annotations

from uuid import uuid4

from psycopg.types.json import Jsonb

from . import a3_baseline, a3_customer_events, a3_governance, db, evidence
from .errors import GovernedError


def fail(code="INVALID_STATE"):
    raise GovernedError(code)


def target_baseline(ex):
    target = ex.target
    revision = ex.target_revision
    if (target["object_type"] != "CompanyReference"
            or target["effective_revision_id"] != revision["revision_id"]):
        fail("STALE_DEPENDENCY")
    candidates = db.jsonable(ex.conn.execute(
        """SELECT a.round_object_id,a.composition_object_id,a.composition_revision_id,a.manifest_hash
           FROM gov_activation_records a JOIN gov_object_revisions r
             ON r.scope_id=a.scope_id AND r.object_id=a.composition_object_id
               AND r.revision_id=a.composition_revision_id
           WHERE a.scope_id=%s AND r.payload->'company_reference_ref'->>'object_id'=%s
             AND r.payload->'company_reference_ref'->>'revision_id'=%s""",
        (ex.ctx.scope_id, target["object_id"], revision["revision_id"])).fetchall())
    if len(candidates) != 1:
        fail("INVALID_STATE")
    activation = candidates[0]
    ref = {"object_id": activation["composition_object_id"],
           "revision_id": activation["composition_revision_id"], "manifest_hash": activation["manifest_hash"]}
    baseline = a3_baseline.active_composition(ex, activation["round_object_id"], ref)
    a3_governance.check_upstream_currency(ex, baseline)
    definition = baseline["definition"]
    if (ex.ctx.principal_id != definition["ceo_principal_id"]
            or target["domain_id"] != definition["company_domain_id"]):
        fail("FORBIDDEN")
    assignment = ex.assignment(definition["ceo_assignment_id"])
    if assignment["principal_id"] != ex.ctx.principal_id or assignment["role"] != "CEO":
        fail("FORBIDDEN")
    ex.required_assignments.add(assignment["assignment_id"])
    for oid in (activation["round_object_id"], activation["composition_object_id"]):
        ex.add_dependency(oid)
    return baseline, assignment


def collect_outcome(ex):
    baseline, assignment = target_baseline(ex)
    packets = []
    for field in ("observation_revision_ids", "evidence_revision_ids"):
        for rid in ex.params[field]:
            obj, revision = ex.add_revision_dependency(rid)
            if obj["object_type"] != "EvidenceAsset":
                fail("INVALID_REQUEST")
            content = evidence.fetch_payload(revision["payload"], scope_id=ex.ctx.scope_id, domain_id=obj["domain_id"])
            if field == "observation_revision_ids":
                packets.append(content)
    for aid in ex.params["delivery_acceptance_ids"]:
        row = ex.conn.execute("SELECT * FROM gov_a3_delivery_acceptances WHERE scope_id=%s AND acceptance_id=%s",
                              (ex.ctx.scope_id, aid)).fetchone()
        if row is None:
            fail("NOT_FOUND")
        review = db.jsonable(row)
        if review["verification_result"] != "accepted":
            fail()
        work = ex.add_dependency(review["work_item_object_id"])
        work_revision = ex.revision(work["object_id"], review["work_item_revision_id"])
        ec_ref = work_revision["payload"]["execution_commitment_ref"]
        ec = ex.add_dependency(ec_ref["object_id"])
        ec_revision = ex.revision(ec["object_id"], ec_ref["revision_id"])
        dc_ref = ec_revision["payload"]["domain_commitment_ref"]
        dc = ex.add_dependency(dc_ref["object_id"])
        dc_revision = ex.revision(dc["object_id"], dc_ref["revision_id"])
        if dc_revision["payload"]["composition_ref"] != baseline["composition_ref"]:
            fail("STALE_DEPENDENCY")
        delivery = ex.add_dependency(review["deliverable_object_id"])
        rev = ex.revision(delivery["object_id"], review["deliverable_revision_id"])
        if rev["payload_hash"] != review["payload_hash"]:
            fail("STALE_DEPENDENCY")
        for rid in rev["payload"]["evidence_revision_ids"]:
            obj, source = ex.add_revision_dependency(rid)
            if obj["object_type"] != "EvidenceAsset":
                fail("INVALID_REQUEST")
            evidence.fetch_payload(source["payload"], scope_id=ex.ctx.scope_id, domain_id=obj["domain_id"])
    now = ex.conn.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
    definition = baseline["definition"]
    result = a3_customer_events.evaluate_packets(
        packets=packets, target_ref={k: ex.target_revision[k] for k in ("object_id", "revision_id", "payload_hash")},
        period_id=definition["period_id"], period_window=definition["period_window"],
        outcome_spec=ex.target_revision["payload"].get("terms", {}).get("outcome_spec", {}), assessed_at=now)
    requested = ex.params["assessment_result"]
    achieved = result["qualified_customer_count"] >= result["target_count"]
    if (requested == "achieved" and not achieved) or (requested == "not_achieved" and achieved):
        fail("INVALID_REQUEST")
    context = {"outcome": True, "baseline": baseline, "assessment": result,
               "assessor": assignment, "assessed_at": now}
    ex.a3_context = context
    return context


def record_outcome_assessment(ex):
    c = collect_outcome(ex)
    aid = str(uuid4())
    baseline, result = c["baseline"], c["assessment"]
    ex.conn.execute(
        """INSERT INTO gov_a3_outcome_assessments
           (assessment_id,scope_id,company_reference_object_id,company_reference_revision_id,
            composition_object_id,composition_revision_id,period_id,assessment_result,
            qualified_customer_count,target_count,observation_revision_ids,evidence_revision_ids,
            delivery_acceptance_ids,assessment_note,assessor_assignment_id,assessor_principal_id,action_id,recorded_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (aid, ex.ctx.scope_id, ex.target["object_id"], ex.target_revision["revision_id"],
         baseline["composition_ref"]["object_id"], baseline["composition_ref"]["revision_id"],
         baseline["definition"]["period_id"], ex.params["assessment_result"], result["qualified_customer_count"],
         result["target_count"], Jsonb(ex.params["observation_revision_ids"]), Jsonb(ex.params["evidence_revision_ids"]),
         Jsonb(ex.params["delivery_acceptance_ids"]), ex.params["assessment_note"], c["assessor"]["assignment_id"],
         ex.ctx.principal_id, ex.action_id, c["assessed_at"]))
    # Advance only mutable CAS/audit. The target's exact revision/hash and
    # lifecycle, delivery and MF states are deliberately unchanged.
    ex.bump(ex.target, detail={"outcome_assessment_id": aid})
    return {"object_id": ex.target["object_id"], "assessment_id": aid,
            "assessment_result": ex.params["assessment_result"],
            "qualified_customer_count": result["qualified_customer_count"], "target_count": result["target_count"]}


def recheck_final(ex):
    target_baseline(ex)
    for rid in [*ex.params["observation_revision_ids"], *ex.params["evidence_revision_ids"]]:
        ex.add_revision_dependency(rid)
