"""Owner-created synthetic metadata fixtures, never business-success records.

Only newly allocated objects are inserted. No historical binding, receipt,
signature, acceptance or effective event is changed or manufactured.
"""
from __future__ import annotations

import uuid

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .control_adapter import A_CONTRACT, A_PROTOCOL
from .profile_cases import P1_HASH
from .support import Harness, digest, public_json


def create_typed_fixtures(h: Harness, f: dict, *, profile_id: str, profile_revision: str) -> dict:
    dbname = conninfo_to_dict(h.env.values["MIGRATION_DATABASE_URL"])["dbname"]
    if not dbname.startswith("tkos_a1_"):
        raise ValueError("typed synthetic fixtures require the one-off A1 database")
    kinds = ("CompanyOutcome", "BusinessCommitment", "ExecutionCommitment", "FeedbackThread",
             "WorkItem", "Decision", "ManagementAdjustment", "MetricObservation")
    ids = {kind: {"object_id": str(uuid.uuid4()), "revision_id": str(uuid.uuid4())} for kind in kinds}
    actors = f["actors"]
    run_id = str(uuid.uuid4())
    payloads = {
        "CompanyOutcome": {"title": "Synthetic A Outcome metadata", "terms": {"target": 3}, "upstream_refs": []},
        "BusinessCommitment": {"title": "Synthetic A BC metadata", "terms": {"target": 3},
            "required_assignment_ids": [actors[k]["assignment_id"] for k in ("ceo", "domain_dri")],
            "upstream_refs": [ids["CompanyOutcome"]]},
        "ExecutionCommitment": {"title": "Synthetic A EC metadata", "terms": {"target": 3},
            "required_assignment_ids": [actors[k]["assignment_id"] for k in ("domain_dri", "mission_dri")],
            "upstream_refs": [ids["BusinessCommitment"]]},
        "FeedbackThread": {"title": "Synthetic A feedback metadata", "description": "No feedback transition was executed"},
        "WorkItem": {"title": "Synthetic A WorkItem metadata with old-looking fields",
            "execution_commitment_ref": ids["ExecutionCommitment"],
            "dri_assignment_id": actors["mission_dri"]["assignment_id"],
            "acceptor_assignment_id": actors["verifier"]["assignment_id"],
            "acceptance_criteria": [{"criterion_id": "source", "description": "Metadata fixture only"}],
            "feedback_ref": ids["FeedbackThread"]},
        "Decision": {"title": "Synthetic A decision metadata", "statement": "This is not a confirmed decision"},
        "ManagementAdjustment": {"title": "Synthetic A adjustment metadata",
            "feedback_revision_id": ids["FeedbackThread"]["revision_id"],
            "decision_revision_id": ids["Decision"]["revision_id"], "changes": []},
        "MetricObservation": {"title": "Synthetic A metric metadata", "metric_id": "clients", "value": 2,
            "unit": "clients", "valid_from": "2026-09-10T00:00:00+00:00", "upstream_refs": []},
    }
    with psycopg.connect(h.env.values["MIGRATION_DATABASE_URL"], row_factory=dict_row) as conn:
        role = conn.execute("SELECT current_user::text AS current, session_user::text AS session").fetchone()
        assert role["current"] == role["session"] == conninfo_to_dict(h.env.values["MIGRATION_DATABASE_URL"])["user"]
        for name, value in (("app.governed_scope_id", f["scope_id"]), ("app.gov_control_plane", "on"),
                            ("app.runtime_write_capability", "tkos-runtime-a1")):
            conn.execute("SELECT set_config(%s,%s,true)", (name, value))
        profile = conn.execute("SELECT canonical_hash FROM gov_method_profile_revisions WHERE scope_id=%s AND profile_id=%s AND revision=%s",
                               (f["scope_id"], profile_id, profile_revision)).fetchone()
        assert profile and profile["canonical_hash"] == P1_HASH, "exact P1 must be installed before typed fixtures"
        for kind, ref in ids.items():
            status = "offered" if kind == "WorkItem" else "draft"
            conn.execute("INSERT INTO gov_objects(object_id,scope_id,domain_id,object_type,lifecycle_status) VALUES(%s,%s,%s,%s,%s)",
                         (ref["object_id"], f["scope_id"], f["domain_id"], kind, status))
            conn.execute("""INSERT INTO gov_object_revisions(revision_id,scope_id,object_id,object_version,payload,payload_hash,recorded_by)
                VALUES(%s,%s,%s,1,%s,%s,%s)""", (ref["revision_id"], f["scope_id"], ref["object_id"],
                    Jsonb(payloads[kind]), digest(payloads[kind]), actors["ceo"]["principal_id"]))
            conn.execute("UPDATE gov_objects SET latest_revision_id=%s WHERE scope_id=%s AND object_id=%s",
                         (ref["revision_id"], f["scope_id"], ref["object_id"]))
            conn.execute("""INSERT INTO gov_object_protocol_bindings
                (scope_id,object_id,binding_version,protocol_id,contract_version,profile_id,profile_revision,
                 profile_canonical_hash,record_origin,run_id,registered_by,receipt_id,detail)
                VALUES(%s,%s,1,%s,%s,%s,%s,%s,'synthetic',%s,'independent-owner-metadata-fixture',NULL,%s)""",
                (f["scope_id"], ref["object_id"], A_PROTOCOL, A_CONTRACT, profile_id, profile_revision,
                 P1_HASH, run_id, Jsonb({"kind": "synthetic_metadata_fixture", "business_success": False})))
        # Old-looking fields deliberately exist but no acceptance/submission
        # exists. An A1 read must not interpret this as legacy DRI ownership.
        work = ids["WorkItem"]
        conn.execute("""INSERT INTO gov_work_item_state
            (object_id,scope_id,work_item_revision_id,dri_assignment_id,acceptor_assignment_id)
            VALUES(%s,%s,%s,%s,%s)""", (work["object_id"], f["scope_id"], work["revision_id"],
                actors["mission_dri"]["assignment_id"], actors["verifier"]["assignment_id"]))
        conn.execute("INSERT INTO gov_protocol_control_events(scope_id,event_type,detail,actor) VALUES(%s,%s,%s,%s)",
            (f["scope_id"], "independent_synthetic_metadata_fixture", Jsonb({"run_id": run_id,
                "object_ids": [r["object_id"] for r in ids.values()], "business_success": False}),
             "independent-owner-metadata-fixture"))
        conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    result = {"run_id": run_id, "record_origin": "synthetic", "business_success": False,
              "types": {kind: {**ref, "expected_version": 1} for kind, ref in ids.items()},
              "payloads": payloads, "historical_objects_modified": False}
    public_json(h.output / "typed-metadata-fixtures.json", result)
    return result
