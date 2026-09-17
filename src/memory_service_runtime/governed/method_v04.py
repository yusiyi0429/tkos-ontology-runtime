"""tkos.method/0.4 formal governance actions on the atomic Method transaction.

Implements the user-confirmed 0.4 rules:

* CEO Agent direct issue initiation/reframing/association on its own MethodRun.
* Agreement: every nominated current human (including the CEO) confirms the
  exact same revision; Agent drafts only; roster/body/evidence change
  invalidates pending confirmations; formalization rechecks all appointments.
* Paired Strategy/Architecture adoption: unchanged objects keep their exact
  revision with an explicit applicability rationale; changed objects receive
  new atomic revisions under current-basis CAS.
* M1B: one window freezes the complete multi-PCO + Mission membership; every
  frozen effective opinion is resolved exactly once; named DRI/Owner commit
  their own responsibility against the exact candidate set; the CEO activates
  the whole set atomically; critical unresolved differences block activation.
* State responsibility is live (LTCO=current CEO, PCO=Scope DRI, Mission=owner);
  PeriodReview is canonical-ref analysis without approval.
* Strategic problem transfer links issue and source, then marks
  transferred/tracking=false in the same transaction; transfer is not resolved.

Nothing here creates execution, acceptance or Outcome effects.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from hashlib import sha256

from psycopg.types.json import Jsonb

from . import db, method_access as access, protocol
from .errors import GovernedError


def fail(message: str, code: str = "INVALID_STATE") -> None:
    raise GovernedError(code, message)


def _exact(head, revision):
    return {"object_id": head["object_id"], "revision_id": revision["revision_id"],
            "payload_hash": revision["payload_hash"]}


def _same(a, b) -> bool:
    return bool(a and b) and all(a.get(k) == b.get(k) for k in ("object_id", "revision_id", "payload_hash"))


def _phase(state, *allowed):
    if state.get("phase") not in allowed:
        fail("This action is not available in the current Method phase.")
    return deepcopy(state)


def _agent(e, role, owner=None):
    e.require_role(role, principal_type="agent")
    if owner is not None:
        e.check_personal_agent(e.ctx.principal_id, owner)


def _ceo_agent_owner(e) -> str:
    """Return the unique current CEO this Agent is bound to in the action domain."""
    _agent(e, "CEO_AGENT")
    rows = e.conn.execute(
        "SELECT owner_principal_id FROM gov_method_agent_bindings WHERE scope_id=%s AND agent_principal_id=%s",
        (e.ctx.scope_id, e.ctx.principal_id)).fetchall()
    owners = set()
    for row in db.jsonable(rows):
        try:
            e.validate_principal(row["owner_principal_id"], "human", "CEO", e.domain_id)
            e.check_personal_agent(e.ctx.principal_id, row["owner_principal_id"])
            owners.add(row["owner_principal_id"])
        except GovernedError:
            continue
    if len(owners) != 1:
        fail("A unique current same-domain CEO binding is required.", "FORBIDDEN")
    return next(iter(owners))


def _human_ceo(e):
    e.require_role("CEO", "human")


def _current_ceo(e, domain_id):
    rows = e.conn.execute(
        "SELECT assignment_id, principal_id FROM gov_role_assignments WHERE scope_id=%s AND domain_id=%s AND role='CEO'",
        (e.ctx.scope_id, domain_id)).fetchall()
    owners = {}
    for row in db.jsonable(rows):
        try:
            owners[row["principal_id"]] = e.validate_principal(row["principal_id"], "human", "CEO", domain_id)
        except GovernedError:
            continue
    if len(owners) != 1:
        fail("A unique current company CEO is required.", "FORBIDDEN")
    principal = next(iter(owners))
    return principal, owners[principal]


def _ref_payload(e, reference, types):
    head, revision = e.ref(reference, types=set(types), current=False)
    return head, revision, revision["payload"]


def _architecture_payload(e, reference):
    # Internal responsibility derivation reads the exact basis server-side: a
    # mapping never grants the caller domain role, but the DRI/Owner still needs
    # the responsibility fact to be resolved.  The write path keeps its own CAS.
    head, revision = _LightExecution(e.conn, e.ctx).ref(reference, types={"StrategicArchitecture"})
    return head, revision, revision["payload"]


def _scope_definition(e, architecture_payload, unit_id):
    """Primary Scope may be a Domain or a Battlefield stable unit."""
    definition = next((d for d in [*architecture_payload["battlefields"], *architecture_payload["domains"]]
                       if d["unit_id"] == unit_id), None)
    if definition is None:
        fail("The primary business Scope is absent from the exact Architecture.", "INVALID_REQUEST")
    return definition


def _domain_definition(e, architecture_payload, unit_id):
    """Required Capability primary responsibility remains a Domain unit."""
    domain = next((d for d in architecture_payload["domains"] if d["unit_id"] == unit_id), None)
    if domain is None:
        fail("The primary Domain is absent from the exact Architecture.", "INVALID_REQUEST")
    return domain


def _check_architecture(e, payload, old=None):
    """Existing auth-domain mapping + stable Battlefield/Domain IDs, no implicit access."""
    for definition in [*payload["battlefields"], *payload["domains"]]:
        if not definition["unit_id"]:
            fail("Architecture definitions require stable IDs.", "INVALID_REQUEST")
    for definition in [*payload["battlefields"], *payload["domains"]]:
        auth_domain = definition.get("auth_domain_id")
        if auth_domain is None:
            if definition.get("current_dri_principal_id"):
                fail("A current DRI mapping requires an explicit authorization domain.", "INVALID_REQUEST")
            continue
        row = e.conn.execute("SELECT 1 FROM gov_domains WHERE scope_id=%s AND domain_id=%s",
                             (e.ctx.scope_id, auth_domain)).fetchone()
        if not row:
            fail("The mapped authorization domain does not exist in this scope.", "INVALID_REQUEST")
        if definition.get("current_dri_principal_id"):
            e.validate_principal(definition["current_dri_principal_id"], "human", "DOMAIN_DRI", auth_domain)
    if old:
        history = e.conn.execute(
            "SELECT payload FROM gov_object_revisions WHERE scope_id=%s AND object_id=%s AND revision_id<>%s",
            (e.ctx.scope_id, old["object_id"], old["revision_id"])).fetchall()
        prior = {}
        for row in db.jsonable(history):
            for definition in row["payload"]["battlefields"]:
                prior[definition["unit_id"]] = "battlefield"
            for definition in row["payload"]["domains"]:
                prior[definition["unit_id"]] = "domain"
        for kind, values in (("battlefield", payload["battlefields"]), ("domain", payload["domains"])):
            for definition in values:
                if definition["unit_id"] in prior and prior[definition["unit_id"]] != kind:
                    fail("Stable definition IDs cannot change their nature.")


def _current_architecture(e, strategy_head):
    reference = e.state(strategy_head).get("architecture_ref")
    if not reference:
        fail("The Strategy has no confirmed Architecture.")
    head, revision = e.ref(reference, types={"StrategicArchitecture"}, effective=True, current=False)
    return head, revision


def _formal_pair_exists(e, domain_id) -> bool:
    row = e.conn.execute("SELECT object_id, revision_id FROM gov_method_strategy_heads WHERE scope_id=%s AND domain_id=%s",
                         (e.ctx.scope_id, domain_id)).fetchone()
    if row is None:
        return False
    head = e.head(str(row["object_id"]))
    return bool(e.state(head).get("architecture_ref"))


def _issue_basis(e, payload):
    if payload.get("strategy_ref"):
        head, revision = e.current_strategy(payload["strategy_ref"])
        e.ref(payload["architecture_ref"], types={"StrategicArchitecture"}, effective=True, current=False)
        current = e.state(head).get("architecture_ref")
        if not _same(payload["architecture_ref"], current):
            fail("The issue must cite the current Strategy/Architecture pair.", "STALE_DEPENDENCY")
    elif _formal_pair_exists(e, e.domain_id):
        fail("An existing formal Strategy/Architecture pair must be cited.", "STALE_DEPENDENCY")


def _source_refs(e, refs):
    for reference in refs:
        e.ref(reference, current=False)


# ----------------------------------------------------------------- authority


V04_SCOPED_ACTIONS = frozenset({
    "m1a_confirm_agreement", "m1b_comment", "m1b_replace_comment", "m1b_withdraw_comment",
    "m1b_assist_review", "m1b_commit_candidate", "method_confirm_state",
    "method_revise_problem", "method_close_problem",
})


def scoped_assignment(conn, ctx, kind, target, revision):
    """Resolve the caller's own responsibility for a scoped 0.4 action.

    Called only after normal domain authorization returned FORBIDDEN; the
    returned assignment still goes through db.authorize_domain in its own
    domain, so a review grant never becomes a company-wide role.
    """
    payload = revision["payload"]
    if kind == "m1a_confirm_agreement":
        for signer in payload["participants"]:
            if signer["principal_id"] == ctx.principal_id:
                return access.assignment(conn, ctx, signer["assignment_id"], ctx.principal_id, "human")
        raise GovernedError("FORBIDDEN")
    if kind in {"m1b_comment", "m1b_replace_comment", "m1b_withdraw_comment", "m1b_assist_review"}:
        return access.window_participant(conn, ctx, payload)
    if kind == "m1b_commit_candidate":
        for reference in payload["target_refs"]:
            owner = _commitment_owner(conn, ctx, reference)
            if owner is not None:
                for row in db._assignments(conn, ctx):
                    if row["principal_id"] == owner:
                        try:
                            return access.assignment(conn, ctx, row["assignment_id"], owner, "human")
                        except GovernedError:
                            continue
        raise GovernedError("FORBIDDEN")
    if kind == "method_confirm_state":
        subject = payload["subject_ref"]
        owner = _state_subject_owner_static(conn, ctx, subject)
        for row in db._assignments(conn, ctx):
            if row["principal_id"] == owner:
                try:
                    return access.assignment(conn, ctx, row["assignment_id"], owner, "human")
                except GovernedError:
                    continue
        raise GovernedError("FORBIDDEN")
    if kind in {"method_revise_problem", "method_close_problem"}:
        return access.assignment(conn, ctx, payload["responsible_assignment_id"], ctx.principal_id, "human")
    raise GovernedError("FORBIDDEN")


def _commitment_owner(conn, ctx, reference):
    """Required committer for one candidate target, using live appointments."""
    row = conn.execute("SELECT object_id FROM gov_objects WHERE scope_id=%s AND object_id=%s",
                       (ctx.scope_id, reference["object_id"])).fetchone()
    if row is None:
        return None
    head = db.jsonable(conn.execute("SELECT * FROM gov_objects WHERE scope_id=%s AND object_id=%s",
                                    (ctx.scope_id, reference["object_id"])).fetchone())
    revision = access.raw_revision(conn, ctx, reference["object_id"], reference["revision_id"])
    context = _LightExecution(conn, ctx)
    if head["object_type"] == "PCO":
        return _pco_dri(context, revision["payload"])[0]
    if head["object_type"] == "Mission":
        return revision["payload"]["owner_principal_id"]
    return None


def _state_subject_owner_static(conn, ctx, subject):
    """Read-only responsibility lookup used only by the scoped-authorization fallback."""
    head = db.jsonable(conn.execute("SELECT * FROM gov_objects WHERE scope_id=%s AND object_id=%s",
                                    (ctx.scope_id, subject["object_id"])).fetchone())
    if head is None or not head.get("effective_revision_id"):
        raise GovernedError("FORBIDDEN")
    revision = access.raw_revision(conn, ctx, subject["object_id"], str(head["effective_revision_id"]))
    context = _LightExecution(conn, ctx)
    if head["object_type"] == "LTCO":
        return _current_ceo(context, head["domain_id"])[0]
    if head["object_type"] == "PCO":
        return _pco_dri(context, revision["payload"])[0]
    if head["object_type"] == "Mission":
        return revision["payload"]["owner_principal_id"]
    raise GovernedError("FORBIDDEN")


class _LightExecution:
    """Read-only resolver for the scoped-authorization fallback only.

    It verifies exact revision hashes and the 0.4 binding before any helper
    reads a responsibility field, so a fallback can never resolve a foreign
    protocol object or a guessed revision.
    """

    def __init__(self, conn, ctx):
        self.conn, self.ctx = conn, ctx
        self.domain_id = None
        self.required_assignments = set()
        self.heads = {}
        self.revisions = {}

    def ref(self, reference, types=None, effective=False, current=False):
        head = db.jsonable(self.conn.execute(
            "SELECT * FROM gov_objects WHERE scope_id=%s AND object_id=%s",
            (self.ctx.scope_id, reference["object_id"])).fetchone())
        if head is None:
            raise GovernedError("NOT_FOUND")
        revision = access.raw_revision(self.conn, self.ctx, reference["object_id"], reference["revision_id"])
        if reference.get("payload_hash") and revision["payload_hash"] != reference["payload_hash"]:
            raise GovernedError("STALE_DEPENDENCY")
        if types and head["object_type"] not in types:
            raise GovernedError("NOT_FOUND")
        binding = protocol.current_binding(self.conn, self.ctx.scope_id, reference["object_id"])
        if binding is None or binding["contract_version"] != "tkos.method/0.4":
            raise GovernedError("PROTOCOL_BINDING_CONFLICT")
        if effective and str(head.get("effective_revision_id")) != str(revision["revision_id"]):
            raise GovernedError("STALE_DEPENDENCY")
        if current and str(head.get("latest_revision_id")) != str(revision["revision_id"]):
            raise GovernedError("STALE_DEPENDENCY")
        self.heads[str(head["object_id"])] = head
        self.revisions[str(revision["revision_id"])] = revision
        return head, revision

    def head(self, object_id):
        object_id = str(object_id)
        if object_id not in self.heads:
            head = db.jsonable(self.conn.execute(
                "SELECT * FROM gov_objects WHERE scope_id=%s AND object_id=%s",
                (self.ctx.scope_id, object_id)).fetchone())
            if head is None:
                raise GovernedError("NOT_FOUND")
            binding = protocol.current_binding(self.conn, self.ctx.scope_id, object_id)
            if binding is None or binding["contract_version"] != "tkos.method/0.4":
                raise GovernedError("PROTOCOL_BINDING_CONFLICT")
            self.heads[object_id] = head
        return self.heads[object_id]

    def revision(self, object_id, revision_id):
        object_id = str(object_id)
        if str(revision_id) not in self.revisions:
            row = access.raw_revision(self.conn, self.ctx, object_id, revision_id)
            self.revisions[str(row["revision_id"])] = row
        return self.revisions[str(revision_id)]

    def state(self, head):
        row = self.conn.execute("SELECT state FROM gov_method_state WHERE scope_id=%s AND object_id=%s",
                                (self.ctx.scope_id, head["object_id"])).fetchone()
        return deepcopy(db.jsonable(row["state"])) if row else {}

    def validate_principal(self, principal_id, principal_type="human", role=None, domain_id=None):
        rows = self.conn.execute(
            "SELECT assignment_id FROM gov_role_assignments WHERE scope_id=%s AND principal_id=%s",
            (self.ctx.scope_id, principal_id)).fetchall()
        for row in db.jsonable(rows):
            try:
                value = access.assignment(self.conn, self.ctx, str(row["assignment_id"]),
                                          principal_id, principal_type, domain_id)
            except GovernedError:
                continue
            if role is None or value["role"] == role:
                return value
        raise GovernedError("FORBIDDEN")


def _pco_responsibility(e, payload):
    architecture = _architecture_payload(e, payload["architecture_ref"])[2]
    scope = _scope_definition(e, architecture, payload["primary_scope_id"])
    auth_domain = scope.get("auth_domain_id")
    if auth_domain is None:
        fail("The primary Scope has no explicit authorization-domain mapping; add one in the Architecture.",
             "INVALID_REQUEST")
    rows = e.conn.execute(
        "SELECT assignment_id, principal_id FROM gov_role_assignments WHERE scope_id=%s AND domain_id=%s AND role='DOMAIN_DRI'",
        (e.ctx.scope_id, auth_domain)).fetchall()
    owners = {}
    for row in db.jsonable(rows):
        try:
            owners[row["principal_id"]] = e.validate_principal(row["principal_id"], "human", "DOMAIN_DRI", auth_domain)
        except GovernedError:
            continue
    if len(owners) != 1:
        fail("A unique current Scope DRI is required.", "FORBIDDEN")
    if scope.get("current_dri_principal_id") and scope["current_dri_principal_id"] not in owners:
        fail("The Architecture's explicit current DRI no longer holds the appointment.", "FORBIDDEN")
    principal = next(iter(owners))
    return principal, "DOMAIN_DRI", auth_domain, owners[principal]


def _pco_dri(e, payload):
    principal, _role, _auth_domain, assignment = _pco_responsibility(e, payload)
    return principal, assignment


def _mission_owner(e, payload):
    return payload["owner_principal_id"], e.validate_principal(payload["owner_principal_id"], "human")


def _state_subject_owner(e, subject_ref):
    head, revision = e.ref(subject_ref, types={"LTCO", "PCO", "Mission"}, effective=True, current=False)
    payload = revision["payload"]
    if head["object_type"] == "LTCO":
        return _current_ceo(e, head["domain_id"])
    if head["object_type"] == "PCO":
        return _pco_dri(e, payload)
    return _mission_owner(e, payload)


# ------------------------------------------------------------------ M1A/M1B


def _validate_people(e, participants):
    for participant in participants:
        e.validate_assignment(participant["assignment_id"], participant["principal_id"], "human")
        if participant.get("personal_agent_id"):
            e.validate_principal(participant["personal_agent_id"], "agent")
            row = access.personal_agent(e.conn, e.ctx, participant["personal_agent_id"], participant["principal_id"])
            e.validate_assignment(row["assignment_id"], participant["personal_agent_id"], "agent")
            e.validate_principal(participant["principal_id"], "human")


def _agreement_ceo(e, signers, domain_id):
    ceos = []
    for signer in signers:
        row = e.validate_assignment(signer["assignment_id"], signer["principal_id"], "human")
        if row["role"] == "CEO" and row["domain_id"] == domain_id:
            ceos.append(signer["principal_id"])
    if len(set(ceos)) != 1:
        fail("The current CEO is a mandatory Agreement signer.", "FORBIDDEN")
    return ceos[0]


def _agreement_basis(e, payload):
    e.ref(payload["issue_ref"], types={"StrategicIssue"}, current=True)
    _source_refs(e, payload["evidence_refs"])


def _agreement_confirmations(e, agreement_head, agreement_revision):
    rows = e.conn.execute(
        """SELECT record_id, principal_id FROM gov_method_reviews
           WHERE scope_id=%s AND kind='agreement_confirmation' AND target_object_id=%s AND target_revision_id=%s
           ORDER BY recorded_at, record_id""",
        (e.ctx.scope_id, agreement_head["object_id"], agreement_revision["revision_id"])).fetchall()
    confirms = {}
    for row in db.jsonable(rows):
        confirms.setdefault(str(row["principal_id"]), str(row["record_id"]))
    return confirms


def _formalize_agreement(e, head, revision, state):
    payload = revision["payload"]
    for signer in payload["participants"]:
        e.validate_assignment(signer["assignment_id"], signer["principal_id"], "human")
    _agreement_ceo(e, payload["participants"], head["domain_id"])
    record = e.review("agreement_formalized", _exact(head, revision),
                      {"signers": [s["principal_id"] for s in payload["participants"]],
                       "issue_ref": payload["issue_ref"]})
    e.transition(head, status="confirmed", effective=True)
    state.update(phase="formal", confirmed_principal_ids=sorted(c["principal_id"] for c in payload["participants"]),
                 formalization_record_id=record)
    e.set_state(head, state)
    # The issue round advances only when the whole exact Agreement is formal.
    issue_head, issue_revision = e.ref(payload["issue_ref"], types={"StrategicIssue"}, current=True)
    issue_state = deepcopy(e.state(issue_head))
    issue_state.update(phase="agreement_formal", agreement_ref=_exact(head, revision))
    e.set_state(issue_head, issue_state)
    e.transition(issue_head)
    return record


# ------------------------------------------------------- M1A collect/run


def _collect_create_issue(e):
    _ceo_agent_owner(e)
    if len(e.run_ids) != 1:
        fail("Direct issue initiation requires one explicit MethodRun.", "INVALID_REQUEST")
    run_id = next(iter(e.run_ids))
    row = e.conn.execute("SELECT owner_principal_id, phase FROM gov_method_runs WHERE scope_id=%s AND run_id=%s",
                         (e.ctx.scope_id, run_id)).fetchone()
    if row is None or str(row["owner_principal_id"]) != e.ctx.principal_id or row["phase"] != "running":
        fail("Only the running Agent-owned intake run may initiate issues.", "FORBIDDEN")
    payload = e.params["payload"]
    _issue_basis(e, payload)
    _source_refs(e, payload["source_refs"])
    e._v04["issue_payload"] = payload


def _collect_reframe_issue(e):
    _ceo_agent_owner(e)
    _phase(e.state(e.target), "issue_confirmed", "agreement_formal", "completed")
    payload = e.params["payload"]
    if payload.get("reframe_of_ref") and not _same(payload["reframe_of_ref"], _exact(e.target, e.target_revision)):
        fail("Reframing may only cite this exact issue revision.", "INVALID_REQUEST")
    _issue_basis(e, payload)
    _source_refs(e, payload["source_refs"])
    e._v04["issue_payload"] = payload


def _collect_associate_issue(e):
    _ceo_agent_owner(e)
    _phase(e.state(e.target), "issue_confirmed", "agreement_formal", "completed")
    _source_refs(e, e.params["source_refs"])


def _collect_set_participants(e):
    _human_ceo(e)
    _phase(e.state(e.target), "issue_confirmed", "agreement_formal", "completed")
    _validate_people(e, e.params["participants"])


def _collect_draft_agreement(e):
    _ceo_agent_owner(e)
    _phase(e.state(e.target), "issue_confirmed")
    if e.state(e.target).get("agreement_ref"):
        fail("This issue round already has an Agreement; revise it or reframe the issue.")
    payload = e.params["payload"]
    if not _same(payload["issue_ref"], _exact(e.target, e.target_revision)):
        fail("The Agreement must bind this exact issue revision.", "INVALID_REQUEST")
    _agreement_basis(e, payload)
    _validate_people(e, payload["participants"])
    _agreement_ceo(e, payload["participants"], e.domain_id)


def _collect_revise_agreement(e):
    _ceo_agent_owner(e)
    state = e.state(e.target)
    if state.get("phase") not in {"draft", "awaiting_confirmation"}:
        fail("A formal Agreement is immutable; reframe the issue for a new round.")
    payload = e.params["payload"]
    e.ref(payload["issue_ref"], types={"StrategicIssue"}, current=True)
    _agreement_basis(e, payload)
    _validate_people(e, payload["participants"])
    _agreement_ceo(e, payload["participants"], e.target["domain_id"])


def _collect_confirm_agreement(e):
    if e.ctx.principal_type != "human":
        fail("An Agent can only draft; it can never confirm an Agreement.", "FORBIDDEN")
    state = e.state(e.target)
    if state.get("phase") not in {"draft", "awaiting_confirmation"}:
        fail("This Agreement is not accepting confirmations.")
    payload = e.target_revision["payload"]
    signer = next((s for s in payload["participants"] if s["principal_id"] == e.ctx.principal_id), None)
    if signer is None:
        fail("Only a nominated current human signer may confirm this exact Agreement.", "FORBIDDEN")
    e.validate_assignment(signer["assignment_id"], signer["principal_id"], "human")
    _agreement_basis(e, payload)
    # Formalization rechecks the whole roster: every remaining signer must still
    # hold the appointment their confirmation would bind.
    confirms = _agreement_confirmations(e, e.target, e.target_revision)
    if all(str(s["principal_id"]) in confirms for s in payload["participants"]):
        _agreement_ceo(e, payload["participants"], e.target["domain_id"])


def _proposal_agreement(e, payload):
    head, revision = e.ref(payload["agreement_ref"], types={"StrategicAgreement"}, effective=True, current=True)
    if e.state(head).get("phase") != "formal":
        fail("The proposal requires a formal Agreement.", "STALE_DEPENDENCY")
    if not _same(revision["payload"]["issue_ref"], payload["issue_ref"]):
        fail("The Agreement does not bind the proposal's issue revision.", "STALE_DEPENDENCY")
    if revision["payload"]["no_change"]:
        fail("A no-change Agreement cannot drive a formal update.")
    return head, revision


def _proposal_basis(e, change):
    strategy_head = strategy_revision = None
    if change.get("strategy_target_ref"):
        strategy_head, strategy_revision = e.current_strategy(change["strategy_target_ref"])
    else:
        row = e.conn.execute("SELECT 1 FROM gov_method_strategy_heads WHERE scope_id=%s AND domain_id=%s",
                             (e.ctx.scope_id, e.domain_id)).fetchone()
        if row:
            fail("An effective Strategy already exists; cite its exact current revision.", "STALE_DEPENDENCY")
    architecture_head = architecture_revision = None
    if change.get("architecture_target_ref"):
        architecture_head, architecture_revision = e.ref(
            change["architecture_target_ref"], types={"StrategicArchitecture"}, effective=True, current=False)
        if strategy_head is not None:
            current = e.state(strategy_head).get("architecture_ref")
            if not _same(change["architecture_target_ref"], current):
                fail("The retained Architecture is not the current basis of the exact Strategy.", "STALE_DEPENDENCY")
    elif strategy_head is not None and e.state(strategy_head).get("architecture_ref"):
        fail("An effective Architecture already exists; cite its exact current revision.", "STALE_DEPENDENCY")
    if change.get("architecture"):
        old_architecture = architecture_revision
        _check_architecture(e, change["architecture"], old_architecture)
        domains = {d["unit_id"] for d in change["architecture"]["domains"]}
    else:
        domains = {d["unit_id"] for d in architecture_revision["payload"]["domains"]}
    if change.get("strategy"):
        for capability in change["strategy"]["required_capabilities"]:
            if capability["primary_domain_id"] not in domains:
                fail("Required Capability names an unknown primary Domain.", "INVALID_REQUEST")
    e._v04["old_strategy"] = _exact(strategy_head, strategy_revision) if strategy_head else None
    e._v04["old_architecture"] = _exact(architecture_head, architecture_revision) if architecture_head else None


def _collect_propose_update(e):
    _ceo_agent_owner(e)
    _phase(e.state(e.target), "agreement_formal")
    payload = e.params["payload"]
    if not _same(payload["issue_ref"], _exact(e.target, e.target_revision)):
        fail("The proposal must bind this exact issue revision.", "INVALID_REQUEST")
    _proposal_agreement(e, payload)
    _proposal_basis(e, payload["change"])


def _collect_review_update(e):
    _agent(e, "CO_AGENT")
    _phase(e.state(e.target), "draft")
    _proposal_basis(e, e.target_revision["payload"]["change"])


def _collect_confirm_update(e):
    _human_ceo(e)
    state = e.state(e.target)
    if state.get("phase") != "reviewed":
        fail("CEO confirmation needs an accepted Co-agent review of this exact proposal.")
    if not state.get("review", {}).get("accepted"):
        fail("The Co-agent returned this proposal; a new exact proposal is required.", "STALE_DEPENDENCY")
    payload = e.target_revision["payload"]
    _, agreement = _proposal_agreement(e, payload)
    e.ref(payload["issue_ref"], types={"StrategicIssue"}, current=True)
    _proposal_basis(e, payload["change"])


def _collect_transfer_problem(e):
    _ceo_agent_owner(e)
    state = e.state(e.target)
    if state.get("phase") not in {"issue_confirmed", "agreement_formal"}:
        fail("Transfer requires an active issue round; explicitly reframe a completed issue first.", "INVALID_STATE")
    problem, revision = e.ref(e.params["problem_ref"], types={"OperatingProblem"}, current=True)
    problem_state = e.state(problem)
    if problem_state.get("phase") != "open" or problem_state.get("tracking") is not True:
        fail("Only a currently tracked open Problem can transfer.", "INVALID_STATE")
    if revision["payload"]["level"] != "strategic":
        fail("Only strategic-level Problems transfer into M1A.", "INVALID_REQUEST")
    _source_refs(e, e.params["evidence_refs"])


def _ltco_check(e, payload):
    _architecture_payload(e, payload["architecture_ref"])
    e.current_strategy(payload["strategy_ref"])
    architecture = e.ref(payload["architecture_ref"], types={"StrategicArchitecture"}, effective=True, current=False)[1]
    strategy_head = e.ref(payload["strategy_ref"], types={"Strategy"}, effective=True, current=False)[0]
    if not _same(e.state(strategy_head).get("architecture_ref"), payload["architecture_ref"]):
        fail("LTCO must cite the Strategy's current Architecture.", "STALE_DEPENDENCY")
    _scope_definition(e, architecture["payload"], payload["primary_scope_id"])
    _source_refs(e, payload["baseline_refs"])


def _pco_check(e, payload):
    ltco_head, ltco = e.ref(payload["parent_ltco_ref"], types={"LTCO"}, effective=True, current=True)
    ltco_payload = ltco["payload"]
    _ltco_check(e, {
        "architecture_ref": payload["architecture_ref"], "strategy_ref": payload["strategy_ref"],
        "primary_scope_id": payload["primary_scope_id"], "baseline_refs": [payload["parent_ltco_ref"]],
    })
    if not (_same(ltco_payload["strategy_ref"], payload["strategy_ref"])
            and _same(ltco_payload["architecture_ref"], payload["architecture_ref"])):
        fail("PCO and its exact parent LTCO must share the Strategy/Architecture basis.", "STALE_DEPENDENCY")
    start = datetime.fromisoformat(payload["period"]["start"])
    end = datetime.fromisoformat(payload["period"]["end"])
    ltco_start = datetime.fromisoformat(ltco_payload["period"]["start"])
    ltco_end = datetime.fromisoformat(ltco_payload["period"]["end"])
    if not (ltco_start <= start < end <= ltco_end):
        fail("PCO period must be contained in its parent LTCO period.", "INVALID_REQUEST")


def _mission_check(e, payload, *, pco_head=None, pco_revision=None):
    if pco_head is None:
        pco_head, pco_revision = e.ref(payload["parent_pco_ref"], types={"PCO"}, current=False)
    pco_payload = pco_revision["payload"]
    if not _same(payload["parent_pco_ref"], _exact(pco_head, pco_revision)):
        fail("Mission must cite the exact parent PCO revision.", "INVALID_REQUEST")
    _architecture_payload(e, pco_payload["architecture_ref"])
    architecture = e.ref(pco_payload["architecture_ref"], types={"StrategicArchitecture"}, current=False)[1]
    _scope_definition(e, architecture["payload"], payload["primary_scope_id"])
    _mission_owner(e, payload)
    _source_refs(e, payload["evidence_refs"])
    start = datetime.fromisoformat(payload["period"]["start"])
    end = datetime.fromisoformat(payload["period"]["end"])
    pco_start = datetime.fromisoformat(pco_payload["period"]["start"])
    pco_end = datetime.fromisoformat(pco_payload["period"]["end"])
    if not (pco_start <= start < end <= pco_end):
        fail("Mission period must be contained in its parent PCO period.", "INVALID_REQUEST")


def _eligible_targets(e, window_payload, *, locked_window=None):
    """Complete company-period PCO+Mission membership for one exact window basis.

    The candidate universe is every latest-revision PCO on the window's exact
    Strategy/Architecture and period that is either an unlocked draft or is
    already frozen into this window (``locked_window``), plus every Mission
    whose exact parent PCO revision belongs to that frozen set.  Objects on a
    different basis/period are not phantom-included; objects inside this
    universe may not be silently omitted.
    """
    rows = db.jsonable(e.conn.execute(
        """SELECT o.object_id, o.object_type, o.latest_revision_id, r.payload
           FROM gov_objects o JOIN gov_object_revisions r
             ON (r.scope_id,r.object_id,r.revision_id)=(o.scope_id,o.object_id,o.latest_revision_id)
           WHERE o.scope_id=%s AND o.object_type IN ('PCO','Mission')""",
        (e.ctx.scope_id,)).fetchall())
    pcos, missions = {}, {}
    pco_revisions = {}
    for row in rows:
        if row["object_type"] != "PCO":
            continue
        state = e.state(e.head(str(row["object_id"])))
        if state.get("window_id") not in {None, locked_window}:
            continue
        if state.get("phase") != "draft" and state.get("window_id") != locked_window:
            continue
        if not (_same(row["payload"]["strategy_ref"], window_payload["strategy_ref"])
                and _same(row["payload"]["architecture_ref"], window_payload["architecture_ref"])
                and row["payload"]["period"] == window_payload["period"]):
            continue
        pcos[str(row["object_id"])] = row
        pco_revisions[str(row["object_id"])] = str(row["latest_revision_id"])
    for row in rows:
        if row["object_type"] != "Mission":
            continue
        state = e.state(e.head(str(row["object_id"])))
        if state.get("window_id") not in {None, locked_window}:
            continue
        if state.get("phase") != "draft" and state.get("window_id") != locked_window:
            continue
        parent = row["payload"].get("parent_pco_ref") or {}
        if str(parent.get("object_id")) not in pco_revisions:
            continue
        if str(parent.get("revision_id")) != pco_revisions[str(parent["object_id"])]:
            continue
        missions[str(row["object_id"])] = row
    return pcos, missions


def _collect_open_window(e):
    _agent(e, "CO_AGENT")
    payload = e.params["payload"]
    if payload.get("previous_window_ref"):
        fail("Only an explicit CEO reopen may attach a previous window.", "INVALID_REQUEST")
    _validate_people(e, payload["participants"])
    _window_basis(e, payload)
    pcos, missions = _validate_window_targets(e, payload, expected_phase="draft", locked_window=None)
    eligible_pcos, eligible_missions = _eligible_targets(e, payload)
    if set(pcos) != set(eligible_pcos):
        fail("The window must freeze the complete company-period PCO membership.", "INVALID_REQUEST")
    if set(missions) != set(eligible_missions):
        fail("The window must freeze the complete Mission membership of its PCOs.", "INVALID_REQUEST")
    parents = {str(m["revision"]["payload"]["parent_ltco_ref"]["object_id"]) for m in pcos.values()}
    if parents != {str(ref["object_id"]) for ref in payload["ltco_refs"]}:
        fail("Window ltco_refs must be exactly the frozen PCO parents.", "INVALID_REQUEST")
    e._v04["targets"] = {**pcos, **missions}


def _window_basis(e, payload):
    e.current_strategy(payload["strategy_ref"])
    e.ref(payload["architecture_ref"], types={"StrategicArchitecture"}, effective=True, current=False)
    for reference in payload["ltco_refs"]:
        _, revision = e.ref(reference, types={"LTCO"}, effective=True, current=False)
        if not (_same(revision["payload"]["strategy_ref"], payload["strategy_ref"])
                and _same(revision["payload"]["architecture_ref"], payload["architecture_ref"])):
            fail("Every frozen LTCO must share the window Strategy/Architecture basis.", "STALE_DEPENDENCY")


def _validate_window_targets(e, payload, *, expected_phase=None, locked_window=None):
    pcos, missions = {}, {}
    allowed_windows = {None, locked_window}
    ltco_ids = {str(ref["object_id"]) for ref in payload["ltco_refs"]}
    for reference in payload["pco_refs"]:
        head, revision = e.ref(reference, types={"PCO"}, current=True)
        state = e.state(head)
        if expected_phase is not None and state.get("phase") != expected_phase:
            fail("Window PCO targets must be drafts.", "INVALID_STATE")
        if state.get("window_id") not in allowed_windows:
            fail("A window target already belongs to another active review.", "INVALID_STATE")
        if not (_same(revision["payload"]["strategy_ref"], payload["strategy_ref"])
                and _same(revision["payload"]["architecture_ref"], payload["architecture_ref"])
                and str(revision["payload"]["parent_ltco_ref"]["object_id"]) in ltco_ids
                and revision["payload"]["period"] == payload["period"]):
            fail("Window targets must retain the exact reviewed period and basis.", "STALE_DEPENDENCY")
        pcos[str(head["object_id"])] = {"head": head, "revision": revision}
    for reference in payload["mission_refs"]:
        head, revision = e.ref(reference, types={"Mission"}, current=True)
        state = e.state(head)
        if expected_phase is not None and state.get("phase") != expected_phase:
            fail("Window Mission targets must be drafts.", "INVALID_STATE")
        if state.get("window_id") not in allowed_windows:
            fail("A window target already belongs to another active review.", "INVALID_STATE")
        parent = str(revision["payload"]["parent_pco_ref"]["object_id"])
        if parent not in pcos:
            fail("Every Mission must belong to one frozen PCO in this window.", "INVALID_REQUEST")
        _mission_check(e, revision["payload"], pco_head=pcos[parent]["head"],
                       pco_revision=pcos[parent]["revision"])
        missions[str(head["object_id"])] = {"head": head, "revision": revision}
    return pcos, missions


def _opinion_rows(e, window_head, state):
    rows = e.list_reviews(window_head["object_id"])
    result = {str(row["record_id"]): row for row in rows if row["kind"] == "window_comment"}
    active = {rid for ids in state.get("active_opinions", {}).values() for rid in ids}
    if not active.issubset(result):
        fail("Window opinion state does not match its immutable records.")
    return result


def _window_actor(e, payload, *, agent=False):
    if agent:
        participant = next((p for p in payload["participants"]
                            if p.get("personal_agent_id") == e.ctx.principal_id), None)
        if participant is None:
            fail("Only a participant's provisioned personal Agent can assist.", "FORBIDDEN")
        e.require_actor(e.ctx.principal_id, "agent")
        e.check_personal_agent(e.ctx.principal_id, participant["principal_id"])
        e.validate_principal(e.ctx.principal_id, "agent")
        return participant
    participant = next((p for p in payload["participants"] if p["principal_id"] == e.ctx.principal_id), None)
    if participant is None:
        fail("Only named window participants may publish their own opinion.", "FORBIDDEN")
    e.require_actor(e.ctx.principal_id, "human")
    e.validate_assignment(participant["assignment_id"], participant["principal_id"], "human")
    return participant


def _collect_window_opinion(e):
    _phase(e.state(e.target), "open")
    payload = e.target_revision["payload"]
    _window_actor(e, payload, agent=e.kind == "m1b_assist_review")
    records = _opinion_rows(e, e.target, e.state(e.target))
    if e.kind != "m1b_withdraw_comment":
        if not any(_same(e.params["target_ref"], ref) for ref in [*payload["pco_refs"], *payload["mission_refs"]]):
            fail("Window access is limited to its fixed exact targets.", "FORBIDDEN")
        e.ref(e.params["target_ref"], types={"PCO", "Mission"}, current=False)
    if e.kind in {"m1b_withdraw_comment", "m1b_replace_comment"}:
        prior = e.params["review_record_id"] if e.kind == "m1b_withdraw_comment" else e.params["replaces_record_id"]
        record = records.get(prior)
        active = e.state(e.target).get("active_opinions", {}).get(e.ctx.principal_id, [])
        if not record or prior not in active or str(record["principal_id"]) != e.ctx.principal_id:
            fail("Only the author may withdraw or replace an active opinion.", "FORBIDDEN")
        if e.kind == "m1b_replace_comment" and (
                str(record["target_object_id"]) != e.params["target_ref"]["object_id"]
                or str(record["target_revision_id"]) != e.params["target_ref"]["revision_id"]):
            fail("A replacement opinion must concern the same exact target.", "INVALID_REQUEST")
    if e.kind == "m1b_assist_review":
        for reference in e.params["source_refs"]:
            e.ref(reference, current=False)


def _collect_close_window(e):
    _agent(e, "CO_AGENT")
    _phase(e.state(e.target), "open")
    _opinion_rows(e, e.target, e.state(e.target))


def _frozen_opinions(state):
    return sorted({rid for ids in state.get("active_opinions", {}).values() for rid in ids})


def _collect_resolve_window(e):
    _agent(e, "CO_AGENT")
    state = _phase(e.state(e.target), "closed")
    if state.get("candidate_ref"):
        fail("A window admits only one formal resolution.")
    window = e.target_revision["payload"]
    params = e.params
    pcos, missions = _validate_window_targets(e, window, expected_phase=None, locked_window=e.target["object_id"])
    if {p["object_id"] for p in params["pcos"]} != set(pcos):
        fail("Resolution must cover every frozen PCO exactly once.", "INVALID_REQUEST")
    if {m["object_id"] for m in params["missions"]} != set(missions):
        fail("Resolution must include every frozen Mission exactly once.", "INVALID_REQUEST")
    for candidate in params["pcos"]:
        frozen = pcos[str(candidate["object_id"])]["revision"]["payload"]
        payload = candidate["payload"]
        if not (_same(payload["strategy_ref"], window["strategy_ref"])
                and _same(payload["architecture_ref"], window["architecture_ref"])
                and _same(payload["parent_ltco_ref"], frozen["parent_ltco_ref"])
                and payload["period"] == window["period"]
                and payload["primary_scope_id"] == frozen["primary_scope_id"]):
            fail("A candidate PCO must retain its reviewed scope, period and basis.", "STALE_DEPENDENCY")
    for candidate in params["missions"]:
        frozen = missions[str(candidate["object_id"])]["revision"]["payload"]
        if candidate["primary_scope_id"] != frozen["primary_scope_id"]:
            fail("A candidate Mission must retain its primary business Scope.", "STALE_DEPENDENCY")
    frozen_ids = set(state.get("frozen_opinion_ids", []))
    if {d["review_record_id"] for d in params["dispositions"]} != frozen_ids:
        fail("Resolution must account for every frozen effective opinion exactly once.", "INVALID_REQUEST")
    if len(params["dispositions"]) != len(frozen_ids):
        fail("Resolution dispositions must be unique.", "INVALID_REQUEST")
    e._m1b = {"state": state, "window": window, "pcos": pcos, "missions": missions}


def _required_committers(e, payload):
    required = {}
    for reference in payload["target_refs"]:
        e.ref(reference, types={"PCO", "Mission"}, current=True)
        member = e.head(reference["object_id"])
        revision = e.revision(reference["object_id"], reference["revision_id"])
        if member["object_type"] == "Mission":
            owner, role, domain_id = _mission_owner(e, revision["payload"])[0], None, None
        else:
            owner, role, domain_id, _assignment = _pco_responsibility(e, revision["payload"])
        if owner is None:
            fail("A candidate responsibility has no live accountable owner.", "INVALID_STATE")
        required[str(reference["object_id"])] = {"reference": reference, "owner": owner,
                                                 "role": role, "domain_id": domain_id}
    return required


def _candidate_basis_current(e, payload, *, lock=False):
    """The whole candidate set binds one exact Strategy/Architecture/LTCO basis.

    A later formal update makes the pending candidate stale: it may not be
    committed to or activated until an explicit reopen resolves it on the new
    basis.  Historical effective results are untouched by this check.  The
    check reads server-side so a cross-domain DRI/Owner is not required to hold
    a company-domain read grant; ``lock=True`` additionally serializes against
    a concurrent formal Strategy update in the write handler.
    """
    light = _LightExecution(e.conn, e.ctx)
    strategy_head, _ = light.ref(payload["strategy_ref"], types={"Strategy"})
    head_row = e.conn.execute(
        "SELECT object_id, revision_id FROM gov_method_strategy_heads WHERE scope_id=%s",
        (e.ctx.scope_id,)).fetchone()
    if (head_row is None or str(head_row["object_id"]) != str(payload["strategy_ref"]["object_id"])
            or str(head_row["revision_id"]) != str(payload["strategy_ref"]["revision_id"])):
        fail("The candidate's Strategy is no longer the current adopted basis.", "STALE_DEPENDENCY")
    if lock:
        e.conn.execute("SELECT object_id FROM gov_objects WHERE scope_id=%s AND object_id=%s FOR UPDATE",
                       (e.ctx.scope_id, payload["strategy_ref"]["object_id"]))
        fresh = e.conn.execute("SELECT latest_revision_id FROM gov_objects WHERE scope_id=%s AND object_id=%s",
                               (e.ctx.scope_id, payload["strategy_ref"]["object_id"])).fetchone()
        if str(fresh["latest_revision_id"]) != str(payload["strategy_ref"]["revision_id"]):
            fail("The adopted Strategy changed concurrently.", "STALE_DEPENDENCY")
    state_row = e.conn.execute("SELECT state FROM gov_method_state WHERE scope_id=%s AND object_id=%s",
                               (e.ctx.scope_id, payload["strategy_ref"]["object_id"])).fetchone()
    current_architecture = (state_row["state"] if state_row else {}).get("architecture_ref")
    if not _same(payload["architecture_ref"], current_architecture):
        fail("The candidate's adopted Architecture is no longer current.", "STALE_DEPENDENCY")
    light.ref(payload["architecture_ref"], types={"StrategicArchitecture"}, effective=True, current=True)
    for reference in payload.get("ltco_refs", []):
        light.ref(reference, types={"LTCO"}, effective=True, current=True)


def _collect_commit_candidate(e):
    if e.ctx.principal_type != "human":
        fail("Only the named DRI or Owner can commit a responsibility.", "FORBIDDEN")
    state = _phase(e.state(e.target), "pending")
    payload = e.target_revision["payload"]
    _candidate_basis_current(e, payload)
    reference = e.params["responsibility_ref"]
    if not any(_same(reference, ref) for ref in payload["target_refs"]):
        fail("The commitment must name one exact candidate responsibility.", "INVALID_REQUEST")
    for ref in payload["target_refs"]:
        e.ref(ref, types={"PCO", "Mission"}, current=True)
    owner = _commitment_owner(e.conn, e.ctx, reference)
    if owner != e.ctx.principal_id:
        fail("A responsibility commitment can only be published by its own DRI or Owner.", "FORBIDDEN")
    member = e.head(reference["object_id"])
    revision = e.revision(reference["object_id"], reference["revision_id"])
    if member["object_type"] == "Mission":
        expected_role, expected_domain = None, None
    else:
        _, expected_role, expected_domain, _assignment = _pco_responsibility(e, revision["payload"])
    assignment = None
    for row in db._assignments(e.conn, e.ctx):
        if row["principal_id"] != owner:
            continue
        if expected_role is not None and (row["role"] != expected_role or str(row["domain_id"]) != str(expected_domain)):
            continue
        try:
            assignment = e.validate_assignment(row["assignment_id"], owner, "human")
            break
        except GovernedError:
            continue
    if assignment is None:
        fail("The named DRI or Owner has no current assignment for this responsibility.", "FORBIDDEN")
    e._v04["commitment_owner"] = owner
    e._v04["commitment_assignment"] = assignment
    e._v04["candidate_state"] = state


def _collect_activate_candidates(e):
    _human_ceo(e)
    state = _phase(e.state(e.target), "pending")
    payload = e.target_revision["payload"]
    _candidate_basis_current(e, payload)
    required = _required_committers(e, payload)
    for reference in payload["target_refs"]:
        member = e.head(reference["object_id"])
        member_state = e.state(member)
        if member_state.get("phase") != "candidate" or not _same(member_state.get("candidate_ref"), _exact(e.target, e.target_revision)):
            fail("The candidate set is no longer the authoritative member state.", "STALE_DEPENDENCY")
    rows = e.conn.execute(
        """SELECT responsibility_object_id, responsibility_revision_id, principal_id, assignment_id
           FROM gov_method_commitments WHERE scope_id=%s AND candidate_revision_id=%s""",
        (e.ctx.scope_id, e.target_revision["revision_id"])).fetchall()
    committed = {str(r["responsibility_object_id"]): db.jsonable(r) for r in db.jsonable(rows)}
    for oid, spec in required.items():
        recorded = committed.get(oid)
        reference = spec["reference"]
        if recorded is None or str(recorded["principal_id"]) != str(spec["owner"]):
            fail("Every named DRI and Owner must commit the exact candidate set before activation.", "INVALID_STATE")
        if str(recorded["responsibility_revision_id"]) != str(reference["revision_id"]):
            fail("A recorded commitment names a different responsibility revision.", "INVALID_STATE")
        try:
            assignment = access.assignment(e.conn, e.ctx, str(recorded["assignment_id"]), spec["owner"], "human")
        except GovernedError:
            fail("A recorded commitment assignment is no longer current; explicit recommit is required.", "INVALID_STATE")
        if spec["role"] is not None and (assignment["role"] != spec["role"]
                                         or str(assignment["domain_id"]) != str(spec["domain_id"])):
            fail("A recorded commitment assignment no longer matches the named responsibility.", "INVALID_STATE")
        e.validate_assignment(str(recorded["assignment_id"]), spec["owner"], "human")
    critical = [d for d in payload.get("unresolved_differences", []) if d["critical"]]
    if critical:
        fail("A critical unresolved difference blocks activation.", "INVALID_STATE")
    window_head, window_revision = e.ref(payload["window_ref"], types={"ReviewWindow"}, current=False)
    if e.state(window_head).get("phase") != "resolved":
        fail("The reviewed window is no longer in its resolved state.", "INVALID_STATE")
    e._v04["activation"] = {"state": state, "payload": payload, "window_head": window_head,
                            "window_revision": window_revision, "required": required}


def activation_blockers(conn, ctx, obj):
    """Read-only blocker codes for m1b_activate_candidates, in priority order.

    Reuses the same basis / member / commitment validators as the write path so
    the workbench surfaces the real reason instead of inventing a second rule
    set.  It never mutates and never returns an allowed verdict on its own.
    """
    payload = (obj.get("latest_revision") or {}).get("payload") or {}
    candidate_revision_id = (obj.get("latest_revision") or {}).get("revision_id")
    light = _LightExecution(conn, ctx)
    blockers = []
    try:
        _candidate_basis_current(light, payload)
    except GovernedError:
        blockers.append("stale_basis")
    try:
        candidate_head = light.head(obj["object_id"])
        candidate_revision = light.revision(obj["object_id"], obj["latest_revision"]["revision_id"])
        for reference in payload["target_refs"]:
            member = light.head(reference["object_id"])
            state = light.state(member)
            if state.get("phase") != "candidate" or not _same(state.get("candidate_ref"),
                                                             _exact(candidate_head, candidate_revision)):
                blockers.append("member_state_changed")
                break
    except (GovernedError, KeyError):
        blockers.append("member_state_changed")
    if any(item.get("critical") for item in payload.get("unresolved_differences", [])):
        blockers.append("critical_difference")
    try:
        required = _required_committers(light, payload)
    except GovernedError:
        blockers.append("missing_commitment")
        return blockers
    rows = conn.execute(
        """SELECT responsibility_object_id, responsibility_revision_id, principal_id, assignment_id
           FROM gov_method_commitments WHERE scope_id=%s AND candidate_revision_id=%s""",
        (ctx.scope_id, candidate_revision_id)).fetchall()
    committed = {str(row["responsibility_object_id"]): db.jsonable(row) for row in db.jsonable(rows)}
    for oid, spec in required.items():
        recorded = committed.get(oid)
        if recorded is None or str(recorded["principal_id"]) != str(spec["owner"]):
            blockers.append("missing_commitment")
            continue
        if str(recorded["responsibility_revision_id"]) != str(spec["reference"]["revision_id"]):
            blockers.append("member_state_changed")
            continue
        try:
            assignment = access.assignment(conn, ctx, str(recorded["assignment_id"]), spec["owner"], "human")
        except GovernedError:
            blockers.append("commitment_assignment_revoked")
            continue
        if spec["role"] is not None and (assignment["role"] != spec["role"]
                                         or str(assignment["domain_id"]) != str(spec["domain_id"])):
            blockers.append("commitment_assignment_mismatch")
    return blockers


def _collect_reopen(e):
    _human_ceo(e)
    if e.kind == "m1b_reopen_candidates":
        of_window = e.ref(e.target_revision["payload"]["window_ref"], types={"ReviewWindow"}, current=False)[0]
    else:
        of_window = e.target
    window_revision = e.revision(of_window["object_id"], of_window["latest_revision_id"])
    window_state = e.state(of_window)
    allowed = {"open", "closed", "resolved"} if e.kind == "m1b_reopen_window" else {"resolved"}
    if window_state.get("phase") not in allowed:
        fail("This window cannot be reopened.")
    if e.kind == "m1b_reopen_candidates" and e.state(e.target).get("phase") != "pending":
        fail("Only a pending candidate set can be reopened.")
    payload = window_revision["payload"]
    e._v04["old_window_head"] = of_window
    e._v04["old_window_state"] = deepcopy(window_state)
    e._v04["old_window_payload"] = deepcopy(payload)
    if e.kind == "m1b_reopen_candidates":
        e._v04["candidate_state"] = deepcopy(e.state(e.target))
    if e.params.get("participants") is not None:
        _validate_people(e, e.params["participants"])
        e._v04["reopen_participants"] = e.params["participants"]
    else:
        # A reopened window must bind current appointments, never a stale
        # assignment id of the superseded roster version.
        refreshed = []
        for member in payload["participants"]:
            current = None
            rows = e.conn.execute(
                """SELECT assignment_id FROM gov_role_assignments
                   WHERE scope_id=%s AND principal_id=%s AND active
                   AND valid_from<=clock_timestamp() AND (valid_to IS NULL OR valid_to>clock_timestamp())""",
                (e.ctx.scope_id, member["principal_id"])).fetchall()
            for row in db.jsonable(rows):
                try:
                    current = e.validate_assignment(str(row["assignment_id"]), member["principal_id"], "human")
                    break
                except GovernedError:
                    continue
            if current is None:
                fail("A window member has no current appointment; change the roster explicitly.", "FORBIDDEN")
            refreshed.append({"principal_id": member["principal_id"],
                              "assignment_id": current["assignment_id"],
                              "personal_agent_id": member.get("personal_agent_id")})
        e._v04["reopen_participants"] = refreshed
    if e.params.get("rebase_strategy_ref"):
        e.current_strategy(e.params["rebase_strategy_ref"])
        e.ref(e.params["rebase_architecture_ref"], types={"StrategicArchitecture"}, effective=True, current=False)
        for reference in payload["ltco_refs"]:
            _, ltco = e.ref(reference, types={"LTCO"}, current=False)
            if not (_same(ltco["payload"]["strategy_ref"], e.params["rebase_strategy_ref"])
                    and _same(ltco["payload"]["architecture_ref"], e.params["rebase_architecture_ref"])):
                fail("Reopening can only rebase onto the basis its exact LTCOs already name.", "STALE_DEPENDENCY")


def _reopen_payload(e):
    old = e._v04["old_window_payload"]
    if e.kind == "m1b_reopen_candidates":
        # A pending candidate's exact target revisions are the membership being
        # re-frozen; the original window payload still names the pre-candidate
        # drafts that are no longer current.
        candidate = e.target_revision["payload"]
        pco_refs, mission_refs = [], []
        for reference in candidate["target_refs"]:
            head = e.head(reference["object_id"])
            (pco_refs if head["object_type"] == "PCO" else mission_refs).append(reference)
        old = {**old, "pco_refs": pco_refs, "mission_refs": mission_refs,
               "ltco_refs": candidate["ltco_refs"]}
    payload = {
        "title": e.params["title"],
        "period": old["period"],
        "strategy_ref": e.params.get("rebase_strategy_ref") or old["strategy_ref"],
        "architecture_ref": e.params.get("rebase_architecture_ref") or old["architecture_ref"],
        "ltco_refs": old["ltco_refs"],
        "pco_refs": e.params.get("pco_refs") or old["pco_refs"],
        "mission_refs": e.params.get("mission_refs") or old["mission_refs"],
        "participants": e.params.get("participants") or e._v04.get("reopen_participants") or old["participants"],
        "previous_window_ref": _exact(e._v04["old_window_head"], e.revision(
            e._v04["old_window_head"]["object_id"], e._v04["old_window_head"]["latest_revision_id"])),
    }
    return payload


def _collect_generate_review(e):
    _agent(e, "CO_AGENT")
    payload = e.params["payload"]
    if payload.get("fact_refs"):
        # BusinessFact is not a 0.4 object in this batch. Accepting unreadable,
        # fabricated or cross-version facts would launder an unsupported basis;
        # the analysis path remains canonical States plus evidence.
        fail("BusinessFact references are not enabled for tkos.method/0.4; "
             "cite canonical States and evidence instead.", "ACTION_NOT_SUPPORTED_FOR_PROTOCOL")
    subjects = []
    for reference in payload["state_refs"]:
        _, revision = _canonical_state(e, reference)
        subjects.append(revision["payload"]["subject_ref"])
    target_keys = sorted((str(r["object_id"]), str(r["revision_id"]), str(r["payload_hash"]))
                         for r in payload["target_refs"])
    subject_keys = sorted((str(s["object_id"]), str(s["revision_id"]), str(s["payload_hash"]))
                          for s in subjects)
    if target_keys != subject_keys:
        fail("Review must cite canonical States for exactly its target object revisions.", "INVALID_REQUEST")
    for reference in payload["target_refs"]:
        e.ref(reference, types={"PCO", "Mission"}, current=False)


def _canonical_state(e, reference):
    head, revision = e.ref(reference, types={"OperatingState"}, effective=True, current=False)
    if not _same(e.state(head).get("canonical_ref"), reference):
        fail("A PeriodReview must cite canonical States.", "STALE_DEPENDENCY")
    _state_subject_owner(e, revision["payload"]["subject_ref"])
    return head, revision


def _collect_propose_state(e):
    payload = e.params["payload"]
    head, revision = e.ref(payload["subject_ref"], types={"LTCO", "PCO", "Mission"}, effective=True, current=True)
    owner, _ = _state_subject_owner(e, payload["subject_ref"])
    if e.ctx.principal_type == "human":
        e.require_actor(owner, "human")
    else:
        _agent(e, "CO_AGENT") if "CO_AGENT" in {a["role"] for a in e.ctx.assignments} else _agent(e, "CEO_AGENT")
    limits = [payload["subject_ref"]]
    for key in ("architecture_ref", "strategy_ref", "parent_ltco_ref", "parent_pco_ref"):
        if revision["payload"].get(key):
            limits.append(revision["payload"][key])
    for reference in payload["baseline_refs"]:
        if not any(_same(reference, limit) for limit in limits):
            fail("State baselines must belong to this exact commitment and its direct basis.", "INVALID_REQUEST")
    if not any(_same(reference, payload["subject_ref"]) for reference in payload["baseline_refs"]):
        fail("State baseline must include the exact subject commitment.", "INVALID_REQUEST")
    for reference in [*payload["baseline_refs"]]:
        e.ref(reference, current=False)
    for reference in payload["evidence_refs"]:
        e.ref(reference, types={"EvidenceAsset", "BusinessFact", "PeriodReview", "OperatingState"}, current=False)
    previous = e.params.get("previous_state_ref")
    if previous:
        prev_head, prev = e.ref(previous, types={"OperatingState"})
        if prev["payload"]["subject_ref"] != payload["subject_ref"] or prev["payload"]["as_of"] != payload["as_of"]:
            fail("A new recommendation must preserve the State identity and as-of.", "VERSION_CONFLICT")


def _collect_confirm_state(e):
    _phase(e.state(e.target), "proposed")
    owner, _ = _state_subject_owner(e, e.target_revision["payload"]["subject_ref"])
    e.require_actor(owner, "human")
    e.validate_principal(owner, "human")
    if e.params.get("rag") and e.params["rag"] != "unknown" and not e.target_revision["payload"]["evidence_refs"]:
        fail("Missing evidence cannot be overridden into a known rating.", "INVALID_REQUEST")


def _state_key(payload):
    subject = payload["subject_ref"]
    return subject["object_id"], "", datetime.fromisoformat(payload["as_of"])


def _problem_key(payload, state_payload):
    subject = state_payload["subject_ref"]
    return subject["object_id"], "", sha256(" ".join(payload["core_question"].split()).encode()).hexdigest()


def _problem_check(e, payload):
    state_head, state_revision = _canonical_state(e, payload["state_ref"])
    subject_ref = state_revision["payload"]["subject_ref"]
    subject_head, subject_revision = e.ref(subject_ref, types={"LTCO", "PCO", "Mission"}, effective=True, current=False)
    assignment = e.validate_assignment(payload["responsible_assignment_id"], principal_type="human")
    level = payload["level"]
    if level == "mission":
        owner, _ = _mission_owner(e, subject_revision["payload"])
        if subject_head["object_type"] != "Mission" or assignment["principal_id"] != owner:
            fail("Mission problems belong to the exact Mission Owner.", "FORBIDDEN")
    elif level == "domain":
        if subject_head["object_type"] != "PCO":
            fail("Domain problems require a Scope PCO subject.", "FORBIDDEN")
        owner, _ = _pco_dri(e, subject_revision["payload"])
        if assignment["principal_id"] != owner or assignment["role"] != "DOMAIN_DRI":
            fail("Domain problems require the current Scope DRI.", "FORBIDDEN")
    else:
        if assignment["role"] != "CEO" or assignment["domain_id"] != e.domain_id:
            fail("Company and strategic problems require the domain CEO.", "FORBIDDEN")
    e.require_actor(assignment["principal_id"], "human")
    for reference in payload["evidence_refs"]:
        e.ref(reference, types={"EvidenceAsset", "BusinessFact", "PeriodReview"}, current=False)
    return assignment


def _collect_open_problem(e):
    _problem_check(e, e.params["payload"])
    subject_ref = e.params["payload"]["state_ref"]
    _, state_revision = _canonical_state(e, subject_ref)
    key = _problem_key(e.params["payload"], state_revision["payload"])
    row = e.conn.execute(
        "SELECT problem_id FROM gov_method_problem_keys WHERE scope_id=%s AND subject_id=%s AND outcome_id=%s AND question_hash=%s",
        (e.ctx.scope_id, *key)).fetchone()
    if row:
        fail("This question already has a Problem; explicitly revise it.", "VERSION_CONFLICT")


def _collect_revise_problem(e):
    _phase(e.state(e.target), "open")
    old = e.target_revision["payload"]
    prior = e.validate_assignment(old["responsible_assignment_id"], principal_type="human")
    e.require_actor(prior["principal_id"], "human")
    _, state_revision = _canonical_state(e, old["state_ref"])
    _problem_check(e, e.params["payload"])
    if _problem_key(old, state_revision) != _problem_key(e.params["payload"], state_revision):
        fail("Revising a Problem preserves its core question and subject.")


def _collect_close_problem(e):
    _phase(e.state(e.target), "open")
    old = e.target_revision["payload"]
    prior = e.validate_assignment(old["responsible_assignment_id"], principal_type="human")
    e.require_actor(prior["principal_id"], "human")
    for reference in e.params["evidence_refs"]:
        e.ref(reference, current=False)


# ----------------------------------------------------------- collect dispatch


def collect(e):
    e._v04 = {}
    kind = e.kind
    params = e.params
    if kind == "method_open_run" and e.ctx.principal_type == "agent":
        _ceo_agent_owner(e)
        if params["payload"]["method"] not in {"M1A", "M1A+M1B"}:
            fail("A CEO Agent may open only strategic intake runs.", "INVALID_REQUEST")
        return
    if kind == "method_attach_run" and e.ctx.principal_type == "agent":
        _ceo_agent_owner(e)
        row = e.conn.execute("SELECT owner_principal_id,phase FROM gov_method_runs WHERE scope_id=%s AND run_id=%s",
                             (e.ctx.scope_id, e.target["object_id"])).fetchone()
        if not row or str(row["owner_principal_id"]) != e.ctx.principal_id or row["phase"] != "running":
            fail("Only the intake run owner can attach roots.", "FORBIDDEN")
        e.ref(params["object_ref"], current=False)
        return
    if kind in {"method_open_run", "method_attach_run", "method_pause_run", "method_resume_run", "method_record_attempt"}:
        return e._collect_run()
    if kind == "m1a_create_issue":
        return _collect_create_issue(e)
    if kind == "m1a_reframe_issue":
        return _collect_reframe_issue(e)
    if kind == "m1a_associate_issue":
        return _collect_associate_issue(e)
    if kind == "m1a_set_participants":
        return _collect_set_participants(e)
    if kind == "m1a_draft_agreement":
        return _collect_draft_agreement(e)
    if kind == "m1a_revise_agreement":
        return _collect_revise_agreement(e)
    if kind == "m1a_confirm_agreement":
        return _collect_confirm_agreement(e)
    if kind == "m1a_propose_update":
        return _collect_propose_update(e)
    if kind == "m1a_review_update":
        return _collect_review_update(e)
    if kind == "m1a_confirm_update":
        return _collect_confirm_update(e)
    if kind == "m1a_transfer_problem":
        return _collect_transfer_problem(e)
    if kind == "m1b_propose_ltco":
        _agent(e, "CEO_AGENT")
        return _ltco_check(e, params["payload"])
    if kind == "m1b_revise_ltco":
        _agent(e, "CEO_AGENT")
        _phase(e.state(e.target), "draft")
        return _ltco_check(e, params["payload"])
    if kind == "m1b_confirm_ltco":
        _phase(e.state(e.target), "draft")
        _ltco_check(e, e.target_revision["payload"])
        owner, _ = _current_ceo(e, e.target["domain_id"])
        e.require_actor(owner, "human")
        return
    if kind == "m1b_draft_pco":
        _agent(e, "CO_AGENT")
        return _pco_check(e, params["payload"])
    if kind == "m1b_revise_pco":
        _agent(e, "CO_AGENT")
        _phase(e.state(e.target), "draft")
        return _pco_check(e, params["payload"])
    if kind == "m1b_draft_mission":
        _agent(e, "CO_AGENT")
        _mission_check(e, params["payload"])
        pco = e.head(params["payload"]["parent_pco_ref"]["object_id"])
        if e.state(pco).get("phase") != "draft" or e.state(pco).get("window_id"):
            fail("A Mission draft requires an unlocked draft PCO.")
        return
    if kind == "m1b_revise_mission":
        _agent(e, "CO_AGENT")
        _phase(e.state(e.target), "draft")
        _mission_check(e, params["payload"])
        pco = e.head(params["payload"]["parent_pco_ref"]["object_id"])
        if e.state(pco).get("phase") != "draft" or e.state(pco).get("window_id"):
            fail("A Mission draft requires an unlocked draft PCO.")
        return
    if kind == "m1b_open_window":
        return _collect_open_window(e)
    if kind in {"m1b_comment", "m1b_replace_comment", "m1b_withdraw_comment", "m1b_assist_review"}:
        return _collect_window_opinion(e)
    if kind == "m1b_close_window":
        return _collect_close_window(e)
    if kind == "m1b_resolve_window":
        return _collect_resolve_window(e)
    if kind == "m1b_commit_candidate":
        return _collect_commit_candidate(e)
    if kind == "m1b_activate_candidates":
        return _collect_activate_candidates(e)
    if kind in {"m1b_reopen_candidates", "m1b_reopen_window"}:
        return _collect_reopen(e)
    if kind in {"m1b_generate_review", "m1b_regenerate_review"}:
        _agent(e, "CO_AGENT")
        if kind == "m1b_regenerate_review":
            previous = e.target_revision["payload"]
            payload = params["payload"]
            if payload["review_id"] != previous["review_id"] or payload["period"] != previous["period"]:
                fail("Regeneration preserves review identity and period.", "INVALID_REQUEST")
            if payload["generation_version"] == previous["generation_version"]:
                fail("Regeneration must identify a new analysis generation.", "INVALID_REQUEST")
        return _collect_generate_review(e)
    if kind == "method_propose_state":
        return _collect_propose_state(e)
    if kind == "method_confirm_state":
        return _collect_confirm_state(e)
    if kind == "method_open_problem":
        return _collect_open_problem(e)
    if kind == "method_revise_problem":
        return _collect_revise_problem(e)
    if kind == "method_close_problem":
        return _collect_close_problem(e)
    fail("Unsupported Method 0.4 action.", "ACTION_NOT_SUPPORTED_FOR_PROTOCOL")


# ------------------------------------------------------------------ run side


def run(e):
    collect(e)
    kind = e.kind
    if kind in {"method_open_run", "method_attach_run", "method_pause_run", "method_resume_run", "method_record_attempt"}:
        return e._run_action()
    if kind == "m1a_create_issue":
        payload = {**e._v04["issue_payload"], "reframe_of_ref": None}
        head, revision = e.create("StrategicIssue", payload, status="active")
        e.set_state(head, {"phase": "issue_confirmed", "initiating_agent_id": e.ctx.principal_id,
                           "participants": [], "source_refs": payload["source_refs"],
                           "round": 1})
        record = e.review("agent_issue_initiation", _exact(head, revision),
                          {"source_refs": payload["source_refs"], "run_ids": sorted(e.run_ids)})
        return {**_exact(head, revision), "phase": "issue_confirmed", "review_record_id": record}
    if kind == "m1a_reframe_issue":
        state = deepcopy(e.state(e.target))
        payload = {**e._v04["issue_payload"], "reframe_of_ref": _exact(e.target, e.target_revision)}
        head, revision = e.revise(e.target, payload, status="active", effective=True)
        state.update(phase="issue_confirmed", agreement_ref=None, round=state.get("round", 1) + 1,
                     previous_frame_ref=_exact(e.target, e.target_revision))
        e.set_state(head, state)
        record = e.review("issue_reframe", _exact(head, revision),
                          {"previous_frame_ref": state["previous_frame_ref"], "source_refs": payload["source_refs"]})
        return {**_exact(head, revision), "phase": "issue_confirmed", "review_record_id": record}
    if kind == "m1a_associate_issue":
        state = deepcopy(e.state(e.target))
        sources = state.setdefault("source_refs", [])
        for reference in e.params["source_refs"]:
            if reference not in sources:
                sources.append(reference)
        e.set_state(e.target, state)
        e.transition(e.target)
        record = e.review("issue_association", _exact(e.target, e.target_revision),
                          {"source_refs": e.params["source_refs"]})
        return {**_exact(e.target, e.target_revision), "source_refs": sources, "review_record_id": record}
    if kind == "m1a_set_participants":
        state = deepcopy(e.state(e.target))
        state["participants"] = e.params["participants"]
        state["research_principal_ids"] = [p["principal_id"] for p in e.params["participants"] if p.get("research")]
        e.set_state(e.target, state)
        e.transition(e.target)
        record = e.review("issue_participants", _exact(e.target, e.target_revision),
                          {"participants": e.params["participants"]})
        return {**_exact(e.target, e.target_revision), "participants": state["participants"],
                "review_record_id": record}
    if kind == "m1a_draft_agreement":
        payload = e.params["payload"]
        head, revision = e.create("StrategicAgreement", payload)
        e.set_state(head, {"phase": "draft", "confirmed_principal_ids": [], "issue_ref": payload["issue_ref"]})
        return {**_exact(head, revision), "phase": "draft"}
    if kind == "m1a_revise_agreement":
        head, revision = e.revise(e.target, e.params["payload"])
        e.set_state(head, {"phase": "draft", "confirmed_principal_ids": [],
                           "issue_ref": e.params["payload"]["issue_ref"]})
        return {**_exact(head, revision), "phase": "draft"}
    if kind == "m1a_confirm_agreement":
        state = deepcopy(e.state(e.target))
        record = e.review("agreement_confirmation", _exact(e.target, e.target_revision),
                          {"statement": e.params["statement"], "principal_id": e.ctx.principal_id})
        confirms = _agreement_confirmations(e, e.target, e.target_revision)
        signers = e.target_revision["payload"]["participants"]
        if all(str(s["principal_id"]) in confirms for s in signers):
            _formalize_agreement(e, e.target, e.target_revision, state)
            return {**_exact(e.target, e.target_revision), "phase": "formal", "review_record_id": record,
                    "formal": True}
        state.update(phase="awaiting_confirmation", confirmed_principal_ids=sorted(confirms))
        e.set_state(e.target, state)
        e.transition(e.target)
        pending = [s["principal_id"] for s in signers if str(s["principal_id"]) not in confirms]
        return {**_exact(e.target, e.target_revision), "phase": "awaiting_confirmation",
                "review_record_id": record, "formal": False, "pending_principal_ids": pending}
    if kind == "m1a_propose_update":
        head, revision = e.create("StrategyUpdateProposal", e.params["payload"])
        e.set_state(head, {"phase": "draft"})
        return {**_exact(head, revision), "phase": "draft"}
    if kind == "m1a_review_update":
        record = e.review("strategy_update_impact_review", _exact(e.target, e.target_revision),
                          {"accepted": e.params["accepted"], "findings": e.params["findings"]})
        state = deepcopy(e.state(e.target))
        state.update(phase="reviewed" if e.params["accepted"] else "returned",
                     review={"accepted": e.params["accepted"], "record_id": record})
        e.set_state(e.target, state)
        e.transition(e.target)
        return {**_exact(e.target, e.target_revision),
                "phase": "reviewed" if e.params["accepted"] else "returned", "review_record_id": record}
    if kind == "m1a_confirm_update":
        return _confirm_update(e)
    if kind == "m1a_transfer_problem":
        return _transfer_problem(e)
    if kind == "m1b_propose_ltco":
        head, revision = e.create("LTCO", e.params["payload"])
        e.set_state(head, {"phase": "draft"})
        return {**_exact(head, revision), "phase": "draft"}
    if kind == "m1b_revise_ltco":
        head, revision = e.revise(e.target, e.params["payload"])
        e.set_state(head, {"phase": "draft"})
        result = {**_exact(head, revision), "phase": "draft"}
        result["review_record_id"] = e.review("ltco_revision_response", _exact(head, revision),
                                              {"response": e.params["response"]})
        return result
    if kind == "m1b_confirm_ltco":
        record = e.review("ltco_confirmation", _exact(e.target, e.target_revision),
                          {"statement": e.params["statement"]})
        state = deepcopy(e.state(e.target))
        state.update(phase="confirmed", confirmation_record_id=record)
        e.set_state(e.target, state)
        e.transition(e.target, status="confirmed", effective=True)
        return {**_exact(e.target, e.target_revision), "phase": "confirmed", "review_record_id": record}
    if kind == "m1b_draft_pco":
        head, revision = e.create("PCO", e.params["payload"])
        e.set_state(head, {"phase": "draft"})
        return {**_exact(head, revision), "phase": "draft"}
    if kind == "m1b_revise_pco":
        head, revision = e.revise(e.target, e.params["payload"])
        e.set_state(head, {"phase": "draft"})
        return {**_exact(head, revision), "phase": "draft"}
    if kind == "m1b_draft_mission":
        head, revision = e.create("Mission", e.params["payload"])
        e.set_state(head, {"phase": "draft"})
        return {**_exact(head, revision), "phase": "draft"}
    if kind == "m1b_revise_mission":
        head, revision = e.revise(e.target, e.params["payload"])
        e.set_state(head, {"phase": "draft"})
        return {**_exact(head, revision), "phase": "draft"}
    if kind == "m1b_open_window":
        head, revision = _create_window(e, e.params["payload"])
        return {**_exact(head, revision), "phase": "open"}
    if kind == "m1b_comment":
        return _run_comment(e)
    if kind == "m1b_replace_comment":
        return _run_replace(e)
    if kind == "m1b_withdraw_comment":
        state = deepcopy(e.state(e.target))
        active = state["active_opinions"][e.ctx.principal_id]
        active.remove(e.params["review_record_id"])
        e.set_state(e.target, state)
        e.transition(e.target)
        record = e.review("window_opinion_withdrawal", _exact(e.target, e.target_revision),
                          {"reason": e.params["reason"], "withdrawn_record_id": e.params["review_record_id"]},
                          window_id=e.target["object_id"])
        return {"window_id": e.target["object_id"], "review_record_id": record,
                "withdrawn_record_id": e.params["review_record_id"]}
    if kind == "m1b_assist_review":
        record = e.review("personal_agent_analysis", e.params["target_ref"],
                          {k: v for k, v in e.params.items() if k != "target_ref"}, window_id=e.target["object_id"])
        e.transition(e.target)
        return {"window_id": e.target["object_id"], "review_record_id": record, "nature": "agent_analysis"}
    if kind == "m1b_close_window":
        state = deepcopy(e.state(e.target))
        state["frozen_opinion_ids"] = _frozen_opinions(state)
        state["phase"] = "closed"
        record = e.review("window_closed", _exact(e.target, e.target_revision),
                          {"reason": e.params["reason"], "frozen_opinion_ids": state["frozen_opinion_ids"]},
                          window_id=e.target["object_id"])
        state["close_record_id"] = record
        e.set_state(e.target, state)
        e.transition(e.target)
        return {"window_id": e.target["object_id"], "phase": "closed",
                "frozen_opinion_ids": state["frozen_opinion_ids"], "review_record_id": record}
    if kind == "m1b_resolve_window":
        return _resolve_window(e)
    if kind == "m1b_commit_candidate":
        return _commit_candidate(e)
    if kind == "m1b_activate_candidates":
        return _activate_candidates(e)
    if kind in {"m1b_reopen_candidates", "m1b_reopen_window"}:
        return _reopen(e)
    if kind == "m1b_generate_review":
        head, revision = e.create("PeriodReview", e.params["payload"], status="recorded")
        e.set_state(head, {"phase": "generated"})
        return {**_exact(head, revision), "nature": "agent_analysis", "phase": "generated"}
    if kind == "m1b_regenerate_review":
        head, revision = e.revise(e.target, e.params["payload"], status="recorded", effective=True)
        e.set_state(head, {"phase": "generated"})
        return {**_exact(head, revision), "nature": "agent_analysis", "phase": "generated"}
    if kind == "method_propose_state":
        payload = e.params["payload"]
        previous = e.params.get("previous_state_ref")
        if previous:
            head, old = e.ref(previous, types={"OperatingState"})
            head, revision = e.revise(head, payload)
        else:
            head, revision = e.create("OperatingState", payload)
            e.conn.execute(
                "INSERT INTO gov_method_state_keys(scope_id,subject_id,outcome_id,as_of,state_id) VALUES(%s,%s,%s,%s,%s)",
                (e.ctx.scope_id, *_state_key(payload), head["object_id"]))
        state = deepcopy(e.state(head))
        state.update(phase="proposed", recommendation_ref=_exact(head, revision))
        e.set_state(head, state)
        return _exact(head, revision)
    if kind == "method_confirm_state":
        original = _exact(e.target, e.target_revision)
        head, revision = e.target, e.target_revision
        if e.params.get("rag"):
            head, revision = e.revise(head, {**revision["payload"], "rag": e.params["rag"],
                                             "summary": e.params["summary"]})
        e.transition(head, status="active", effective=True)
        reference = _exact(head, revision)
        state = deepcopy(e.state(head))
        state.update(phase="confirmed", canonical_ref=reference, confirmed_by=e.ctx.principal_id)
        e.set_state(head, state)
        record = e.review("state_confirmation", reference,
                          {**e.params, "recommendation_ref": original})
        return {**reference, "review_record_id": record}
    if kind == "method_open_problem":
        payload = e.params["payload"]
        head, revision = e.create("OperatingProblem", payload, status="active")
        e.conn.execute(
            "INSERT INTO gov_method_problem_keys(scope_id,subject_id,outcome_id,question_hash,problem_id) VALUES(%s,%s,%s,%s,%s)",
            (e.ctx.scope_id, *_problem_key(payload, _canonical_state(e, payload["state_ref"])[1]["payload"]),
             head["object_id"]))
        e.set_state(head, {"phase": "open", "tracking": True})
        return _exact(head, revision)
    if kind == "method_revise_problem":
        head, revision = e.revise(e.target, e.params["payload"])
        e.transition(head, status="active", effective=True)
        e.set_state(head, {"phase": "open", "tracking": True})
        return _exact(head, revision)
    if kind == "method_close_problem":
        state = {"phase": e.params["disposition"], "tracking": False, "reason": e.params["reason"]}
        e.set_state(e.target, state)
        e.transition(e.target)
        record = e.review("problem_closure", _exact(e.target, e.target_revision), e.params)
        return {**_exact(e.target, e.target_revision), "review_record_id": record}
    fail("Unsupported Method 0.4 action.", "ACTION_NOT_SUPPORTED_FOR_PROTOCOL")


def _create_window(e, payload, targets=None):
    head, revision = e.create("ReviewWindow", payload, status="open")
    e.set_state(head, {"phase": "open", "active_opinions": {}, "frozen_opinion_ids": []})
    if targets:
        for target in targets.values():
            member_head = target["head"]
            state = deepcopy(e.state(member_head))
            state.update(window_id=head["object_id"], phase="under_review")
            e.set_state(member_head, state)
            e.transition(member_head)
    else:
        for reference in [*payload["pco_refs"], *payload["mission_refs"]]:
            member = e.head(reference["object_id"])
            state = deepcopy(e.state(member))
            state.update(window_id=head["object_id"], phase="under_review")
            e.set_state(member, state)
            e.transition(member)
    return head, revision


def _run_comment(e):
    state = deepcopy(e.state(e.target))
    record = e.review("window_comment", e.params["target_ref"], {"content": e.params["content"]},
                      window_id=e.target["object_id"])
    active = state.setdefault("active_opinions", {}).setdefault(e.ctx.principal_id, [])
    active.append(record)
    e.set_state(e.target, state)
    e.transition(e.target)
    return {"window_id": e.target["object_id"], "review_record_id": record, "phase": "open"}


def _run_replace(e):
    state = deepcopy(e.state(e.target))
    active = state["active_opinions"][e.ctx.principal_id]
    active.remove(e.params["replaces_record_id"])
    record = e.review("window_comment", e.params["target_ref"],
                      {"content": e.params["content"], "replaces_record_id": e.params["replaces_record_id"]},
                      window_id=e.target["object_id"])
    active.append(record)
    e.set_state(e.target, state)
    e.transition(e.target)
    return {"window_id": e.target["object_id"], "review_record_id": record,
            "replaced_record_id": e.params["replaces_record_id"], "phase": "open"}


def _resolve_window(e):
    cached = e._m1b
    pcos = cached["pcos"]
    missions = cached["missions"]
    candidate_refs = []
    candidate_by_object = {}
    for candidate in e.params["pcos"]:
        frozen = pcos[str(candidate["object_id"])]
        head, revision = e.revise(frozen["head"], candidate["payload"])
        reference = _exact(head, revision)
        candidate_by_object[str(candidate["object_id"])] = reference
        candidate_refs.append(reference)
    e.checkpoint("after_method_candidate_write")
    for candidate in e.params["missions"]:
        frozen = missions[str(candidate["object_id"])]
        parent = str(frozen["revision"]["payload"]["parent_pco_ref"]["object_id"])
        payload = {**candidate, "parent_pco_ref": candidate_by_object[parent]}
        payload.pop("object_id", None)
        head, revision = e.revise(frozen["head"], payload)
        candidate_refs.append(_exact(head, revision))
    window_payload = e.target_revision["payload"]
    record = e.review("window_resolution", _exact(e.target, e.target_revision),
                      {"summary": e.params["summary"], "dispositions": e.params["dispositions"],
                       "unresolved_differences": e.params["unresolved_differences"],
                       "candidate_target_refs": candidate_refs},
                      window_id=e.target["object_id"])
    payload = {"title": e.params["title"], "window_ref": _exact(e.target, e.target_revision),
               "strategy_ref": window_payload["strategy_ref"], "architecture_ref": window_payload["architecture_ref"],
               "ltco_refs": window_payload["ltco_refs"], "target_refs": candidate_refs,
               "resolution_record_id": record, "dispositions": e.params["dispositions"],
               "unresolved_differences": e.params["unresolved_differences"], "notes": []}
    head, revision = e.create("CandidateSet", payload, status="proposed")
    candidate_ref = _exact(head, revision)
    state = deepcopy(cached["state"])
    state.update(phase="resolved", candidate_ref=candidate_ref)
    e.set_state(e.target, state)
    e.transition(e.target)
    for reference in candidate_refs:
        member = e.head(reference["object_id"])
        member_state = deepcopy(e.state(member))
        member_state.update(phase="candidate", candidate_ref=candidate_ref)
        e.set_state(member, member_state)
    e.set_state(head, {"phase": "pending"})
    return {**candidate_ref, "phase": "pending", "target_refs": candidate_refs,
            "resolution_record_id": record}


def _commit_candidate(e):
    payload = e.target_revision["payload"]
    _candidate_basis_current(e, payload, lock=True)
    responsibility = next((ref for ref in payload["target_refs"] if _same(ref, e.params["responsibility_ref"])), None)
    if responsibility is None:
        fail("The commitment must name one exact candidate responsibility.", "INVALID_REQUEST")
    owner = e._v04["commitment_owner"]
    existing = e.conn.execute(
        """SELECT commitment_id FROM gov_method_commitments
           WHERE scope_id=%s AND candidate_revision_id=%s AND responsibility_object_id=%s""",
        (e.ctx.scope_id, e.target_revision["revision_id"], responsibility["object_id"])).fetchone()
    if existing:
        fail("This responsibility is already committed for the exact candidate set.", "VERSION_CONFLICT")
    commitment = db.jsonable(e.conn.execute(
        """INSERT INTO gov_method_commitments
           (scope_id,candidate_object_id,candidate_revision_id,responsibility_object_id,
            responsibility_revision_id,principal_id,assignment_id,statement,action_id)
           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING commitment_id,recorded_at""",
        (e.ctx.scope_id, e.target["object_id"], e.target_revision["revision_id"],
         responsibility["object_id"], responsibility["revision_id"], owner,
         e._v04["commitment_assignment"]["assignment_id"], e.params["statement"], e.action_id)).fetchone())
    state = deepcopy(e._v04["candidate_state"])
    commitments = state.setdefault("commitments", {})
    commitments[str(responsibility["object_id"])] = {
        "commitment_id": commitment["commitment_id"], "principal_id": owner,
        "statement": e.params["statement"], "recorded_at": db.jsonable(commitment["recorded_at"]),
    }
    e.set_state(e.target, state)
    e.transition(e.target)
    return {**_exact(e.target, e.target_revision),
            "commitment_id": commitment["commitment_id"], "responsibility_ref": responsibility,
            "principal_id": owner}


def _activate_candidates(e):
    cached = e._v04["activation"]
    payload = cached["payload"]
    _candidate_basis_current(e, payload, lock=True)
    record = e.review("candidate_set_activation", _exact(e.target, e.target_revision),
                      {"statement": e.params["statement"], "notes": e.params["notes"],
                       "responsibilities": [spec["reference"] for spec in cached["required"].values()]},
                      window_id=cached["window_head"]["object_id"])
    for reference in payload["target_refs"]:
        member = e.head(reference["object_id"])
        state = deepcopy(e.state(member))
        state.update(phase="confirmed", candidate_ref=_exact(e.target, e.target_revision),
                     confirmation_record_id=record)
        state.pop("window_id", None)
        e.set_state(member, state)
        e.transition(member, status="confirmed", effective=True)
    state = deepcopy(cached["state"])
    state.update(phase="confirmed", confirmation_record_id=record)
    e.set_state(e.target, state)
    e.transition(e.target, status="confirmed", effective=True)
    window_state = deepcopy(e.state(cached["window_head"]))
    window_state.update(phase="confirmed", confirmation_record_id=record)
    e.set_state(cached["window_head"], window_state)
    e.transition(cached["window_head"])
    return {**_exact(e.target, e.target_revision), "phase": "confirmed", "review_record_id": record,
            "target_refs": payload["target_refs"], "execution_authority_created": False}


def _reopen(e):
    old_head = e._v04["old_window_head"]
    old_state = e._v04["old_window_state"]
    payload = _reopen_payload(e)
    if e.params.get("pco_refs"):
        targets = _validate_window_targets(e, payload, expected_phase=None, locked_window=old_head["object_id"])
        e._v04["targets"] = targets
    old_candidate = old_state.get("candidate_ref")
    head, revision = _create_window(e, payload, e._v04.get("targets"))
    if old_state.get("phase") == "open":
        old_state["frozen_opinion_ids"] = _frozen_opinions(old_state)
    old_state.update(phase="reopened", reopened_window_ref=_exact(head, revision))
    e.set_state(old_head, old_state)
    e.transition(old_head)
    if e.kind == "m1b_reopen_candidates":
        candidate_state = e._v04["candidate_state"]
        candidate_state.update(phase="reopened", reopened_window_ref=_exact(head, revision))
        e.set_state(e.target, candidate_state)
        e.transition(e.target)
    elif old_candidate:
        # Reopening a resolved window invalidates its pending candidate set too.
        candidate_head = e.head(old_candidate["object_id"])
        candidate_state = deepcopy(e.state(candidate_head))
        if candidate_state.get("phase") == "pending":
            candidate_state.update(phase="reopened", reopened_window_ref=_exact(head, revision))
            e.set_state(candidate_head, candidate_state)
            e.transition(candidate_head)
    e.review("ceo_reopen", _exact(old_head, e.revision(old_head["object_id"], old_head["latest_revision_id"])),
             {"reason": e.params["reason"], "new_window_ref": _exact(head, revision)}, window_id=old_head["object_id"])
    return {**_exact(head, revision), "phase": "open"}


def _confirm_update(e):
    proposal_head, proposal_revision = e.target, e.target_revision
    proposal = proposal_revision["payload"]
    change = proposal["change"]
    agreement_ref = proposal["agreement_ref"]
    proposal_ref = _exact(proposal_head, proposal_revision)
    issue_ref = proposal["issue_ref"]
    strategy_ref = change.get("strategy_target_ref")
    architecture_ref = change.get("architecture_target_ref")
    changed = []
    if change.get("strategy"):
        payload = {**change["strategy"], "source_agreement_ref": agreement_ref, "source_proposal_ref": proposal_ref}
        if strategy_ref:
            strategy_head, _ = e.current_strategy(strategy_ref)
            strategy_head, strategy_revision = e.revise(strategy_head, payload, status="active", effective=True)
        else:
            strategy_head, strategy_revision = e.create("Strategy", payload, status="active")
        strategy_ref = _exact(strategy_head, strategy_revision)
        changed.append(strategy_ref)
        e.checkpoint("after_method_pair_strategy_write")
        e.conn.execute(
            """INSERT INTO gov_method_strategy_heads(scope_id,domain_id,object_id,revision_id,action_id)
               VALUES(%s,%s,%s,%s,%s)
               ON CONFLICT(scope_id,domain_id) DO UPDATE SET object_id=EXCLUDED.object_id,
               revision_id=EXCLUDED.revision_id,action_id=EXCLUDED.action_id""",
            (e.ctx.scope_id, strategy_head["domain_id"], strategy_head["object_id"],
             strategy_revision["revision_id"], e.action_id))
    if change.get("architecture"):
        architecture_payload = {**change["architecture"], "strategy_ref": strategy_ref,
                                "source_agreement_ref": agreement_ref, "source_proposal_ref": proposal_ref}
        if architecture_ref:
            old_head, old_revision = e.ref(architecture_ref, types={"StrategicArchitecture"},
                                           effective=True, current=False)
            architecture_head, architecture_revision = e.revise(old_head, architecture_payload,
                                                                status="active", effective=True)
        else:
            architecture_head, architecture_revision = e.create("StrategicArchitecture", architecture_payload,
                                                                status="active")
        architecture_ref = _exact(architecture_head, architecture_revision)
        changed.append(architecture_ref)
        e.set_state(architecture_head, {"phase": "confirmed"})
        strategy_head = e.head(strategy_ref["object_id"])
        strategy_state = deepcopy(e.state(strategy_head))
        strategy_state["architecture_ref"] = architecture_ref
        e.set_state(strategy_head, strategy_state)
        e.transition(strategy_head)
    issue_state = deepcopy(e.state(e.head(issue_ref["object_id"])))
    issue_state.update(phase="completed", outcome="updated", strategy_ref=strategy_ref,
                       architecture_ref=architecture_ref, issue_ref=issue_ref,
                       change_refs={"changed": changed,
                                    "retained_strategy": change.get("strategy_target_ref") if not change.get("strategy") else None,
                                    "retained_architecture": change.get("architecture_target_ref") if not change.get("architecture") else None,
                                    "applicability_rationale": change["applicability_rationale"]},
                       proposal_ref=proposal_ref, agreement_ref=agreement_ref)
    issue_head = e.head(issue_ref["object_id"])
    e.set_state(issue_head, issue_state)
    e.transition(issue_head, status="closed")
    record = e.review("strategy_update_confirmation", proposal_ref,
                      {"statement": e.params["statement"], "changed_refs": changed,
                       "retained_strategy_ref": issue_state["change_refs"]["retained_strategy"],
                       "retained_architecture_ref": issue_state["change_refs"]["retained_architecture"],
                       "applicability_rationale": change["applicability_rationale"]})
    _insert_impacts(e, strategy_ref, architecture_ref, proposal)
    # A confirmed proposal is terminal: it must never be offered for another
    # CEO confirmation from the workbench projection.
    proposal_state = deepcopy(e.state(proposal_head))
    proposal_state.update(phase="confirmed", confirmation_record_id=record)
    e.set_state(proposal_head, proposal_state)
    e.transition(proposal_head, status="confirmed")
    return {**proposal_ref, "phase": "completed", "changed_refs": changed,
            "strategy_ref": strategy_ref, "architecture_ref": architecture_ref,
            "review_record_id": record}


def _insert_impacts(e, strategy_ref, architecture_ref, proposal):
    """Preserve historical targets; changed formal bases leave review notices."""
    rows = e.conn.execute(
        """SELECT o.object_id, r.revision_id, r.payload FROM gov_objects o
           JOIN gov_object_revisions r ON (r.scope_id,r.object_id,r.revision_id)=(o.scope_id,o.object_id,o.effective_revision_id)
           WHERE o.scope_id=%s AND o.object_type IN ('LTCO','PCO','Mission')""", (e.ctx.scope_id,)).fetchall()
    for row in db.jsonable(rows):
        old_strategy = row["payload"].get("strategy_ref")
        if old_strategy and old_strategy == proposal["change"].get("strategy_target_ref"):
            e.conn.execute(
                """INSERT INTO gov_method_impacts
                   (scope_id,strategy_object_id,strategy_revision_id,target_object_id,target_revision_id,old_strategy_ref,action_id)
                   VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                (e.ctx.scope_id, strategy_ref["object_id"], strategy_ref["revision_id"],
                 row["object_id"], row["revision_id"], Jsonb(old_strategy), e.action_id))


def _transfer_problem(e):
    issue_ref = _exact(e.target, e.target_revision)
    problem_head, problem_revision = e.ref(e.params["problem_ref"], types={"OperatingProblem"}, current=True)
    problem_ref = _exact(problem_head, problem_revision)
    record = e.review("problem_transfer", issue_ref,
                      {"problem_ref": problem_ref, "reason": e.params["reason"],
                       "evidence_refs": e.params["evidence_refs"]})
    issue_state = deepcopy(e.state(e.target))
    transferred = issue_state.setdefault("transferred_problems", [])
    if problem_ref not in transferred:
        transferred.append(problem_ref)
    sources = issue_state.setdefault("source_refs", [])
    for reference in [problem_ref, *e.params["evidence_refs"]]:
        if reference not in sources:
            sources.append(reference)
    issue_state["transfer_record_id"] = record
    e.set_state(e.target, issue_state)
    e.transition(e.target)
    e.checkpoint("after_method_problem_transfer_link")
    problem_state = deepcopy(e.state(problem_head))
    problem_state.update(phase="transferred", tracking=False, issue_ref=issue_ref,
                         transfer_reason=e.params["reason"], transfer_record_id=record)
    sources = problem_state.setdefault("source_refs", [])
    for reference in [issue_ref, *e.params["evidence_refs"]]:
        if reference not in sources:
            sources.append(reference)
    e.set_state(problem_head, problem_state)
    e.transition(problem_head)
    return {"issue_ref": issue_ref, "problem_ref": problem_ref, "transferred": True,
            "review_record_id": record}
