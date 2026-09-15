"""Method execution on the existing authorization/CAS/receipt transaction."""
from __future__ import annotations
from copy import deepcopy
from uuid import uuid4
from psycopg.types.json import Jsonb
from pydantic import ValidationError
from . import db, protocol, evidence, checkpoints, method_access as access
from .errors import GovernedError
from .service import ActionExecution


def fail(code="INVALID_STATE", message=""):
    raise GovernedError(code, message)


def exact_ref(head, revision):
    return {"object_id": head["object_id"], "revision_id": revision["revision_id"], "payload_hash": revision["payload_hash"]}


class MethodExecution(ActionExecution):
    @classmethod
    def handles_request(cls, conn, ctx, request):
        from .method_v03_models import ACTION_PARAMS
        from .method_v02_models import ACTION_PARAMS as OLD_PARAMS
        return request.action_type in ACTION_PARAMS or request.action_type in OLD_PARAMS

    def authorize(self):
        from .method_models import registry
        self.contract_version = self.request.contract_version
        self.action_params, self.action_targets, self.payload_models = registry(self.contract_version)
        self.params = self.action_params[self.kind].model_validate(self.params).model_dump(mode="json", exclude_none=True)
        self.required_assignments = set()
        self.method_assignment_specs = {}
        self.method_evidence = {}
        self.run_ids = set()
        self.method_scoped_domain = None
        self.protocol_context = self.contract_version
        if self.request.target:
            self.target = self.head(self.request.target.object_id)
            self.domain_id = self.target["domain_id"]
            self.target_revision = self.revision(self.target["object_id"], self.request.target.revision_id)
            protocol.gate_target_action(self.conn, self.ctx.scope_id, self.target["object_id"], self.kind, self.request.contract_version)
            if self.target["latest_revision_id"] != self.target_revision["revision_id"]:
                fail("STALE_DEPENDENCY", "The target content changed; read it again.")
            if self.kind in {"m1b_comment", "m1b_withdraw_comment", "m1b_assist_review"}:
                member = access.window_participant(self.conn, self.ctx, self.target_revision["payload"])
                self.validate_assignment(member["assignment_id"], self.ctx.principal_id, self.ctx.principal_type)
                self.action_assignments = db.authorize_domain(self.conn, self.ctx, member["domain_id"], self.kind)
            elif self.target["object_type"] == "StrategicIssue" and self.kind.startswith("m1a_"):
                try:
                    self.action_assignments = db.authorize_domain(self.conn, self.ctx, self.domain_id, self.kind)
                except GovernedError as exc:
                    if exc.code != "FORBIDDEN":
                        raise
                    member = access.research_participant(self.conn, self.ctx, self.state(self.target))
                    self.method_scoped_domain = member["domain_id"]
                    self.action_assignments = db.authorize_domain(self.conn, self.ctx, member["domain_id"], self.kind)
            else:
                try:
                    self.action_assignments = db.authorize_domain(self.conn, self.ctx, self.domain_id, self.kind)
                except GovernedError as exc:
                    if exc.code != "FORBIDDEN" or self.contract_version != "tkos.method/0.3" or self.kind not in {"method_confirm_state", "method_revise_problem", "method_close_problem", "method_revise_architecture"}:
                        raise
                    member = access.anchor_participant(self.conn, self.ctx, self.target)
                    self.method_scoped_domain = member['domain_id']
                    self.action_assignments = db.authorize_domain(self.conn,self.ctx,member['domain_id'],self.kind)
        else:
            self.domain_id = self.params["domain_id"]
            try:
                self.action_assignments = db.authorize_domain(self.conn, self.ctx, self.domain_id, self.kind)
            except GovernedError as exc:
                if exc.code != "FORBIDDEN" or self.contract_version != "tkos.method/0.3" or self.kind != "method_propose_state":
                    raise
                member = access.state_subject_assignment(self.conn,self.ctx,self.params['payload']['subject_ref'])
                self.method_scoped_domain = member['domain_id']
                self.action_assignments = db.authorize_domain(self.conn,self.ctx,member['domain_id'],self.kind)
            # Root creates are resolved by the domain's server-side policy.
            create_type = {
                "method_open_run": "MethodRun", "m1a_record_signal": "Signal",
                "method_propose_architecture": "StrategicArchitecture",
                "method_propose_state": "OperatingState", "method_open_problem": "OperatingProblem",
                "m1a_open_potential_issue": "PotentialIssue", "m1b_record_fact": "BusinessFact",
                "m1a_create_direct_issue": "StrategicIssue",
                "m1b_generate_review": "PeriodReview", "m1b_advise_ltco": "LTCOReviewAdvice",
                "m1b_propose_ltco": "LTCO", "m1b_draft_pco": "PCO",
                "m1b_draft_mission": "Mission", "m1b_open_window": "ReviewWindow",
            }.get(self.kind)
            if create_type is None:
                fail("ACTION_NOT_SUPPORTED_FOR_PROTOCOL")
            self.creation_fields = protocol.resolve_creation(self.conn, self.ctx.scope_id, self.domain_id,
                create_type, self.request.contract_version, action_type=self.kind)
        for row in self.action_assignments:
            self.validate_assignment(row["assignment_id"], self.ctx.principal_id, self.ctx.principal_type)
        if self.target:
            self._run_gate(self.target["object_id"], inherit=True)

        if self.request.run_ref is not None:
            run, _ = self.ref(self.request.run_ref.model_dump(mode="json"), types={"MethodRun"}, current=False)
            self._run_gate(run["object_id"], inherit=True)
            if len(self.run_ids) != 1:
                fail("INVALID_STATE", "A command cannot join different runs.")

    def head(self, object_id):
        object_id = str(object_id)
        if object_id not in self.heads:
            self.heads[object_id] = access.head(self.conn, self.ctx, object_id)
        return self.heads[object_id]

    def revision(self, object_id, revision_id):
        row = access.revision(self.conn, self.ctx, str(object_id), str(revision_id))
        self.revisions[row["revision_id"]] = row
        return row

    def ref(self, reference, types=None, effective=False, current=True):
        if hasattr(reference, "model_dump"):
            reference = reference.model_dump(mode="json")
        head = self.head(reference["object_id"])
        revision = self.revision(head["object_id"], reference["revision_id"])
        protocol.gate_dependency(self.conn, self.ctx.scope_id, head["object_id"], self.contract_version)
        if types is not None and head["object_type"] not in types:
            fail("INVALID_REQUEST", "Reference has the wrong business type.")
        if reference.get("payload_hash") != revision["payload_hash"]:
            fail("STALE_DEPENDENCY", "Reference content hash does not match the immutable revision.")
        if effective and head["effective_revision_id"] != revision["revision_id"]:
            fail("STALE_DEPENDENCY", "The referenced version is not effective.")
        if current and head["latest_revision_id"] != revision["revision_id"]:
            fail("STALE_DEPENDENCY", "The referenced content revision is no longer current.")
        self.add_dependency(head["object_id"])
        if current:
            self._run_gate(head["object_id"], inherit=False)
        if head["object_type"] == "EvidenceAsset":
            self.method_evidence[revision["revision_id"]] = (head, revision)
        return head, revision

    def add_dependency(self, object_id):
        head = self.head(object_id)
        protocol.gate_dependency(self.conn, self.ctx.scope_id, head["object_id"], self.contract_version)
        if not self.target or head["object_id"] != self.target["object_id"]:
            self.dependencies.add(head["object_id"])
        return head

    def _run_gate(self, object_id, *, inherit=False):
        rows = self.conn.execute("SELECT r.* FROM gov_method_run_members m JOIN gov_method_runs r ON (r.scope_id,r.run_id)=(m.scope_id,m.run_id) WHERE m.scope_id=%s AND m.object_id=%s",
                                 (self.ctx.scope_id, object_id)).fetchall()
        for row in db.jsonable(rows):
            if inherit:
                self.run_ids.add(row["run_id"])
            if row["phase"] == "paused" and self.kind not in {"method_resume_run", "method_record_attempt"}:
                fail("INVALID_STATE", "The associated run is paused.")

    def validate_assignment(self, assignment_id, principal_id=None, principal_type=None, domain_id=None):
        row = access.assignment(self.conn, self.ctx, str(assignment_id), principal_id, principal_type, domain_id)
        self.required_assignments.add(row["assignment_id"])
        self.method_assignment_specs[row["assignment_id"]] = (principal_id, principal_type, domain_id)
        return row

    def assignment(self, assignment_id, **kwargs):
        return self.validate_assignment(assignment_id, **kwargs)

    def validate_principal(self, principal_id, principal_type="human", role=None, domain_id=None):
        rows = self.conn.execute("SELECT assignment_id,role,domain_id FROM gov_role_assignments WHERE scope_id=%s AND principal_id=%s ORDER BY assignment_id",
                                 (self.ctx.scope_id, principal_id)).fetchall()
        for row in db.jsonable(rows):
            if (role is not None and row["role"] != role) or (domain_id is not None and row["domain_id"] != domain_id):
                continue
            try:
                return self.validate_assignment(row["assignment_id"], principal_id, principal_type, domain_id)
            except GovernedError:
                continue
        fail("FORBIDDEN", "A currently assigned principal of the required kind is necessary.")

    principal = validate_principal

    def require_actor(self, principal_id, principal_type="human"):
        if self.ctx.principal_id != principal_id or self.ctx.principal_type != principal_type:
            fail("FORBIDDEN", "This action must be published by the responsible principal.")

    def require_role(self, role, principal_type="human", principal_id=None):
        self.require_actor(principal_id or self.ctx.principal_id, principal_type)
        # Window participation rights were checked against the specific participant
        # assignment in authorize; they never become a company-wide role.
        domain = None if self.kind in {"m1b_comment", "m1b_withdraw_comment", "m1b_assist_review"} else (self.method_scoped_domain or self.domain_id)
        return self.validate_principal(self.ctx.principal_id, principal_type, role, domain)

    def check_personal_agent(self, agent_id, owner_id):
        row = access.personal_agent(self.conn, self.ctx, agent_id, owner_id)
        owner_role = "CEO" if row["role"] == "CEO_AGENT" else ("DOMAIN_DRI" if self.target and self.target["object_type"] == "StrategicIssue" else None)
        self.validate_principal(owner_id, "human", role=owner_role)
        return self.validate_assignment(row["assignment_id"], agent_id, "agent")

    def state(self, head):
        row = self.conn.execute("SELECT state FROM gov_method_state WHERE scope_id=%s AND object_id=%s",
                                 (self.ctx.scope_id, head["object_id"])).fetchone()
        return deepcopy(db.jsonable(row["state"])) if row else {}

    def set_state(self, head, value):
        self.conn.execute("""INSERT INTO gov_method_state(scope_id,object_id,state,updated_by_action_id) VALUES(%s,%s,%s,%s)
            ON CONFLICT(scope_id,object_id) DO UPDATE SET state=EXCLUDED.state,updated_by_action_id=EXCLUDED.updated_by_action_id""",
            (self.ctx.scope_id, head["object_id"], Jsonb(value), self.action_id))
        current = self.heads.get(head["object_id"], head)
        self.event(current, current, self.kind + ".state", {"method_state": value})

    @staticmethod
    def exact_ref(head, revision):
        return exact_ref(head, revision)

    def checked_payload(self, object_type, payload):
        try:
            return self.payload_models[object_type].model_validate(payload).model_dump(mode="json", exclude_none=True)
        except (KeyError, ValueError, TypeError) as exc:
            raise GovernedError("INVALID_REQUEST", "Payload does not satisfy this Method type.", status=422) from exc

    def create(self, object_type, payload, domain_id=None, status="draft"):
        domain_id = domain_id or self.domain_id
        scoped = self.method_scoped_domain and domain_id == self.domain_id and (
            (self.target and self.target["object_type"] == "StrategicIssue") or
            (self.contract_version == "tkos.method/0.3" and self.kind in {"method_propose_state", "method_confirm_state"}))
        if not scoped:
            db.authorize_domain(self.conn, self.ctx, domain_id, self.kind)
        payload = self.checked_payload(object_type, payload)
        if self.target and domain_id == self.domain_id:
            source = protocol.current_binding(self.conn, self.ctx.scope_id, self.target["object_id"])
            fields = {key: source[key] for key in ("protocol_id", "contract_version", "profile_id", "profile_revision", "profile_canonical_hash", "record_origin")}
            registry = protocol._check_registry(self.conn, self.ctx.scope_id, "tkos.method", self.contract_version)
            if not registry.can_create or not registry.can_write or object_type not in registry.object_types:
                fail("PROTOCOL_WRITE_DISABLED")
        else:
            fields = protocol.resolve_creation(self.conn, self.ctx.scope_id, domain_id, object_type, self.contract_version, action_type=self.kind)
        oid = str(uuid4())
        head = db.jsonable(self.conn.execute("INSERT INTO gov_objects(scope_id,object_id,domain_id,object_type,lifecycle_status) VALUES(%s,%s,%s,%s,%s) RETURNING *",
            (self.ctx.scope_id, oid, domain_id, object_type, status)).fetchone())
        revision = self.insert_revision(head, payload, version=1)
        effective_id = revision["revision_id"] if status in {"active", "confirmed", "recorded", "stored"} else None
        head = db.jsonable(self.conn.execute("UPDATE gov_objects SET latest_revision_id=%s,effective_revision_id=%s WHERE scope_id=%s AND object_id=%s RETURNING *",
            (revision["revision_id"], effective_id, self.ctx.scope_id, oid)).fetchone())
        protocol.insert_binding(self.conn, self.ctx.scope_id, oid, fields, registered_by=self.ctx.principal_id, receipt_id=self.action_id)
        self.heads[oid] = head
        self.changed[oid] = head
        self.event(head, None, self.kind)
        if len(self.run_ids) == 1:
            self._attach(next(iter(self.run_ids)), oid)
        checkpoints.checkpoint("method_after_object_write", {"action_type": self.kind, "object_id": oid})
        return head, revision

    def revise(self, head, payload, status=None, effective=False):
        head = self.heads.get(head["object_id"], head)
        if head["domain_id"] != self.domain_id:
            grants = db.authorize_domain(self.conn, self.ctx, head["domain_id"], self.kind)
            for grant in grants:
                self.validate_assignment(grant["assignment_id"], self.ctx.principal_id, self.ctx.principal_type)
        payload = self.checked_payload(head["object_type"], payload)
        revision = self.insert_revision(head, payload, version=head["object_version"] + 1)
        kwargs = {"latest": revision["revision_id"]}
        if effective:
            kwargs["effective"] = revision["revision_id"]
        head = self.bump(head, status=status, **kwargs)
        return head, revision

    def transition(self, head, status=None, effective=False):
        head = self.heads.get(head["object_id"], head)
        kwargs = {"effective": head["latest_revision_id"]} if effective else {}
        return self.bump(head, status=status, **kwargs)

    def review(self, kind, target_ref, content, window_id=None):
        record_id = str(uuid4())
        self.conn.execute("""INSERT INTO gov_method_reviews(record_id,scope_id,kind,target_object_id,target_revision_id,window_id,principal_id,content,action_id)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (record_id, self.ctx.scope_id, kind, target_ref["object_id"], target_ref["revision_id"], window_id, self.ctx.principal_id, Jsonb(content), self.action_id))
        return record_id

    def list_reviews(self, window_id):
        return db.jsonable(self.conn.execute("SELECT * FROM gov_method_reviews WHERE scope_id=%s AND window_id=%s ORDER BY recorded_at,record_id",
                                           (self.ctx.scope_id, window_id)).fetchall())

    def current_strategy(self, reference):
        head, revision = self.ref(reference, types={"Strategy"}, effective=True, current=False)
        row = self.conn.execute("SELECT object_id,revision_id FROM gov_method_strategy_heads WHERE scope_id=%s AND domain_id=%s",
                                (self.ctx.scope_id, head["domain_id"])).fetchone()
        if row is None or str(row["object_id"]) != head["object_id"] or str(row["revision_id"]) != revision["revision_id"]:
            fail("STALE_DEPENDENCY", "The effective Strategy changed; explicitly recheck the basis.")
        return head, revision

    def activate_strategy(self, target_ref, payload, source_agreement_ref, source_proposal_ref):
        payload = {**payload, "source_agreement_ref": source_agreement_ref, "source_proposal_ref": source_proposal_ref}
        if target_ref:
            head, _ = self.current_strategy(target_ref)
            head, revision = self.revise(head, payload, status="active", effective=True)
        else:
            existing = self.conn.execute("SELECT 1 FROM gov_method_strategy_heads WHERE scope_id=%s AND domain_id=%s",
                                         (self.ctx.scope_id, self.domain_id)).fetchone()
            if existing:
                fail("STALE_DEPENDENCY", "An effective Strategy already exists.")
            head, revision = self.create("Strategy", payload, status="active")
        self.conn.execute("""INSERT INTO gov_method_strategy_heads(scope_id,domain_id,object_id,revision_id,action_id) VALUES(%s,%s,%s,%s,%s)
            ON CONFLICT(scope_id,domain_id) DO UPDATE SET object_id=EXCLUDED.object_id,revision_id=EXCLUDED.revision_id,action_id=EXCLUDED.action_id""",
            (self.ctx.scope_id, head["domain_id"], head["object_id"], revision["revision_id"], self.action_id))
        if self.contract_version == "tkos.method/0.3":
            from .method_v03 import activate_architecture
            change = next(c for c in self._m1a['proposal']['changes'] if c['scope'] == 'company')
            previous = self.state(head).get('architecture_ref')
            activate_architecture(self, exact_ref(head, revision), change['architecture'], source_proposal_ref, previous)
        if target_ref:
            targets = self.conn.execute("""SELECT o.object_id,r.revision_id,r.payload FROM gov_objects o
                JOIN gov_object_revisions r ON (r.scope_id,r.object_id,r.revision_id)=(o.scope_id,o.object_id,o.effective_revision_id)
                LEFT JOIN gov_object_revisions p ON p.scope_id=r.scope_id AND p.object_id::text=r.payload->'pco_ref'->>'object_id'
                    AND p.revision_id::text=r.payload->'pco_ref'->>'revision_id'
                WHERE o.scope_id=%s AND o.object_type IN ('LTCO','PCO','Mission')
                AND COALESCE(r.payload->'strategy_ref',p.payload->'strategy_ref')->>'object_id'=%s
                AND COALESCE(r.payload->'strategy_ref',p.payload->'strategy_ref')->>'revision_id'=%s""",
                (self.ctx.scope_id, target_ref["object_id"], target_ref["revision_id"])).fetchall()
            for target in db.jsonable(targets):
                if not access.is_method_object(self.conn, self.ctx, target["object_id"]):
                    continue
                self.conn.execute("""INSERT INTO gov_method_impacts(scope_id,strategy_object_id,strategy_revision_id,target_object_id,target_revision_id,old_strategy_ref,action_id)
                    VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                    (self.ctx.scope_id, head["object_id"], revision["revision_id"], target["object_id"], target["revision_id"], Jsonb(target_ref), self.action_id))
        self.checkpoint("after_method_strategy_write")
        return head, revision

    def checkpoint(self, name):
        checkpoints.checkpoint(name, {"action_type": self.kind, "receipt_id": self.action_id})

    def collect_dependencies(self):
        if self.contract_version == "tkos.method/0.3":
            from . import method_v03
            method_v03.collect(self)
        elif self.contract_version == "tkos.method/0.2" and self.kind.startswith(("m1a_", "m1b_")):
            from . import method_v02
            method_v02.collect(self)
        elif self.kind.startswith("m1a_"):
            from . import method_m1a
            method_m1a.collect(self)
        elif self.kind.startswith("m1b_"):
            from . import method_m1b
            method_m1b.collect(self)
        else:
            self._collect_run()
        if self.request.step_key and not self.run_ids:
            fail("INVALID_REQUEST", "A step key needs an explicit or inherited target run.")

    def check_versions(self):
        supplied = {x.object_id: x.expected_version for x in self.request.expected_versions}
        if self.dependencies - supplied.keys():
            fail("DEPENDENCY_MISSING", "Prepare again to obtain every mutable dependency.")
        for oid in supplied:
            self.add_dependency(oid)
        expected = dict(supplied)
        if self.target:
            expected[self.target["object_id"]] = self.request.target.expected_version
        for oid, version in expected.items():
            head = self.head(oid)
            if head["object_version"] != version:
                fail("VERSION_CONFLICT", "An object changed after prepare.")

    def run_action(self):
        # External bytes verified after all auth/CAS checks and before any business write.
        for head, revision in self.method_evidence.values():
            evidence.fetch_payload(revision["payload"], scope_id=self.ctx.scope_id, domain_id=head["domain_id"])
        if self.contract_version == "tkos.method/0.3":
            from . import method_v03
            return method_v03.run(self)
        if self.contract_version == "tkos.method/0.2" and self.kind.startswith(("m1a_", "m1b_")):
            from . import method_v02
            return method_v02.run(self)
        if self.kind.startswith("m1a_"):
            from . import method_m1a
            return method_m1a.run(self)
        if self.kind.startswith("m1b_"):
            from . import method_m1b
            return method_m1b.run(self)
        return self._run_action()

    def _enqueue_effects(self, result, versions):
        # No dispatch authority is inferred from Strategy/Mission confirmation.
        result["contract_version"] = self.contract_version
        for run_id in sorted(self.run_ids):
            self.conn.execute("""INSERT INTO gov_method_run_attempts(scope_id,run_id,step_key,outcome,target_object_id,principal_id,note,action_id)
                VALUES(%s,%s,%s,'succeeded',%s,%s,%s,%s)""",
                (self.ctx.scope_id, run_id, self.request.step_key or self.kind, self.target["object_id"] if self.target else result.get("object_id"), self.ctx.principal_id, self.request.reason, self.action_id))
        result["run_ids"] = sorted(self.run_ids)

    def recheck_final_barrier(self):
        if self.contract_version in {"tkos.method/0.2", "tkos.method/0.3"}:
            from .method_v02 import deadline_check
            deadline_check(self)
        for aid, args in self.method_assignment_specs.items():
            access.assignment(self.conn, self.ctx, aid, *args)
        # Recheck exact policy-derived authority, including scoped window grants.
        if self.kind in {"m1b_comment", "m1b_withdraw_comment", "m1b_assist_review"}:
            row = access.window_participant(self.conn, self.ctx, self.target_revision["payload"])
            db.authorize_domain(self.conn, self.ctx, row["domain_id"], self.kind)
        else:
            db.authorize_domain(self.conn, self.ctx, self.method_scoped_domain or self.domain_id, self.kind)
        for oid in self.dependencies:
            access.head(self.conn, self.ctx, oid)

    def _collect_run(self):
        if self.kind == "method_open_run":
            if self.ctx.principal_type != "human":
                fail("FORBIDDEN")
            self.require_role("CEO")
            return
        row = self.conn.execute("SELECT * FROM gov_method_runs WHERE scope_id=%s AND run_id=%s", (self.ctx.scope_id, self.target["object_id"])).fetchone()
        if row is None:
            fail("INVALID_STATE")
        self._method_run = db.jsonable(row)
        if self.kind != "method_record_attempt":
            self.require_role("CEO")
        if self.kind == "method_attach_run":
            head, _ = self.ref(self.params["object_ref"], current=False)
            current = self.conn.execute("SELECT run_id FROM gov_method_run_members WHERE scope_id=%s AND object_id=%s", (self.ctx.scope_id, head["object_id"])).fetchone()
            if current and str(current["run_id"]) != self.target["object_id"]:
                fail("INVALID_STATE", "Object already belongs to another run.")
        elif self.kind == "method_pause_run" and row["phase"] != "running":
            fail("INVALID_STATE")
        elif self.kind == "method_resume_run" and row["phase"] != "paused":
            fail("INVALID_STATE")

    def _attach(self, run_id, oid):
        self.conn.execute("INSERT INTO gov_method_run_members(scope_id,run_id,object_id,action_id) VALUES(%s,%s,%s,%s) ON CONFLICT(scope_id,object_id) DO NOTHING",
                          (self.ctx.scope_id, run_id, oid, self.action_id))

    def _run_action(self):
        if self.kind == "method_open_run":
            head, rev = self.create("MethodRun", self.params["payload"], status="active")
            self.conn.execute("INSERT INTO gov_method_runs(scope_id,run_id,owner_principal_id,phase,action_id) VALUES(%s,%s,%s,'running',%s)", (self.ctx.scope_id, head["object_id"], self.ctx.principal_id, self.action_id))
            self._attach(head["object_id"], head["object_id"])
            self.run_ids.add(head["object_id"])
            return {**exact_ref(head, rev), "phase": "running"}
        run_id = self.target["object_id"]
        if self.kind == "method_attach_run":
            self._attach(run_id, self.params["object_ref"]["object_id"])
        elif self.kind in {"method_pause_run", "method_resume_run"}:
            phase = "paused" if self.kind == "method_pause_run" else "running"
            self.conn.execute("UPDATE gov_method_runs SET phase=%s,action_id=%s WHERE scope_id=%s AND run_id=%s", (phase, self.action_id, self.ctx.scope_id, run_id))
        else:
            self.conn.execute("""INSERT INTO gov_method_run_attempts(scope_id,run_id,step_key,outcome,target_object_id,principal_id,note,action_id)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s)""", (self.ctx.scope_id, run_id, self.params["step_key"], self.params["outcome"], run_id, self.ctx.principal_id, self.params["note"], self.action_id))
        head = self.transition(self.target)
        return {**exact_ref(head, self.target_revision), "run_id": run_id}
