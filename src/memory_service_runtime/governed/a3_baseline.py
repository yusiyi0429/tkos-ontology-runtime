"""Actor-neutral checks of an already activated A2 composition.

These scoped reads validate authority, never grant visibility or add hidden CAS
objects to an IC's receipt. The outer Action transaction owns the scope fence.
"""
from __future__ import annotations

from pydantic import ValidationError

from . import a2_models, a2_rounds, db, protocol
from .errors import GovernedError


def fail():
    raise GovernedError("STALE_DEPENDENCY", "The adopted company baseline is no longer current.")


def row(ex, table, key, value):
    # Table/column names are compile-time call sites, not request input.
    result = ex.conn.execute(f"SELECT * FROM {table} WHERE scope_id=%s AND {key}=%s",
                             (ex.ctx.scope_id, value)).fetchone()
    if result is None:
        fail()
    return db.jsonable(result)


def revision(ex, oid, rid):
    result = row(ex, "gov_object_revisions", "revision_id", rid)
    if result["object_id"] != oid:
        fail()
    return result


def active_composition(ex, round_id, composition_ref):
    state = row(ex, "gov_formation_round_state", "object_id", round_id)
    head = row(ex, "gov_objects", "object_id", round_id)
    comp = row(ex, "gov_objects", "object_id", composition_ref["object_id"])
    activation = row(ex, "gov_activation_records", "round_object_id", round_id)
    rid = composition_ref["revision_id"]
    if (head["object_type"] != "FormationRound" or head["lifecycle_status"] != "active"
            or comp["object_type"] != "CompanyComposition" or comp["lifecycle_status"] != "active"
            or comp["effective_revision_id"] != rid
            or state["activated_composition_object_id"] != comp["object_id"]
            or state["activated_composition_revision_id"] != rid
            or activation["composition_object_id"] != comp["object_id"]
            or activation["composition_revision_id"] != rid):
        fail()
    definition = revision(ex, round_id, head["latest_revision_id"])["payload"]
    manifest = revision(ex, comp["object_id"], rid)["payload"]
    try:
        a2_models.validate_manifest(a2_models.CompositionManifest.model_validate(manifest))
    except (ValueError, ValidationError):
        fail()
    if (manifest["round_id"] != round_id or manifest["scope_id"] != ex.ctx.scope_id
            or manifest["company_id"] != ex.ctx.company_id
            or manifest["period_id"] != definition["period_id"]
            or manifest["manifest_hash"] != composition_ref["manifest_hash"]
            or activation["manifest_hash"] != manifest["manifest_hash"]
            or manifest["company_reference_ref"] != definition["company_reference_ref"]
            or manifest["method_profile_ref"] != definition["method_profile_ref"]):
        fail()
    for key in ("member_set_version", "input_set_version"):
        if state[key] != manifest[key] or activation[key] != manifest[key]:
            fail()
    for oid in (round_id, comp["object_id"]):
        binding = protocol.current_binding(ex.conn, ex.ctx.scope_id, oid)
        profile = manifest["method_profile_ref"]
        if (binding is None or binding["protocol_id"] != "tkos.contract-a"
                or (binding["profile_id"], binding["profile_revision"], binding["profile_canonical_hash"])
                != (profile["profile_id"], profile["revision"], profile["canonical_hash"])):
            fail()
    members = sorted(definition["members"], key=lambda item: item["domain_id"])
    if [{k: m[k] for k in ("domain_id", "dri_assignment_id", "dri_principal_id")}
            for m in manifest["members"]] != members:
        fail()
    slots = [{"assignment_id": definition["ceo_assignment_id"],
              "principal_id": definition["ceo_principal_id"], "responsibility_role": "company_decider"}]
    slots += [{"assignment_id": m["dri_assignment_id"], "principal_id": m["dri_principal_id"],
               "responsibility_role": "area_accountable"} for m in members]
    if sorted(slots, key=lambda item: item["principal_id"]) != manifest["required_signers"]:
        fail()
    for slot in slots:
        company = slot["responsibility_role"] == "company_decider"
        domain = definition["company_domain_id"] if company else next(
            m["domain_id"] for m in members if m["dri_assignment_id"] == slot["assignment_id"])
        current = ex.conn.execute(
            """SELECT 1 FROM gov_role_assignments a JOIN gov_principals p
                 ON p.scope_id=a.scope_id AND p.principal_id=a.principal_id
               JOIN gov_domains d ON d.scope_id=a.scope_id AND d.domain_id=a.domain_id
               WHERE a.scope_id=%s AND a.assignment_id=%s AND a.principal_id=%s
                 AND a.domain_id=%s AND a.role=%s AND a.active AND p.active
                 AND p.principal_type='human' AND a.valid_from<=clock_timestamp()
                 AND (a.valid_to IS NULL OR clock_timestamp()<a.valid_to)""",
            (ex.ctx.scope_id, slot["assignment_id"], slot["principal_id"], domain,
             "CEO" if company else "DOMAIN_DRI")).fetchone()
        if current is None:
            fail()
    signed = ex.conn.execute(
        """SELECT principal_id,assignment_id,responsibility_role,manifest_hash
           FROM gov_composition_confirmations WHERE scope_id=%s
             AND composition_object_id=%s AND composition_revision_id=%s""",
        (ex.ctx.scope_id, comp["object_id"], rid)).fetchall()
    if {(str(r["principal_id"]), str(r["assignment_id"]), r["responsibility_role"])
            for r in signed if r["manifest_hash"] == manifest["manifest_hash"]} != {
            (s["principal_id"], s["assignment_id"], s["responsibility_role"]) for s in slots}:
        fail()
    formal = db.jsonable(ex.conn.execute(
        "SELECT * FROM gov_round_formal_submissions WHERE scope_id=%s AND round_object_id=%s",
        (ex.ctx.scope_id, round_id)).fetchall())
    if {f["domain_id"] for f in formal} != {m["domain_id"] for m in members}:
        fail()
    submissions = {}
    refs = [manifest["company_reference_ref"]]
    for member in manifest["members"]:
        f = next(f for f in formal if f["domain_id"] == member["domain_id"])
        sub = revision(ex, f["submission_object_id"], f["submission_revision_id"])
        if (member["submission_ref"] != {k: sub[k] for k in ("object_id", "revision_id", "payload_hash")}
                or f["published_by_assignment_id"] != member["dri_assignment_id"]
                or f["published_by_principal_id"] != member["dri_principal_id"]):
            fail()
        submissions[member["domain_id"]] = sub
        refs.extend(a2_rounds.source_refs(sub["payload"]["submission"]))
    refs.extend(dep["source_ref"] for dep in manifest["binding_dependencies"])
    for judgment in manifest["judgments"].values():
        refs.extend(judgment["evidence_refs"])
    return {"round_object_id": round_id, "round_head": head, "round_state": state,
            "composition_head": comp, "composition_ref": composition_ref,
            "activation": activation, "definition": definition, "manifest": manifest,
            "formal_submissions": submissions, "source_refs": refs}
