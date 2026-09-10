"""Controlled malformed registrations: DB rejection or real HTTP fail-closed.

Owner creates new synthetic metadata only. No trigger is disabled and no old
object/profile/binding is modified. A DB rejection needs its exact expected
SQLSTATE (and, for cross-scope FK, constraint name); a parser/shape error cannot
count as a protocol denial. All successful malformed fixtures remain labelled
synthetic and are never interpreted as accepted business work.
"""
from __future__ import annotations

from copy import deepcopy
import uuid

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from acceptance.runtime.client import Client
from .control_adapter import A_CONTRACT, A_PROTOCOL, LEGACY_CONTRACT, LEGACY_PROTOCOL
from .support import Harness, digest, public_json
from .profile_cases import rehash


def _owner(h, f):
    if not conninfo_to_dict(h.env.values["MIGRATION_DATABASE_URL"])["dbname"].startswith("tkos_a1_"):
        raise ValueError("binding negative fixtures require the one-off A1 database")
    conn = psycopg.connect(h.env.values["MIGRATION_DATABASE_URL"], row_factory=dict_row)
    for name, value in (("app.governed_scope_id", f["scope_id"]), ("app.gov_control_plane", "on"),
                        ("app.runtime_write_capability", "tkos-runtime-a1")):
        conn.execute("SELECT set_config(%s,%s,true)", (name, value))
    return conn


def _insert_binding(conn, f, ref, fields):
    conn.execute("""INSERT INTO gov_object_protocol_bindings
        (scope_id,object_id,binding_version,protocol_id,contract_version,profile_id,profile_revision,
         profile_canonical_hash,record_origin,registered_by,detail)
        VALUES(%s,%s,1,%s,%s,%s,%s,%s,'synthetic','independent-negative-fixture',%s)""",
        (fields.get("scope_id", f["scope_id"]), ref["object_id"], fields["protocol_id"], fields["contract_version"],
         fields["profile_id"], fields["profile_revision"], fields["profile_canonical_hash"],
         Jsonb({"synthetic": True, "business_success": False})))


def _fixture_attempt(h, f, label, fields, *, registry=None):
    ref = {"object_id": str(uuid.uuid4()), "revision_id": str(uuid.uuid4()), "expected_version": 1}
    payload = {"title": "Independent malformed protocol fixture: " + label, "terms": {}, "upstream_refs": []}
    before = h.snapshot(f)
    try:
        with _owner(h, f) as conn:
            if registry:
                conn.execute("""INSERT INTO gov_protocol_support_registry
                    (scope_id,protocol_id,contract_version,registry_seq,content,recorded_by)
                    VALUES(%s,%s,%s,%s,%s,'independent-negative-fixture')""",
                    (f["scope_id"], registry["protocol_id"], registry["contract_version"], registry["registry_seq"], Jsonb(registry["content"])))
            conn.execute("INSERT INTO gov_objects(object_id,scope_id,domain_id,object_type,lifecycle_status) VALUES(%s,%s,%s,'CompanyOutcome','draft')",
                         (ref["object_id"], f["scope_id"], f["domain_id"]))
            conn.execute("""INSERT INTO gov_object_revisions
                (revision_id,scope_id,object_id,object_version,payload,payload_hash,recorded_by)
                VALUES(%s,%s,%s,1,%s,%s,%s)""", (ref["revision_id"], f["scope_id"], ref["object_id"],
                    Jsonb(payload), digest(payload), f["actors"]["ceo"]["principal_id"]))
            conn.execute("UPDATE gov_objects SET latest_revision_id=%s WHERE scope_id=%s AND object_id=%s",
                         (ref["revision_id"], f["scope_id"], ref["object_id"]))
            if fields is not None:
                _insert_binding(conn, f, ref, fields)
            if fields is not None:
                conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
            # The missing-binding case deliberately reaches real transaction
            # commit so it proves the deferred commit fence itself.
        return {"mode": "committed_synthetic_metadata", "ref": ref}
    except psycopg.Error as exc:
        assert h.snapshot(f) == before, "rejected synthetic fixture changed durable state"
        return {"mode": "database_rejected", "sqlstate": exc.sqlstate, "constraint": exc.diag.constraint_name,
                "snapshot_sha256": digest(before)}


def _nullable_profile_binding_probes(h, f, a_fields):
    """SQL three-valued logic must not admit a NULL implied-protocol match.

    These are raw owner control-plane fixtures, not profile-CLI successes. Each
    attempt is force-rolled back even if the binding incorrectly succeeds. No
    constraint is disabled and no existing Profile/object/binding is rewritten.
    """
    rows = h.sql(f, "SELECT content FROM gov_method_profile_revisions WHERE scope_id=%s AND profile_id=%s AND revision=%s",
                (f["scope_id"], a_fields["profile_id"], a_fields["profile_revision"]))
    assert len(rows) == 1
    probes = []
    for key in ("contract_id", "revision", "content_sha256"):
        for form in ("missing", "explicit_null"):
            core = deepcopy(rows[0]["content"])
            core["revision"] = "raw-null-binding-" + uuid.uuid4().hex
            if form == "missing":
                core["action_contract_ref"].pop(key)
            else:
                core["action_contract_ref"][key] = None
            rehash(core)
            fields = {**a_fields, "profile_revision": core["revision"], "profile_canonical_hash": core["canonical_hash"]}
            ref = {"object_id": str(uuid.uuid4()), "revision_id": str(uuid.uuid4())}
            payload = {"title": "Synthetic nullable-contract binding invariant", "terms": {}, "upstream_refs": []}
            before = h.snapshot(f)
            phase, sqlstate = "raw_profile", None
            try:
                with _owner(h, f) as conn:
                    with conn.transaction(force_rollback=True):
                        conn.execute("""INSERT INTO gov_method_profile_revisions
                            (scope_id,profile_id,revision,schema_version,canonical_hash,action_contract_ref,
                             record_origin,experimental,content,installed_by,install_reason)
                            VALUES(%s,%s,%s,'tkos.profile-core/0.1',%s,%s,'synthetic',true,%s,
                                   'independent-raw-db-invariant','temporary rollback-only malformed Profile')""",
                            (f["scope_id"], core["profile_id"], core["revision"], core["canonical_hash"],
                             Jsonb(core["action_contract_ref"]), Jsonb(core)))
                        phase = "synthetic_object"
                        conn.execute("INSERT INTO gov_objects(object_id,scope_id,domain_id,object_type,lifecycle_status) VALUES(%s,%s,%s,'CompanyOutcome','draft')",
                                     (ref["object_id"], f["scope_id"], f["domain_id"]))
                        conn.execute("""INSERT INTO gov_object_revisions
                            (revision_id,scope_id,object_id,object_version,payload,payload_hash,recorded_by)
                            VALUES(%s,%s,%s,1,%s,%s,%s)""",
                            (ref["revision_id"], f["scope_id"], ref["object_id"], Jsonb(payload), digest(payload), f["actors"]["ceo"]["principal_id"]))
                        conn.execute("UPDATE gov_objects SET latest_revision_id=%s WHERE scope_id=%s AND object_id=%s",
                                     (ref["revision_id"], f["scope_id"], ref["object_id"]))
                        phase = "binding_insert"
                        _insert_binding(conn, f, ref, fields)
                        conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
            except psycopg.Error as exc:
                sqlstate = exc.sqlstate
            assert h.snapshot(f) == before, "nullable Profile binding probe left durable rows"
            assert phase == "binding_insert" and sqlstate == "23514", \
                "missing/null contract reference was not rejected by the binding invariant with SQLSTATE 23514"
            probes.append({"field": key, "form": form, "phase": phase, "sqlstate": sqlstate,
                           "forced_rollback": True, "snapshot_sha256": digest(before), "business_success": False})
    return probes


def run_binding_cases(h: Harness, f: dict, *, url: str, typed_fixture: dict, legacy_flow,
                      legacy_result: dict, foreign_fixture: dict | None = None) -> dict:
    legacy = h.sql(f, "SELECT * FROM gov_object_protocol_bindings WHERE scope_id=%s AND object_id=%s",
                   (f["scope_id"], f["outcome"]["object_id"]))[0]
    a = h.sql(f, "SELECT * FROM gov_object_protocol_bindings WHERE scope_id=%s AND object_id=%s",
              (f["scope_id"], typed_fixture["types"]["CompanyOutcome"]["object_id"]))[0]
    keys = ("protocol_id", "contract_version", "profile_id", "profile_revision", "profile_canonical_hash")
    legacy_fields, a_fields = ({key: row[key] for key in keys} for row in (legacy, a))
    registry_rows = h.sql(f, "SELECT * FROM gov_protocol_support_registry WHERE scope_id=%s AND protocol_id=%s ORDER BY registry_seq DESC LIMIT 1",
                          (f["scope_id"], LEGACY_PROTOCOL))
    assert len(registry_rows) == 1 and registry_rows[0]["content"]["can_write"] is True
    clients = h.clients(url, f)
    output = {"checks": {}, "probes": [], "contract_a1_accepted": False}
    try:
        unknown = "tkos.independent-unknown-" + uuid.uuid4().hex
        probes = [
            ("missing_binding_denied", "missing", None, None, "PROTOCOL_BINDING_MISSING", ["23514"]),
            ("unsupported_profile_denied", "missing_profile", {**legacy_fields, "profile_revision": "not-installed"},
             None, "METHOD_PROFILE_UNSUPPORTED", ["23514", "23503"]),
            ("unknown_binding_protocol_denied", "unknown_protocol_with_writable_registry",
             {**legacy_fields, "protocol_id": unknown, "contract_version": unknown + "/1"},
             {"protocol_id": unknown, "contract_version": unknown + "/1", "registry_seq": 1,
              "content": deepcopy(registry_rows[0]["content"])}, "PROTOCOL_NOT_SUPPORTED", ["23514"]),
            (None, "a_profile_claims_legacy", {**a_fields, "protocol_id": LEGACY_PROTOCOL, "contract_version": LEGACY_CONTRACT},
             None, "METHOD_PROFILE_UNSUPPORTED", ["23514"]),
            (None, "legacy_profile_claims_a", {**legacy_fields, "protocol_id": A_PROTOCOL, "contract_version": A_CONTRACT},
             None, "METHOD_PROFILE_UNSUPPORTED", ["23514"]),
        ]
        for check, label, fields, registry, error_code, sqlstates in probes:
            attempt = _fixture_attempt(h, f, label, fields, registry=registry)
            if label == "missing":
                assert attempt["mode"] == "database_rejected", "a new object without a binding committed"
            if attempt["mode"] == "database_rejected":
                assert attempt["sqlstate"] in sqlstates, "fixture failed for an unrelated DB reason"
            else:
                command = Client.command("confirm_outcome", {}, target=attempt["ref"])
                if fields and fields["contract_version"] == A_CONTRACT:
                    command["contract_version"] = A_CONTRACT
                attempt["http"] = [h.rejection(f, clients["ceo"], path, command, 409, error_code)
                    for path in ("/v1/actions/prepare", "/v1/actions")]
            output["probes"].append({"id": label, **attempt})
            if check:
                output["checks"][check] = {"passed": True, "evidence": attempt}

        nullable = _nullable_profile_binding_probes(h, f, a_fields)
        output["probes"].append({"id": "raw_nullable_action_contract_ref_rejected", "attempts": nullable})
        output["checks"]["unsupported_profile_denied"]["nullable_reference_db_invariant"] = nullable

        # A writable registry cannot turn an unknown legacy version into an
        # implemented handler. Restore by appending the prior registry content,
        # never by overwriting its audit history.
        old_registry = registry_rows[0]
        version = "tkos.governed/999-independent"
        changed_registry = {"protocol_id": LEGACY_PROTOCOL, "contract_version": version,
                            "registry_seq": old_registry["registry_seq"] + 1, "content": deepcopy(old_registry["content"])}
        attempted = None
        try:
            attempted = _fixture_attempt(h, f, "unknown_legacy_contract_version", {**legacy_fields, "contract_version": version},
                                         registry=changed_registry)
            if attempted["mode"] == "database_rejected":
                assert attempted["sqlstate"] == "23514"
            else:
                command = Client.command("confirm_outcome", {}, target=attempted["ref"])
                command["contract_version"] = version
                attempted["http"] = [h.rejection(f, clients["ceo"], path, command, 409, "PROTOCOL_NOT_SUPPORTED")
                    for path in ("/v1/actions/prepare", "/v1/actions")]
            output["probes"].append({"id": "unknown_legacy_contract_version", **attempted})
        finally:
            if attempted and attempted["mode"] == "committed_synthetic_metadata":
                with _owner(h, f) as conn:
                    conn.execute("""INSERT INTO gov_protocol_support_registry
                        (scope_id,protocol_id,contract_version,registry_seq,content,recorded_by)
                        VALUES(%s,%s,%s,%s,%s,'independent-restore-prior-registry')""",
                        (f["scope_id"], LEGACY_PROTOCOL, old_registry["contract_version"],
                         old_registry["registry_seq"] + 2, Jsonb(old_registry["content"])))

        duplicate = {"object_id": typed_fixture["types"]["CompanyOutcome"]["object_id"]}
        before = h.snapshot(f)
        try:
            with _owner(h, f) as conn:
                _insert_binding(conn, f, duplicate, a_fields)
                conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
        except psycopg.Error as exc:
            assert exc.sqlstate == "55000", "duplicate binding failed for an unrelated reason"
        else:
            raise AssertionError("a second initial binding was accepted")
        assert h.snapshot(f) == before
        output["checks"]["duplicate_binding_cannot_fork"] = {"passed": True, "sqlstate": "55000"}

        if foreign_fixture is not None:
            other = h.sql(foreign_fixture, "SELECT * FROM gov_object_protocol_bindings WHERE scope_id=%s AND object_id=%s",
                          (foreign_fixture["scope_id"], foreign_fixture["outcome"]["object_id"]))[0]
            cross = {key: other[key] for key in keys}
            cross["scope_id"] = foreign_fixture["scope_id"]
            attempt = _fixture_attempt(h, f, "cross_scope_object_binding", cross)
            assert attempt["mode"] == "database_rejected" and attempt["sqlstate"] == "23503"
            assert attempt["constraint"] == "fk_gov_object_binding_object"
            output["checks"]["cross_scope_binding_fk_denied"] = {"passed": True, "evidence": attempt}

        refs = typed_fixture["types"]
        payloads = [
            ("BusinessCommitment", {"title": "Legacy BC with A Outcome", "terms": {"target": 3},
                "required_assignment_ids": [f["actors"][k]["assignment_id"] for k in ("ceo", "domain_dri")],
                "upstream_refs": [{k: refs["CompanyOutcome"][k] for k in ("object_id", "revision_id")}] }),
            ("ExecutionCommitment", {"title": "Legacy EC with A BC", "terms": {"target": 3},
                "required_assignment_ids": [f["actors"][k]["assignment_id"] for k in ("domain_dri", "mission_dri")],
                "upstream_refs": [{k: refs["BusinessCommitment"][k] for k in ("object_id", "revision_id")}] }),
            ("WorkItem", {**deepcopy(typed_fixture["payloads"]["WorkItem"]), "title": "Legacy WorkItem with A EC"}),
        ]
        parent_checks = []
        for kind, payload in payloads:
            request = Client.command("create_object", {"object_type": kind, "domain_id": f["domain_id"], "payload": payload})
            for path in ("/v1/actions/prepare", "/v1/actions"):
                parent_checks.append(h.rejection(f, clients["ceo"], path, request, 409, "PROTOCOL_BINDING_CONFLICT"))
        output["checks"]["parent_binding_conflict_denied"] = {"passed": True, "http": parent_checks}
        return output
    finally:
        public_json(h.output / "binding-negative-cases.json", output)
        for client in clients.values():
            client.close()
