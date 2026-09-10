"""Independently valid legacy adjustment bundle with each A dependency substituted."""
from __future__ import annotations

from copy import deepcopy
import uuid

from psycopg.types.json import Jsonb

from acceptance.runtime.client import Client
from .binding_cases import _owner
from .control_adapter import A_CONTRACT
from .support import digest, public_json


def run_adjustment_cases(h, f, *, legacy_flow, legacy_result, typed_fixture):
    flow = legacy_flow
    decision = flow.create("Decision", {"title": "Independent proposed adjustment basis",
        "statement": "A proposed terms change is not applied until its entire governed bundle is valid"})
    flow.act("ceo", "confirm_decision", {}, oid=decision)
    feedback = legacy_result["feedback"]
    adjustment = flow.create("ManagementAdjustment", {"title": "Independent closed adjustment candidate",
        "feedback_revision_id": flow.ref(feedback)["revision_id"], "decision_revision_id": flow.ref(decision)["revision_id"], "changes": []})
    bc, ec = legacy_result["bc"], legacy_result["ec"]
    old_bc, old_ec = flow.ref(bc), flow.ref(ec)
    bc_payload = deepcopy(flow.object(bc)["effective_revision"]["payload"])
    bc_payload["terms"]["target"] = 4
    revised_bc = flow.act("ceo", "propose_revision", {"payload": bc_payload, "bundle_id": adjustment}, oid=bc)
    new_bc = flow.object(bc)["latest_revision_id"]
    ec_payload = deepcopy(flow.object(ec)["effective_revision"]["payload"])
    ec_payload["terms"]["target"] = 4
    ec_payload["upstream_refs"] = [{"object_id": bc, "revision_id": new_bc}]
    flow.act("ceo", "propose_revision", {"payload": ec_payload, "bundle_id": adjustment}, oid=ec)
    new_ec = flow.object(ec)["latest_revision_id"]
    # The proposed BC/EC revisions need real signatures before prepare can
    # validate the complete change set. These are legacy signatures only.
    for oid, actors in ((bc, ("ceo", "domain_dri")), (ec, ("domain_dri", "mission_dri"))):
        obj = flow.object(oid)
        revision = flow.ceo.revision(oid, obj["latest_revision_id"])
        for actor in actors:
            flow.act(actor, "accept_commitment", {"party_assignment_id": f["actors"][actor]["assignment_id"],
                "understanding": "I understand this exact proposed legacy bundle revision; application is separate",
                "accepted_terms_hash": revision["payload_hash"]}, oid=oid)
    changes = [{"object_id": bc, "from_revision_id": old_bc["revision_id"], "to_revision_id": new_bc},
               {"object_id": ec, "from_revision_id": old_ec["revision_id"], "to_revision_id": new_ec}]
    params = {"feedback_revision_id": flow.ref(feedback)["revision_id"],
              "decision_revision_id": flow.ref(decision)["revision_id"], "changes": changes}
    flow.act("ceo", "propose_revision", {"payload": {"title": "Independent closed adjustment candidate", **params}}, oid=adjustment)
    before = h.snapshot(f)
    valid = flow.prepare("ceo", "confirm_adjustment", params, oid=adjustment)
    assert h.snapshot(f) == before, "valid adjustment prepare wrote business state"

    # Allocate a second raw metadata revision only for our A fixture. No
    # lifecycle event, signing, acceptance or effective revision is created.
    a_ref = typed_fixture["types"]["BusinessCommitment"]
    next_revision = str(uuid.uuid4())
    candidate = {**deepcopy(typed_fixture["payloads"]["BusinessCommitment"]), "title": "Synthetic A candidate revision two"}
    with _owner(h, f) as conn:
        conn.execute("""INSERT INTO gov_object_revisions
            (revision_id,scope_id,object_id,object_version,payload,payload_hash,recorded_by)
            VALUES(%s,%s,%s,2,%s,%s,%s)""", (next_revision, f["scope_id"], a_ref["object_id"],
                Jsonb(candidate), digest(candidate), f["actors"]["ceo"]["principal_id"]))
        conn.execute("UPDATE gov_objects SET object_version=2,latest_revision_id=%s WHERE scope_id=%s AND object_id=%s AND object_version=1",
                     (next_revision, f["scope_id"], a_ref["object_id"]))
    refs = typed_fixture["types"]
    alternatives = [
        ("decision_dependency", {**deepcopy(params), "decision_revision_id": refs["Decision"]["revision_id"]}),
        ("feedback_dependency", {**deepcopy(params), "feedback_revision_id": refs["FeedbackThread"]["revision_id"]}),
        ("changed_object_dependency", {**deepcopy(params), "changes": [*changes, {"object_id": a_ref["object_id"],
            "from_revision_id": a_ref["revision_id"], "to_revision_id": next_revision}]}),
    ]
    checked = []
    for label, substituted in alternatives:
        command = Client.command("confirm_adjustment", substituted, target=flow.target(adjustment))
        for path in ("/v1/actions/prepare", "/v1/actions"):
            checked.append({"part": label, "path": path, **h.rejection(f, flow.ceo, path, command, 409, "PROTOCOL_BINDING_CONFLICT")})
    command = Client.command("confirm_adjustment", params, target=refs["ManagementAdjustment"])
    command["contract_version"] = A_CONTRACT
    for path in ("/v1/actions/prepare", "/v1/actions"):
        checked.append({"part": "typed_a_adjustment_target", "path": path,
            **h.rejection(f, flow.ceo, path, command, 409, "ACTION_NOT_SUPPORTED_FOR_PROTOCOL")})
    assert flow.object(bc)["effective_revision_id"] == old_bc["revision_id"]
    assert flow.object(ec)["effective_revision_id"] == old_ec["revision_id"]
    assert flow.object(feedback)["lifecycle_status"] == "investigating"
    result = {"passed": True, "valid_legacy_prepare": valid, "checks": checked,
              "a_metadata_candidate_only": next_revision, "business_adjustment_applied": False,
              "contract_a1_accepted": False}
    public_json(h.output / "adjustment-affected-set-cases.json", result)
    return result
