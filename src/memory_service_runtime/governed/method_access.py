"""Current, object-scoped Method permissions. A review grant never propagates."""
from __future__ import annotations
from . import db, protocol
from .errors import GovernedError


def is_method_object(conn, ctx, object_id):
    binding = protocol.current_binding(conn, ctx.scope_id, str(object_id))
    return bool(binding and binding["protocol_id"] == "tkos.method")


def assignment(conn, ctx, assignment_id, principal_id=None, principal_type=None, domain_id=None):
    row = conn.execute(
        """SELECT a.*,p.principal_type FROM gov_role_assignments a JOIN gov_principals p
        ON (a.scope_id,a.principal_id)=(p.scope_id,p.principal_id)
        WHERE a.scope_id=%s AND a.assignment_id=%s AND a.active AND p.active
        AND a.valid_from<=clock_timestamp() AND (a.valid_to IS NULL OR a.valid_to>clock_timestamp())""",
        (ctx.scope_id, assignment_id)).fetchone()
    if row is None:
        raise GovernedError("FORBIDDEN", "Responsibility is no longer active.")
    row = db.jsonable(row)
    if ((principal_id is not None and row["principal_id"] != principal_id)
            or (principal_type is not None and row["principal_type"] != principal_type)
            or (domain_id is not None and row["domain_id"] != domain_id)):
        raise GovernedError("FORBIDDEN", "Responsibility does not bind this person and scope.")
    return row


def personal_agent(conn, ctx, agent_id, owner_id):
    rows = conn.execute("SELECT assignment_id FROM gov_method_agent_bindings WHERE scope_id=%s AND agent_principal_id=%s AND owner_principal_id=%s",
                        (ctx.scope_id, agent_id, owner_id)).fetchall()
    owner_rows = conn.execute("SELECT assignment_id FROM gov_role_assignments WHERE scope_id=%s AND principal_id=%s",
                              (ctx.scope_id, owner_id)).fetchall()
    owner_active = False
    for owner_row in owner_rows:
        try:
            assignment(conn, ctx, str(owner_row["assignment_id"]), owner_id, "human")
            owner_active = True
            break
        except GovernedError:
            continue
    if not owner_active:
        raise GovernedError("FORBIDDEN", "The personal Agent's owner no longer has an active responsibility.")
    for row in rows:
        try:
            value = assignment(conn, ctx, str(row["assignment_id"]), agent_id, "agent")
            if value["role"] in {"PERSONAL_AGENT", "CEO_AGENT"}:
                return value
        except GovernedError:
            continue
    raise GovernedError("FORBIDDEN", "Agent has no current personal identity binding.")


def window_participant(conn, ctx, payload):
    for member in payload.get("participants", []):
        try:
            human = assignment(conn, ctx, member["assignment_id"], member["principal_id"], "human")
            if member["principal_id"] == ctx.principal_id and ctx.principal_type == "human":
                return human
            if member.get("personal_agent_id") == ctx.principal_id and ctx.principal_type == "agent":
                return personal_agent(conn, ctx, ctx.principal_id, member["principal_id"])
        except GovernedError:
            continue
    raise GovernedError("FORBIDDEN", "The caller is not a current participant in this window.")


def research_participant(conn, ctx, state):
    roles = {"ceo_principal_id": ("CEO", "human"), "dri_principal_id": ("DOMAIN_DRI", "human"),
             "ceo_agent_id": ("CEO_AGENT", "agent"), "dri_agent_id": ("PERSONAL_AGENT", "agent"),
             "co_agent_id": ("CO_AGENT", "agent")}
    for key, (role, kind) in roles.items():
        if state.get(key) != ctx.principal_id or ctx.principal_type != kind:
            continue
        rows = conn.execute("SELECT assignment_id FROM gov_role_assignments WHERE scope_id=%s AND principal_id=%s AND role=%s",
                            (ctx.scope_id, ctx.principal_id, role)).fetchall()
        for row in rows:
            try:
                current = assignment(conn, ctx, str(row["assignment_id"]), ctx.principal_id, kind)
                if key in {"ceo_agent_id", "dri_agent_id"}:
                    owner_key = "ceo_principal_id" if key == "ceo_agent_id" else "dri_principal_id"
                    personal_agent(conn, ctx, ctx.principal_id, state[owner_key])
                    owner_role = "CEO" if key == "ceo_agent_id" else "DOMAIN_DRI"
                    owners = conn.execute("SELECT assignment_id FROM gov_role_assignments WHERE scope_id=%s AND principal_id=%s AND role=%s",
                                          (ctx.scope_id, state[owner_key], owner_role)).fetchall()
                    owner_active = False
                    for owner in owners:
                        try:
                            assignment(conn, ctx, str(owner["assignment_id"]), state[owner_key], "human")
                            owner_active = True
                        except GovernedError:
                            continue
                    if not owner_active:
                        continue
                return current
            except GovernedError:
                continue
    raise GovernedError("FORBIDDEN", "The caller is not assigned to this research issue.")


def research_grants(conn, ctx, head):
    allowed = set()
    # StrategicJudgment is a result of an issue, not an issue artifact with an
    # issue_ref field. Only its exact, server-written provenance establishes a
    # grant; this does not make its whole domain or other sources readable.
    judgments = []
    if head["object_type"] == "StrategicJudgment":
        judgments = db.jsonable(conn.execute("""SELECT object_id,revision_id,payload,payload_hash
            FROM gov_object_revisions WHERE scope_id=%s AND object_id=%s""",
            (ctx.scope_id, head["object_id"])).fetchall())
    issues = conn.execute("""SELECT o.object_id,s.state FROM gov_objects o JOIN gov_method_state s
        ON (s.scope_id,s.object_id)=(o.scope_id,o.object_id)
        WHERE o.scope_id=%s AND o.object_type='StrategicIssue'""", (ctx.scope_id,)).fetchall()
    for issue in db.jsonable(issues):
        if not is_method_object(conn, ctx, issue["object_id"]):
            continue
        try:
            research_participant(conn, ctx, issue["state"])
        except GovernedError:
            continue
        rows = conn.execute("""SELECT r.object_id,r.revision_id,r.payload,r.payload_hash,o.object_type
            FROM gov_object_revisions r JOIN gov_objects o ON (o.scope_id,o.object_id)=(r.scope_id,r.object_id)
            WHERE r.scope_id=%s AND (r.object_id=%s OR r.payload->'issue_ref'->>'object_id'=%s)""",
            (ctx.scope_id, issue["object_id"], issue["object_id"])).fetchall()
        rows = db.jsonable(rows)
        source_versions = {(r["object_id"], r["revision_id"]): r for r in rows}
        for judgment in judgments:
            agreement_ref = judgment["payload"].get("source_agreement_ref") or {}
            proposal_ref = judgment["payload"].get("source_proposal_ref") or {}
            agreement = source_versions.get((agreement_ref.get("object_id"), agreement_ref.get("revision_id")))
            proposal = source_versions.get((proposal_ref.get("object_id"), proposal_ref.get("revision_id")))
            if (agreement and proposal and agreement["object_type"] == "StrategicAgreement"
                    and proposal["object_type"] == "StrategyUpdateProposal"
                    and agreement["payload_hash"] == agreement_ref.get("payload_hash")
                    and proposal["payload_hash"] == proposal_ref.get("payload_hash")
                    and proposal["payload"].get("agreement_ref") == agreement_ref
                    and agreement["payload"].get("issue_ref", {}).get("object_id") == issue["object_id"]
                    and proposal["payload"].get("issue_ref", {}).get("object_id") == issue["object_id"]):
                allowed.add(judgment["revision_id"])
        for row in rows:
            if row["object_id"] == head["object_id"]:
                allowed.add(row["revision_id"])
            # Explicit research materials are shared by exact version. This
            # never traverses their sources or arbitrary other business objects.
            if head["object_type"] == "EvidenceAsset":
                for key in ("source_refs", "material_refs", "evidence_refs"):
                    for ref in row["payload"].get(key, []):
                        if ref.get("object_id") == head["object_id"]:
                            allowed.add(ref["revision_id"])
    return allowed


def raw_revision(conn, ctx, object_id, revision_id):
    row = conn.execute("SELECT * FROM gov_object_revisions WHERE scope_id=%s AND object_id=%s AND revision_id=%s",
                       (ctx.scope_id, object_id, revision_id)).fetchone()
    if row is None:
        raise GovernedError("NOT_FOUND")
    return db.jsonable(row)


def head_access(conn, ctx, object_id):
    """Return head and allowed revision IDs (None = explicit domain read right)."""
    db._assignments(conn, ctx)
    oid = db._uuid(object_id)
    head = conn.execute("SELECT * FROM gov_objects WHERE scope_id=%s AND object_id=%s", (ctx.scope_id, oid)).fetchone()
    if head is None:
        raise GovernedError("NOT_FOUND")
    head = db.jsonable(head)
    try:
        db.authorize_domain(conn, ctx, head["domain_id"], "read")
        return head, None
    except GovernedError as exc:
        if exc.code != "FORBIDDEN":
            raise
    if not is_method_object(conn, ctx, oid):
        raise GovernedError("NOT_FOUND")
    allowed = research_grants(conn, ctx, head)
    windows = conn.execute(
        """SELECT o.object_id,r.revision_id,r.payload FROM gov_objects o
        JOIN gov_object_revisions r ON (r.scope_id,r.object_id,r.revision_id)=(o.scope_id,o.object_id,o.latest_revision_id)
        JOIN gov_object_protocol_bindings b ON b.scope_id=o.scope_id AND b.object_id=o.object_id
        WHERE o.scope_id=%s AND o.object_type='ReviewWindow' AND b.protocol_id='tkos.method'
        AND b.binding_version=(SELECT MAX(x.binding_version) FROM gov_object_protocol_bindings x WHERE x.scope_id=b.scope_id AND x.object_id=b.object_id)""",
        (ctx.scope_id,)).fetchall()
    for window in db.jsonable(windows):
        try:
            window_participant(conn, ctx, window["payload"])
        except GovernedError:
            continue
        if window["object_id"] == oid:
            allowed.add(window["revision_id"])
        original_ids = {ref["object_id"] for ref in window["payload"].get("target_refs", [])}
        for ref in window["payload"].get("target_refs", []):
            if ref["object_id"] == oid:
                allowed.add(ref["revision_id"])
        state = conn.execute("SELECT state FROM gov_method_state WHERE scope_id=%s AND object_id=%s", (ctx.scope_id, window["object_id"])).fetchone()
        candidate_ref = (state["state"] if state else {}).get("candidate_ref")
        if candidate_ref:
            candidate = raw_revision(conn, ctx, candidate_ref["object_id"], candidate_ref["revision_id"])
            if candidate_ref["object_id"] == oid:
                allowed.add(candidate_ref["revision_id"])
            for ref in candidate["payload"].get("target_refs", []):
                if ref["object_id"] == oid and oid in original_ids:
                    allowed.add(ref["revision_id"])
    # Confirmed responsibility confers the exact Mission and its referenced PCO,
    # not the company domain, its sources, or future unrelated candidate drafts.
    missions = conn.execute("""SELECT o.object_id,r.revision_id,r.payload FROM gov_objects o JOIN gov_object_revisions r
        ON (r.scope_id,r.object_id,r.revision_id)=(o.scope_id,o.object_id,o.effective_revision_id)
        WHERE o.scope_id=%s AND o.object_type='Mission'""", (ctx.scope_id,)).fetchall()
    for mission in db.jsonable(missions):
        if not is_method_object(conn, ctx, mission["object_id"]):
            continue
        people = [mission["payload"].get("owner_principal_id"), *mission["payload"].get("participants", [])]
        if ctx.principal_type == "human" and ctx.principal_id in people:
            if mission["object_id"] == oid:
                allowed.add(mission["revision_id"])
            reference = mission["payload"].get("pco_ref", {})
            if reference.get("object_id") == oid:
                allowed.add(reference["revision_id"])
    if not allowed:
        raise GovernedError("NOT_FOUND")
    return head, allowed


def head(conn, ctx, object_id):
    value, _ = head_access(conn, ctx, object_id)
    protocol.require_read_support(conn, ctx.scope_id, value["object_id"])
    return value


def revision(conn, ctx, object_id, revision_id):
    value, allowed = head_access(conn, ctx, object_id)
    protocol.require_read_support(conn, ctx.scope_id, value["object_id"])
    if allowed is not None and str(revision_id) not in allowed:
        raise GovernedError("NOT_FOUND")
    return raw_revision(conn, ctx, value["object_id"], db._uuid(revision_id))
