"""Authoritative governed actions; the caller owns the fenced DB transaction.

No function here commits, connects to a different database, or mutates immutable
records. The scope authorization fence is acquired and reauthenticated by db.py
before this module is called. All successful state/event/receipt/outbox writes
therefore commit together, or roll back together on any exception.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any
from uuid import uuid4

from psycopg.types.json import Jsonb
from pydantic import ValidationError

from memory_service_runtime.repository import enqueue_task
from . import checkpoints, db, delivery
from .errors import GovernedError
from .models import ActionRequest, validated_payload


COMMITMENTS = {"BusinessCommitment", "ExecutionCommitment"}
HUMAN_ACTIONS = {"accept_commitment", "activate_commitment", "confirm_adjustment", "confirm_closure",
                 "route_feedback", "accept_feedback", "investigate_feedback", "confirm_decision",
                 "confirm_outcome", "record_acceptance", "request_feedback_acceptance", "reopen_feedback",
                 "revoke_assignment"} | delivery.ACTIONS
_UNCHANGED = object()


def _canonical(value: Any) -> str:
    return json.dumps(db.jsonable(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _fail(code: str, message: str = "", status: int | None = None) -> None:
    raise GovernedError(code, message, status=status)


def _receipt(row: dict[str, Any]) -> dict[str, Any]:
    return db.jsonable({
        "receipt_id": row["receipt_id"], "action_type": row["action_type"],
        "actor_id": row["principal_id"], "auth_epoch": row["auth_epoch"], "status": row["status"],
        "result": row["result"], "object_versions": row["object_versions"],
        "effect_task_ids": row["effect_task_ids"], "recorded_at": row["recorded_at"],
    })


class ActionExecution:
    def __init__(self, conn: Any, ctx: Any, request: ActionRequest) -> None:
        self.conn, self.ctx, self.request = conn, ctx, request
        self.kind = request.action_type
        self.params = request.params.model_dump(mode="json", exclude_none=True)
        self.action_id = str(uuid4())
        self.heads: dict[str, dict[str, Any]] = {}
        self.revisions: dict[str, dict[str, Any]] = {}
        self.dependencies: set[str] = set()
        self.changed: dict[str, dict[str, Any]] = {}
        self.action_assignments: list[dict[str, Any]] = []
        self.domain_id: str | None = None
        self.target: dict[str, Any] | None = None
        self.target_revision: dict[str, Any] | None = None
        self.payload: dict[str, Any] | None = None
        self.required_assignments: set[str] = set()
        self.effect_task_ids: list[str] = []

    def head(self, object_id: str) -> dict[str, Any]:
        object_id = str(object_id)
        if object_id not in self.heads:
            self.heads[object_id] = db.jsonable(db.object_row(self.conn, self.ctx, object_id))
        return self.heads[object_id]

    def revision(self, object_id: str, revision_id: str) -> dict[str, Any]:
        object_id, revision_id = str(object_id), str(revision_id)
        self.head(object_id)
        if revision_id not in self.revisions:
            self.revisions[revision_id] = db.jsonable(db.revision_row(self.conn, self.ctx, object_id, revision_id))
        row = self.revisions[revision_id]
        if row["object_id"] != object_id:
            _fail("NOT_FOUND")
        return row

    def revision_by_id(self, revision_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        row = self.conn.execute(
            "SELECT object_id FROM gov_object_revisions WHERE scope_id=%s AND revision_id=%s",
            (self.ctx.scope_id, revision_id),
        ).fetchone()
        if row is None:
            _fail("NOT_FOUND")
        obj = self.head(str(row["object_id"]))
        return obj, self.revision(obj["object_id"], revision_id)

    def add_dependency(self, object_id: str) -> dict[str, Any]:
        obj = self.head(object_id)
        if not self.request.target or object_id != self.request.target.object_id:
            self.dependencies.add(object_id)
        return obj

    def add_revision_dependency(self, revision_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        obj, revision = self.revision_by_id(revision_id)
        self.add_dependency(obj["object_id"])
        return obj, revision

    def same_domain(self, obj: dict[str, Any]) -> None:
        if obj["domain_id"] != self.domain_id:
            _fail("NOT_FOUND")

    def assignment(self, assignment_id: str) -> dict[str, Any]:
        row = self.conn.execute(
            """SELECT a.*, p.principal_type, p.active AS principal_active
                 FROM gov_role_assignments a JOIN gov_principals p
                   ON p.scope_id=a.scope_id AND p.principal_id=a.principal_id
                WHERE a.scope_id=%s AND a.assignment_id=%s
                  AND a.active AND p.active AND a.valid_from<=clock_timestamp()
                  AND (a.valid_to IS NULL OR clock_timestamp()<a.valid_to)""",
            (self.ctx.scope_id, assignment_id),
        ).fetchone()
        if row is None:
            _fail("FORBIDDEN", "A required assignment is not currently valid.")
        row = db.jsonable(row)
        if self.domain_id is not None and row["domain_id"] != self.domain_id:
            _fail("FORBIDDEN")
        return row

    def current_policy(self) -> dict[str, Any]:
        row = self.conn.execute(
            """SELECT * FROM gov_activation_policies WHERE scope_id=%s AND domain_id=%s
               ORDER BY policy_seq DESC, recorded_at DESC, policy_revision_id LIMIT 1""",
            (self.ctx.scope_id, self.domain_id),
        ).fetchone()
        if row is None:
            _fail("FORBIDDEN", "No current policy authorizes this domain.")
        return db.jsonable(row)

    def checked_payload(self, object_type: str, payload: Any) -> dict[str, Any]:
        try:
            return validated_payload(object_type, payload)
        except (ValidationError, ValueError, TypeError):
            _fail("INVALID_REQUEST", "Payload does not match the actual object type.", 422)

    def authorize(self) -> None:
        if self.request.target:
            self.target = self.head(self.request.target.object_id)
            self.target_revision = self.revision(self.target["object_id"], self.request.target.revision_id)
            self.domain_id = self.target["domain_id"]
        elif self.kind == "create_object":
            self.domain_id = self.params["domain_id"]
        else:
            row = self.conn.execute(
                "SELECT domain_id FROM gov_role_assignments WHERE scope_id=%s AND assignment_id=%s",
                (self.ctx.scope_id, self.params["assignment_id"]),
            ).fetchone()
            if row is None:
                _fail("NOT_FOUND")
            self.domain_id = str(row["domain_id"])
        allowed = db.authorize_domain(self.conn, self.ctx, self.domain_id, action_type=self.kind)
        self.action_assignments = [db.jsonable(row) for row in allowed]
        if not self.action_assignments:
            _fail("FORBIDDEN")
        if self.kind in HUMAN_ACTIONS and self.ctx.principal_type != "human":
            _fail("FORBIDDEN")
        if self.kind == "create_object":
            self.payload = self.checked_payload(self.params["object_type"], self.params["payload"])
            object_type = self.params["object_type"]
        elif self.kind == "propose_revision":
            object_type = self.target["object_type"]
            if object_type in {"WorkItem", "Deliverable"}:
                _fail("INVALID_STATE", "Frozen work and delivery content require their dedicated actions.")
            self.payload = self.checked_payload(object_type, self.params["payload"])
        else:
            object_type = self.target["object_type"] if self.target else None
        if self.ctx.principal_type == "agent" and object_type not in {"FeedbackThread", "MetricObservation"}:
            _fail("FORBIDDEN")
        if self.kind == "confirm_outcome" and not any(row["role"] == "CEO" for row in self.action_assignments):
            _fail("FORBIDDEN")
        # Record exactly the assignment used for this invocation, not every grant.
        self.required_assignments.add(sorted(self.action_assignments, key=lambda row: row["assignment_id"])[0]["assignment_id"])

    def _payload_dependencies(self, payload: dict[str, Any], object_type: str) -> None:
        if object_type == "WorkItem":
            delivery.work_dependencies(self, payload)
        for ref in payload.get("upstream_refs", []):
            obj = self.add_dependency(ref["object_id"])
            self.same_domain(obj)
            self.revision(ref["object_id"], ref["revision_id"])
        if object_type == "ManagementAdjustment":
            for field in ("feedback_revision_id", "decision_revision_id"):
                obj, _ = self.add_revision_dependency(payload[field])
                self.same_domain(obj)
            for change in payload["changes"]:
                obj = self.add_dependency(change["object_id"])
                self.same_domain(obj)
                self.revision(change["object_id"], change["from_revision_id"])
                self.revision(change["object_id"], change["to_revision_id"])

    def feedback_state(self) -> dict[str, Any]:
        if not self.target or self.target["object_type"] != "FeedbackThread":
            _fail("INVALID_STATE", "Action requires a feedback thread.")
        row = self.conn.execute(
            "SELECT * FROM gov_feedback_state WHERE scope_id=%s AND object_id=%s",
            (self.ctx.scope_id, self.target["object_id"]),
        ).fetchone()
        if row is None:
            _fail("INVALID_STATE", "Feedback processing state is absent.")
        state = db.jsonable(row)
        if state["cycle_id"] != self.target["processing_cycle_id"]:
            _fail("INVALID_STATE")
        return state

    def _resolution_dependencies(self) -> None:
        state = self.feedback_state()
        if state["resolution_decision_revision_id"]:
            self.add_revision_dependency(state["resolution_decision_revision_id"])
        if state["resolution_adjustment_object_id"]:
            adjustment = self.add_dependency(state["resolution_adjustment_object_id"])
            if adjustment["effective_revision_id"] is None:
                _fail("INVALID_STATE")
            revision = self.revision(adjustment["object_id"], adjustment["effective_revision_id"])
            for change in revision["payload"]["changes"]:
                self.add_dependency(change["object_id"])
                self.revision(change["object_id"], change["to_revision_id"])

    def _bundle_dependencies(self) -> None:
        changes = self.params["changes"]
        changed_ids = {change["object_id"] for change in changes}
        queue: list[tuple[str, str]] = []
        for change in changes:
            self.add_dependency(change["object_id"])
            self.revision(change["object_id"], change["from_revision_id"])
            queue.append((change["object_id"], change["to_revision_id"]))
        seen = set()
        while queue:
            object_id, revision_id = queue.pop()
            if (object_id, revision_id) in seen:
                continue
            seen.add((object_id, revision_id))
            obj = self.add_dependency(object_id)
            self.same_domain(obj)
            revision = self.revision(object_id, revision_id)
            for ref in revision["payload"].get("upstream_refs", []):
                queue.append((ref["object_id"], ref["revision_id"]))
        # Active reverse dependents are server-discovered, never client-declared.
        for dependent in self.active_commitments():
            if any(ref["object_id"] in changed_ids for ref in dependent["payload"].get("upstream_refs", [])):
                self.add_dependency(dependent["object_id"])

    def active_commitments(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """SELECT o.object_id, o.domain_id, o.effective_revision_id, r.payload
                 FROM gov_objects o JOIN gov_object_revisions r
                   ON r.scope_id=o.scope_id AND r.revision_id=o.effective_revision_id
                WHERE o.scope_id=%s AND o.domain_id=%s AND o.lifecycle_status='active'
                  AND o.object_type IN ('BusinessCommitment','ExecutionCommitment')""",
            (self.ctx.scope_id, self.domain_id),
        ).fetchall()
        return db.jsonable(rows)

    def collect_dependencies(self) -> None:
        if self.kind in delivery.ACTIONS:
            delivery.collect_dependencies(self)
        elif self.kind in {"create_object", "propose_revision"}:
            object_type = self.params["object_type"] if self.kind == "create_object" else self.target["object_type"]
            self._payload_dependencies(self.payload, object_type)
            if self.params.get("bundle_id"):
                self.add_dependency(self.params["bundle_id"])
        elif self.kind in {"accept_commitment", "activate_commitment"}:
            self._payload_dependencies(self.target_revision["payload"], self.target["object_type"])
            if self.target_revision.get("bundle_id"):
                self.add_dependency(self.target_revision["bundle_id"])
        elif self.kind in {"confirm_decision", "confirm_outcome"}:
            self._payload_dependencies(self.target_revision["payload"], self.target["object_type"])
        elif self.kind == "confirm_adjustment":
            self.add_revision_dependency(self.params["feedback_revision_id"])
            self.add_revision_dependency(self.params["decision_revision_id"])
            self._bundle_dependencies()
        elif self.kind in {"record_acceptance", "confirm_closure"}:
            decision_field = "decision_revision_id" if self.kind == "record_acceptance" else "resolution_decision_revision_id"
            evidence_field = "evidence_revision_ids" if self.kind == "record_acceptance" else "closure_evidence_revision_ids"
            self.add_revision_dependency(self.params[decision_field])
            for revision_id in self.params[evidence_field]:
                self.add_revision_dependency(revision_id)
            self._resolution_dependencies()
        elif self.kind == "request_feedback_acceptance":
            self.add_revision_dependency(self.params["decision_revision_id"])

    def check_versions(self) -> None:
        supplied = {item.object_id: item.expected_version for item in self.request.expected_versions}
        missing = self.dependencies - supplied.keys()
        if missing:
            _fail("DEPENDENCY_MISSING", "The request omits required mutable dependencies.")
        # Extra dependencies are permitted only when they are currently readable,
        # and they still participate in the concurrency check.
        for object_id in supplied:
            self.add_dependency(object_id)
        expected = dict(supplied)
        if self.request.target:
            expected[self.request.target.object_id] = self.request.target.expected_version
        for object_id in sorted(expected):
            obj = db.jsonable(db.object_row(self.conn, self.ctx, object_id, lock=True))
            self.heads[object_id] = obj
            if obj["object_version"] != expected[object_id]:
                _fail("VERSION_CONFLICT", "An object changed after the request was prepared.")
        if self.request.target:
            self.target = self.heads[self.request.target.object_id]
            if self.target["latest_revision_id"] != self.request.target.revision_id:
                _fail("STALE_DEPENDENCY", "Target must identify the current candidate revision.")

    def event(self, obj: dict[str, Any], before: dict[str, Any] | None, event_type: str,
              detail: dict[str, Any] | None = None) -> None:
        content = dict(detail or {})
        content.update(before_version=before["object_version"] if before else 0,
                       object_version=obj["object_version"], revision_id=obj["latest_revision_id"],
                       effective_revision_id=obj["effective_revision_id"],
                       processing_cycle_id=obj["processing_cycle_id"])
        self.conn.execute(
            """INSERT INTO gov_lifecycle_events
               (event_id,scope_id,object_id,event_type,from_status,to_status,action_id,principal_id,detail)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (str(uuid4()), self.ctx.scope_id, obj["object_id"], event_type,
             before["lifecycle_status"] if before else None, obj["lifecycle_status"],
             self.action_id, self.ctx.principal_id, Jsonb(content)),
        )

    def bump(self, obj: dict[str, Any], *, status: str | None = None, latest: Any = _UNCHANGED,
             effective: Any = _UNCHANGED, cycle: str | None = None,
             event_type: str | None = None, detail: dict[str, Any] | None = None) -> dict[str, Any]:
        before = dict(obj)
        row = self.conn.execute(
            """UPDATE gov_objects SET object_version=object_version+1, lifecycle_status=%s,
               latest_revision_id=%s,effective_revision_id=%s,processing_cycle_id=%s,updated_at=clock_timestamp()
               WHERE scope_id=%s AND object_id=%s AND object_version=%s RETURNING *""",
            (status or obj["lifecycle_status"], obj["latest_revision_id"] if latest is _UNCHANGED else latest,
             obj["effective_revision_id"] if effective is _UNCHANGED else effective,
             cycle or obj["processing_cycle_id"], self.ctx.scope_id, obj["object_id"], obj["object_version"]),
        ).fetchone()
        if row is None:
            _fail("VERSION_CONFLICT")
        updated = db.jsonable(row)
        self.heads[obj["object_id"]] = updated
        self.changed[obj["object_id"]] = updated
        if self.target and self.target["object_id"] == obj["object_id"]:
            self.target = updated
        self.event(updated, before, event_type or self.kind, detail)
        return updated

    def insert_revision(self, obj: dict[str, Any], payload: dict[str, Any], *, version: int,
                        bundle_id: str | None = None, valid_from: str | None = None) -> dict[str, Any]:
        payload_from = payload.get("valid_from")
        if valid_from and payload_from and valid_from != payload_from:
            _fail("INVALID_REQUEST", "Conflicting validity timestamps.", 422)
        valid_from = payload_from or valid_from
        row = self.conn.execute(
            """INSERT INTO gov_object_revisions
               (revision_id,scope_id,object_id,object_version,payload,payload_hash,bundle_id,recorded_by,action_id,valid_from,valid_to)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,COALESCE(%s::timestamptz,clock_timestamp()),%s)
               RETURNING *""",
            (str(uuid4()), self.ctx.scope_id, obj["object_id"], version, Jsonb(payload), _hash(payload),
             bundle_id, self.ctx.principal_id, self.action_id, valid_from, payload.get("valid_to")),
        ).fetchone()
        revision = db.jsonable(row)
        self.revisions[revision["revision_id"]] = revision
        return revision

    def check_reference(self, ref: dict[str, str], *, post: dict[str, str] | None = None,
                        bundle_id: str | None = None) -> dict[str, Any]:
        obj = self.head(ref["object_id"])
        self.same_domain(obj)
        revision = self.revision(ref["object_id"], ref["revision_id"])
        if post and ref["object_id"] in post:
            if ref["revision_id"] != post[ref["object_id"]]:
                _fail("STALE_DEPENDENCY", "Candidate does not reference the post-adjustment revision.")
        elif obj["effective_revision_id"] != ref["revision_id"]:
            if not (bundle_id and revision.get("bundle_id") == bundle_id
                    and obj["latest_revision_id"] == ref["revision_id"]):
                _fail("STALE_DEPENDENCY", "Upstream reference is not currently effective.")
        return obj

    def commitment_parties(self, obj: dict[str, Any], revision: dict[str, Any]) -> list[dict[str, Any]]:
        if obj["object_type"] not in COMMITMENTS:
            _fail("INVALID_STATE", "Action requires a commitment.")
        payload = self.checked_payload(obj["object_type"], revision["payload"])
        parties = [self.assignment(assignment_id) for assignment_id in payload["required_assignment_ids"]]
        if any(row["principal_type"] != "human" for row in parties) or len({row["principal_id"] for row in parties}) != 2:
            _fail("FORBIDDEN", "Commitment parties must be two different humans.")
        policy = self.current_policy()
        expected_roles = policy["content"].get("commitment_party_roles", {}).get(obj["object_type"])
        if not expected_roles or sorted(row["role"] for row in parties) != sorted(expected_roles):
            _fail("FORBIDDEN", "Required parties do not match the current commitment policy.")
        return parties

    def validate_commitment(self, obj: dict[str, Any], revision: dict[str, Any], *,
                            post: dict[str, str] | None = None, allow_bundle: bool = False) -> list[dict[str, Any]]:
        parties = self.commitment_parties(obj, revision)
        refs = revision["payload"]["upstream_refs"]
        if len(refs) != 1:
            _fail("INVALID_REQUEST", "The pilot requires exactly one direct commitment upstream.", 422)
        upstream = self.check_reference(refs[0], post=post,
                                        bundle_id=revision.get("bundle_id") if allow_bundle else None)
        expected = "CompanyOutcome" if obj["object_type"] == "BusinessCommitment" else "BusinessCommitment"
        if upstream["object_type"] != expected or upstream["object_id"] == obj["object_id"]:
            _fail("INVALID_REQUEST", "Commitment upstream has the wrong type.", 422)
        return parties

    def signed_commitment(self, obj: dict[str, Any], revision: dict[str, Any], *,
                          selected_ids: list[str] | None = None, post: dict[str, str] | None = None) -> list[dict[str, Any]]:
        parties = self.validate_commitment(obj, revision, post=post)
        rows = self.conn.execute(
            "SELECT * FROM gov_handshakes WHERE scope_id=%s AND object_id=%s AND revision_id=%s",
            (self.ctx.scope_id, obj["object_id"], revision["revision_id"]),
        ).fetchall()
        signatures = db.jsonable(rows)
        if selected_ids is not None and set(selected_ids) != {row["handshake_id"] for row in signatures}:
            _fail("INVALID_STATE", "Activation must provide the exact required handshake records.")
        by_assignment = {row["assignment_id"]: row for row in signatures}
        if set(by_assignment) != {row["assignment_id"] for row in parties}:
            _fail("INVALID_STATE", "Both required same-revision signatures are missing.")
        for party in parties:
            signature = by_assignment[party["assignment_id"]]
            if signature["principal_id"] != party["principal_id"] or signature["terms_hash"] != revision["payload_hash"]:
                _fail("INVALID_STATE", "Signature does not bind the required person and terms.")
            self.required_assignments.add(party["assignment_id"])
        return signatures

    def confirmed_decision(self, revision_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        obj, revision = self.revision_by_id(revision_id)
        self.same_domain(obj)
        if obj["object_type"] != "Decision" or obj["effective_revision_id"] != revision_id or obj["lifecycle_status"] != "confirmed":
            _fail("INVALID_STATE", "Resolution requires a currently effective human-confirmed decision.")
        return obj, revision

    def validate_proposed_payload(self, obj: dict[str, Any], payload: dict[str, Any], bundle_id: str | None) -> None:
        if obj["object_type"] == "WorkItem":
            delivery.validate_work(self, payload, creating=True)
        if obj["object_type"] in COMMITMENTS:
            candidate = {"payload": payload, "bundle_id": bundle_id}
            self.validate_commitment(obj, candidate, allow_bundle=True)
        else:
            for ref in payload.get("upstream_refs", []):
                self.check_reference(ref)
        if bundle_id:
            bundle = self.head(bundle_id)
            self.same_domain(bundle)
            if obj["object_type"] not in COMMITMENTS or bundle["object_type"] != "ManagementAdjustment" or bundle["lifecycle_status"] != "proposed":
                _fail("INVALID_STATE", "Only a proposed adjustment can bind commitment candidates.")
        if obj["object_type"] == "ManagementAdjustment":
            feedback, _ = self.revision_by_id(payload["feedback_revision_id"])
            self.same_domain(feedback)
            if feedback["object_type"] != "FeedbackThread" or feedback["latest_revision_id"] != payload["feedback_revision_id"] or feedback["lifecycle_status"] != "investigating":
                _fail("INVALID_STATE", "Adjustment requires the current investigating feedback revision.")
            self.confirmed_decision(payload["decision_revision_id"])

    def create_object(self) -> dict[str, Any]:
        object_type = self.params["object_type"]
        object_id = str(uuid4())
        provisional = {"object_id": object_id, "object_type": object_type, "domain_id": self.domain_id}
        self.validate_proposed_payload(provisional, self.payload, None)
        status = {"CompanyOutcome": "proposed", "Decision": "proposed", "ManagementAdjustment": "proposed",
                  "BusinessCommitment": "offered", "ExecutionCommitment": "offered",
                  "FeedbackThread": "open", "MetricObservation": "recorded", "WorkItem": "offered"}[object_type]
        obj = db.jsonable(self.conn.execute(
            """INSERT INTO gov_objects(object_id,scope_id,domain_id,object_type,lifecycle_status)
               VALUES (%s,%s,%s,%s,%s) RETURNING *""",
            (object_id, self.ctx.scope_id, self.domain_id, object_type, status),
        ).fetchone())
        revision = self.insert_revision(obj, self.payload, version=1, valid_from=self.params.get("valid_from"))
        effective = revision["revision_id"] if object_type in {"MetricObservation", "FeedbackThread"} else None
        obj = db.jsonable(self.conn.execute(
            "UPDATE gov_objects SET latest_revision_id=%s,effective_revision_id=%s WHERE scope_id=%s AND object_id=%s RETURNING *",
            (revision["revision_id"], effective, self.ctx.scope_id, object_id),
        ).fetchone())
        if object_type == "FeedbackThread":
            self.conn.execute(
                "INSERT INTO gov_feedback_state(object_id,scope_id,cycle_id) VALUES (%s,%s,%s)",
                (object_id, self.ctx.scope_id, obj["processing_cycle_id"]),
            )
        if object_type == "WorkItem":
            delivery.initialize_work(self, obj, revision)
        self.heads[object_id] = self.changed[object_id] = obj
        self.event(obj, None, "create_object")
        return {"object_id": object_id, "revision_id": revision["revision_id"]}

    def propose_revision(self) -> dict[str, Any]:
        obj = self.target
        if obj["object_type"] in {"WorkItem", "Deliverable"}:
            _fail("INVALID_STATE", "Work baselines are frozen; delivery versions require submit_deliverable.")
        if obj["object_type"] == "ManagementAdjustment" and obj["lifecycle_status"] != "proposed":
            _fail("INVALID_STATE", "Applied adjustments cannot be rewritten.")
        if obj["object_type"] == "FeedbackThread" and obj["lifecycle_status"] not in {"open", "routed", "accepted", "investigating"}:
            _fail("INVALID_STATE", "Feedback under acceptance or already closed cannot be rewritten.")
        bundle_id = self.params.get("bundle_id")
        self.validate_proposed_payload(obj, self.payload, bundle_id)
        revision = self.insert_revision(obj, self.payload, version=obj["object_version"] + 1, bundle_id=bundle_id)
        effective = revision["revision_id"] if obj["object_type"] in {"MetricObservation", "FeedbackThread"} else _UNCHANGED
        self.bump(obj, latest=revision["revision_id"], effective=effective,
                  detail={"supersedes_content_revision_id": obj["latest_revision_id"], "bundle_id": bundle_id})
        return {"object_id": obj["object_id"], "revision_id": revision["revision_id"],
                "effective_revision_id": self.target["effective_revision_id"]}

    def confirm_content(self, object_type: str) -> dict[str, Any]:
        obj = self.target
        if obj["object_type"] != object_type or obj["effective_revision_id"] == self.target_revision["revision_id"]:
            _fail("INVALID_STATE")
        for ref in self.target_revision["payload"].get("upstream_refs", []):
            self.check_reference(ref)
        updated = self.bump(obj, status="confirmed", effective=self.target_revision["revision_id"])
        return {"object_id": obj["object_id"], "revision_id": updated["effective_revision_id"]}

    def accept_commitment(self) -> dict[str, Any]:
        obj, revision = self.target, self.target_revision
        if obj["lifecycle_status"] not in {"offered", "active"}:
            _fail("INVALID_STATE")
        parties = self.validate_commitment(obj, revision, allow_bundle=True)
        party = next((item for item in parties if item["assignment_id"] == self.params["party_assignment_id"]), None)
        if party is None or party["principal_id"] != self.ctx.principal_id:
            _fail("FORBIDDEN", "A caller can accept only its own required party slot.")
        if revision["payload_hash"] != self.params["accepted_terms_hash"]:
            _fail("INVALID_STATE", "Accepted terms hash differs from the immutable revision.")
        self.required_assignments.add(party["assignment_id"])
        existing = self.conn.execute(
            "SELECT * FROM gov_handshakes WHERE scope_id=%s AND object_id=%s AND revision_id=%s AND assignment_id=%s",
            (self.ctx.scope_id, obj["object_id"], revision["revision_id"], party["assignment_id"]),
        ).fetchone()
        if existing is not None:
            return {"object_id": obj["object_id"], "revision_id": revision["revision_id"],
                    "handshake_id": str(existing["handshake_id"]), "already_accepted": True}
        if obj["effective_revision_id"] == revision["revision_id"]:
            _fail("INVALID_STATE", "An effective revision cannot acquire retrospective signatures.")
        handshake_id = str(uuid4())
        self.conn.execute(
            """INSERT INTO gov_handshakes
               (handshake_id,scope_id,object_id,revision_id,assignment_id,principal_id,terms_hash,understanding,action_id)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (handshake_id, self.ctx.scope_id, obj["object_id"], revision["revision_id"], party["assignment_id"],
             self.ctx.principal_id, revision["payload_hash"], self.params["understanding"], self.action_id),
        )
        self.bump(obj, detail={"handshake_id": handshake_id, "accepted_revision_id": revision["revision_id"]})
        return {"object_id": obj["object_id"], "revision_id": revision["revision_id"], "handshake_id": handshake_id}

    def activate_commitment(self) -> dict[str, Any]:
        obj, revision = self.target, self.target_revision
        if revision.get("bundle_id"):
            _fail("INVALID_STATE", "A bundle-bound revision must activate with the full adjustment.")
        if obj["lifecycle_status"] != "offered" or obj["effective_revision_id"] is not None:
            _fail("INVALID_STATE", "Changes to existing effective commitments require an adjustment.")
        policy = self.current_policy()
        if policy["policy_revision_id"] != self.params["activation_policy_revision_id"]:
            _fail("STALE_DEPENDENCY", "Activation policy has changed.")
        self.signed_commitment(obj, revision, selected_ids=self.params["handshake_record_ids"])
        updated = self.bump(obj, status="active", effective=revision["revision_id"],
                            detail={"policy_revision_id": policy["policy_revision_id"]})
        return {"object_id": obj["object_id"], "revision_id": updated["effective_revision_id"]}

    def _check_post_bundle_graph(self, changes: list[dict[str, str]]) -> None:
        post = {item["object_id"]: item["to_revision_id"] for item in changes}
        visiting: set[tuple[str, str]] = set()
        visited: set[tuple[str, str]] = set()

        def visit(object_id: str, revision_id: str) -> None:
            node = (object_id, revision_id)
            if node in visiting:
                _fail("INVALID_STATE", "Upstream dependencies contain a cycle.")
            if node in visited:
                return
            visiting.add(node)
            revision = self.revision(object_id, revision_id)
            for ref in revision["payload"].get("upstream_refs", []):
                self.check_reference(ref, post=post)
                visit(ref["object_id"], ref["revision_id"])
            visiting.remove(node)
            visited.add(node)

        for item in changes:
            visit(item["object_id"], item["to_revision_id"])
        for dependent in self.active_commitments():
            if dependent["object_id"] not in post and any(ref["object_id"] in post for ref in dependent["payload"].get("upstream_refs", [])):
                _fail("STALE_DEPENDENCY", "An active reverse dependent was omitted from the adjustment.")

    def confirm_adjustment(self) -> dict[str, Any]:
        adjustment, revision = self.target, self.target_revision
        if adjustment["object_type"] != "ManagementAdjustment" or adjustment["lifecycle_status"] != "proposed":
            _fail("INVALID_STATE")
        payload = revision["payload"]
        sort_changes = lambda values: sorted(values, key=lambda item: item["object_id"])
        if not payload["changes"] or sort_changes(payload["changes"]) != sort_changes(self.params["changes"]):
            _fail("INVALID_STATE", "The command must apply the exact frozen adjustment change set.")
        if any(payload[key] != self.params[key] for key in ("feedback_revision_id", "decision_revision_id")):
            _fail("INVALID_STATE", "Resolution scope differs from the adjustment revision.")
        self.confirmed_decision(payload["decision_revision_id"])
        feedback, _ = self.revision_by_id(payload["feedback_revision_id"])
        self.same_domain(feedback)
        if feedback["object_type"] != "FeedbackThread" or feedback["latest_revision_id"] != payload["feedback_revision_id"] or feedback["lifecycle_status"] != "investigating":
            _fail("INVALID_STATE")
        changes = payload["changes"]
        post = {item["object_id"]: item["to_revision_id"] for item in changes}
        for change in changes:
            obj = self.head(change["object_id"])
            self.same_domain(obj)
            db.authorize_domain(self.conn, self.ctx, obj["domain_id"], action_type="confirm_adjustment")
            candidate = self.revision(obj["object_id"], change["to_revision_id"])
            if obj["object_type"] not in COMMITMENTS or obj["lifecycle_status"] != "active" or obj["effective_revision_id"] != change["from_revision_id"]:
                _fail("STALE_DEPENDENCY", "An adjustment base is no longer effective.")
            if obj["latest_revision_id"] != change["to_revision_id"] or candidate.get("bundle_id") != adjustment["object_id"]:
                _fail("INVALID_STATE", "Every candidate must belong to this adjustment.")
            self.signed_commitment(obj, candidate, post=post)
        self._check_post_bundle_graph(changes)
        for index, change in enumerate(changes):
            self.bump(self.head(change["object_id"]), status="active", effective=change["to_revision_id"],
                      detail={"adjustment_object_id": adjustment["object_id"], "from_revision_id": change["from_revision_id"]})
            if index == 0:
                checkpoints.checkpoint("after_first_bundle_update", {"action_type": self.kind, "object_id": change["object_id"],
                                                                    "receipt_id": self.action_id})
        self.bump(adjustment, status="applied", effective=revision["revision_id"])
        self.bump(feedback, status="awaiting_acceptance", detail={"adjustment_object_id": adjustment["object_id"],
                                                               "resolution_decision_revision_id": payload["decision_revision_id"]})
        self.conn.execute(
            """UPDATE gov_feedback_state SET resolution_source='adjustment',resolution_decision_revision_id=%s,
               resolution_adjustment_object_id=%s,resolution_adjustment_receipt_id=%s,updated_at=clock_timestamp()
               WHERE scope_id=%s AND object_id=%s AND cycle_id=%s""",
            (payload["decision_revision_id"], adjustment["object_id"], self.action_id,
             self.ctx.scope_id, feedback["object_id"], feedback["processing_cycle_id"]),
        )
        return {"object_id": adjustment["object_id"], "revision_id": revision["revision_id"],
                "feedback_object_id": feedback["object_id"], "changes": changes}

    def route_feedback(self) -> dict[str, Any]:
        state = self.feedback_state()
        if self.target["lifecycle_status"] != "open":
            _fail("INVALID_STATE")
        assignment = self.assignment(self.params["responsible_assignment_id"])
        if assignment["principal_type"] != "human" or assignment["role"] not in {"CEO", "DOMAIN_DRI", "MISSION_DRI"}:
            _fail("FORBIDDEN")
        self.conn.execute(
            "UPDATE gov_feedback_state SET assignee_assignment_id=%s,updated_at=clock_timestamp() WHERE scope_id=%s AND object_id=%s",
            (assignment["assignment_id"], self.ctx.scope_id, self.target["object_id"]),
        )
        obj = self.bump(self.target, status="routed", detail={"responsible_assignment_id": assignment["assignment_id"]})
        return {"object_id": obj["object_id"], "cycle_id": state["cycle_id"]}

    def assigned_transition(self, before: str, after: str) -> dict[str, Any]:
        state = self.feedback_state()
        if self.target["lifecycle_status"] != before or not state["assignee_assignment_id"]:
            _fail("INVALID_STATE")
        assignment = self.assignment(state["assignee_assignment_id"])
        if assignment["principal_id"] != self.ctx.principal_id:
            _fail("FORBIDDEN", "Only the assigned responsible human can perform this transition.")
        self.required_assignments.add(assignment["assignment_id"])
        obj = self.bump(self.target, status=after)
        return {"object_id": obj["object_id"], "cycle_id": state["cycle_id"]}

    def request_feedback_acceptance(self) -> dict[str, Any]:
        state = self.feedback_state()
        if self.target["lifecycle_status"] != "investigating":
            _fail("INVALID_STATE")
        self.confirmed_decision(self.params["decision_revision_id"])
        assignment = self.assignment(state["assignee_assignment_id"]) if state["assignee_assignment_id"] else None
        if assignment is None or assignment["principal_id"] != self.ctx.principal_id:
            _fail("FORBIDDEN")
        self.required_assignments.add(assignment["assignment_id"])
        self.conn.execute(
            """UPDATE gov_feedback_state SET resolution_source='decision',resolution_decision_revision_id=%s,
               resolution_adjustment_object_id=NULL,resolution_adjustment_receipt_id=NULL,updated_at=clock_timestamp()
               WHERE scope_id=%s AND object_id=%s""",
            (self.params["decision_revision_id"], self.ctx.scope_id, self.target["object_id"]),
        )
        obj = self.bump(self.target, status="awaiting_acceptance", detail={"resolution_decision_revision_id": self.params["decision_revision_id"]})
        return {"object_id": obj["object_id"], "cycle_id": state["cycle_id"]}

    def resolution_scope(self, decision_revision_id: str) -> tuple[dict[str, Any], set[str]]:
        state = self.feedback_state()
        if self.target["lifecycle_status"] != "awaiting_acceptance" or not state["resolution_source"]:
            _fail("INVALID_STATE", "Feedback is not awaiting independent acceptance.")
        if state["resolution_decision_revision_id"] != decision_revision_id:
            _fail("INVALID_STATE", "Decision does not match this feedback processing cycle.")
        _, decision = self.confirmed_decision(decision_revision_id)
        excluded = {decision["recorded_by"]}
        if state["assignee_assignment_id"]:
            row = self.conn.execute(
                "SELECT principal_id FROM gov_role_assignments WHERE scope_id=%s AND assignment_id=%s",
                (self.ctx.scope_id, state["assignee_assignment_id"]),
            ).fetchone()
            if row:
                excluded.add(str(row["principal_id"]))
        if state["resolution_source"] == "adjustment":
            adjustment = self.head(state["resolution_adjustment_object_id"])
            if adjustment["lifecycle_status"] != "applied" or not adjustment["effective_revision_id"]:
                _fail("INVALID_STATE")
            revision = self.revision(adjustment["object_id"], adjustment["effective_revision_id"])
            if revision["payload"]["feedback_revision_id"] != self.target["latest_revision_id"] or revision["payload"]["decision_revision_id"] != decision_revision_id:
                _fail("INVALID_STATE", "Applied adjustment no longer matches the resolution scope.")
            authors = self.conn.execute(
                "SELECT DISTINCT recorded_by FROM gov_object_revisions WHERE scope_id=%s AND object_id=%s",
                (self.ctx.scope_id, adjustment["object_id"]),
            ).fetchall()
            excluded.update(str(row["recorded_by"]) for row in authors)
            changed_ids = []
            for change in revision["payload"]["changes"]:
                obj = self.head(change["object_id"])
                candidate = self.revision(obj["object_id"], change["to_revision_id"])
                if obj["effective_revision_id"] != change["to_revision_id"]:
                    _fail("STALE_DEPENDENCY", "An affected effective revision has changed since the adjustment.")
                excluded.add(candidate["recorded_by"])
                changed_ids.append(obj["object_id"])
                signers = self.conn.execute(
                    "SELECT principal_id FROM gov_handshakes WHERE scope_id=%s AND object_id=%s AND revision_id=%s",
                    (self.ctx.scope_id, obj["object_id"], candidate["revision_id"]),
                ).fetchall()
                excluded.update(str(row["principal_id"]) for row in signers)
            receipts = self.conn.execute(
                """SELECT receipt_id, principal_id, effect_task_ids FROM gov_action_receipts
                   WHERE scope_id=%s AND (receipt_id=%s OR
                       (target_object_id=ANY(%s::uuid[]) AND action_type='activate_commitment'))""",
                (self.ctx.scope_id, state["resolution_adjustment_receipt_id"], changed_ids),
            ).fetchall()
            receipts = db.jsonable(receipts)
            if not any(row["receipt_id"] == state["resolution_adjustment_receipt_id"] for row in receipts):
                _fail("INVALID_STATE", "Applied adjustment receipt is missing.")
            effect_ids: set[str] = set()
            for receipt in receipts:
                excluded.add(receipt["principal_id"])
                effect_ids.update(receipt["effect_task_ids"])
            if not effect_ids:
                _fail("INVALID_STATE", "Adjustment has no external effect reservation.")
            tasks = self.conn.execute(
                """SELECT task_id,state FROM runtime_tasks WHERE tenant_id=%s AND organization_id=%s
                   AND task_id=ANY(%s::uuid[])""",
                (self.ctx.tenant_id, self.ctx.company_id, sorted(effect_ids)),
            ).fetchall()
            if len(tasks) != len(effect_ids) or any(row["state"] != "succeeded" for row in tasks):
                _fail("INVALID_STATE", "Relevant external effects have not all succeeded.")
        elif state["resolution_source"] != "decision":
            _fail("INVALID_STATE")
        return state, excluded

    def verify_evidence(self, revision_ids: list[str]) -> None:
        from .evidence import verify_revision
        for revision_id in revision_ids:
            obj, _ = self.revision_by_id(revision_id)
            self.same_domain(obj)
            if obj["object_type"] != "EvidenceAsset":
                _fail("INVALID_STATE", "Acceptance evidence must reference real evidence assets.")
            verify_revision(self.conn, self.ctx, revision_id)

    def record_acceptance(self) -> dict[str, Any]:
        state, excluded = self.resolution_scope(self.params["decision_revision_id"])
        verifier = next((item for item in self.action_assignments if item["role"] == "VERIFIER"), None)
        if verifier is None or self.ctx.principal_id in excluded:
            _fail("FORBIDDEN", "An independent authorized verifier must assess the resolution.")
        self.assignment(verifier["assignment_id"])
        self.required_assignments.add(verifier["assignment_id"])
        self.verify_evidence(self.params["evidence_revision_ids"])
        acceptance_id = str(uuid4())
        self.conn.execute(
            """INSERT INTO gov_acceptances
               (acceptance_id,scope_id,feedback_object_id,cycle_id,feedback_revision_id,decision_revision_id,
                evidence_revision_ids,verification_result,verifier_assignment_id,verifier_principal_id,action_id)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (acceptance_id, self.ctx.scope_id, self.target["object_id"], state["cycle_id"],
             self.target["latest_revision_id"], self.params["decision_revision_id"], Jsonb(self.params["evidence_revision_ids"]),
             self.params["verification_result"], verifier["assignment_id"], self.ctx.principal_id, self.action_id),
        )
        status = "awaiting_acceptance" if self.params["verification_result"] == "accepted" else "investigating"
        obj = self.bump(self.target, status=status, detail={"acceptance_id": acceptance_id,
                                                         "verification_result": self.params["verification_result"]})
        return {"object_id": obj["object_id"], "acceptance_id": acceptance_id, "cycle_id": state["cycle_id"],
                "verification_result": self.params["verification_result"]}

    def confirm_closure(self) -> dict[str, Any]:
        state, _ = self.resolution_scope(self.params["resolution_decision_revision_id"])
        disposition = self.params["disposition"]
        if (state["resolution_source"] == "adjustment") != (disposition == "resolved"):
            _fail("INVALID_STATE", "Closure disposition must match the recorded resolution path.")
        acceptance = self.conn.execute(
            "SELECT * FROM gov_acceptances WHERE scope_id=%s AND acceptance_id=%s",
            (self.ctx.scope_id, self.params["acceptance_record_id"]),
        ).fetchone()
        if acceptance is None:
            _fail("INVALID_STATE", "A valid acceptance record is required.")
        acceptance = db.jsonable(acceptance)
        if (acceptance["feedback_object_id"] != self.target["object_id"]
                or acceptance["cycle_id"] != state["cycle_id"]
                or acceptance["feedback_revision_id"] != self.target["latest_revision_id"]
                or acceptance["decision_revision_id"] != self.params["resolution_decision_revision_id"]
                or acceptance["verification_result"] != "accepted"
                or sorted(acceptance["evidence_revision_ids"]) != sorted(self.params["closure_evidence_revision_ids"])):
            _fail("INVALID_STATE", "Acceptance does not match this revision, cycle, decision and evidence.")
        latest = self.conn.execute(
            """SELECT acceptance_id FROM gov_acceptances WHERE scope_id=%s AND feedback_object_id=%s AND cycle_id=%s
               ORDER BY recorded_at DESC,acceptance_id DESC LIMIT 1""",
            (self.ctx.scope_id, self.target["object_id"], state["cycle_id"]),
        ).fetchone()
        if latest is None or str(latest["acceptance_id"]) != acceptance["acceptance_id"]:
            _fail("INVALID_STATE", "A later verification supersedes this acceptance.")
        self.verify_evidence(self.params["closure_evidence_revision_ids"])
        obj = self.bump(self.target, status="dismissed" if disposition == "dismissed" else "closed",
                        detail={"acceptance_id": acceptance["acceptance_id"], "disposition": disposition,
                                "closure_note": self.params["closure_note"],
                                "resolution_decision_revision_id": self.params["resolution_decision_revision_id"]})
        return {"object_id": obj["object_id"], "acceptance_id": acceptance["acceptance_id"],
                "cycle_id": state["cycle_id"], "disposition": disposition}

    def reopen_feedback(self) -> dict[str, Any]:
        state = self.feedback_state()
        if self.target["lifecycle_status"] not in {"closed", "dismissed"}:
            _fail("INVALID_STATE")
        assignment = self.conn.execute(
            """SELECT a.assignment_id FROM gov_role_assignments a JOIN gov_principals p
                 ON p.scope_id=a.scope_id AND p.principal_id=a.principal_id
               WHERE a.scope_id=%s AND a.assignment_id=%s AND a.active AND p.active
                 AND a.valid_from<=clock_timestamp() AND (a.valid_to IS NULL OR clock_timestamp()<a.valid_to)""",
            (self.ctx.scope_id, state["assignee_assignment_id"]),
        ).fetchone()
        cycle_id = str(uuid4())
        assigned = str(assignment["assignment_id"]) if assignment else None
        self.conn.execute(
            """UPDATE gov_feedback_state SET cycle_id=%s,assignee_assignment_id=%s,resolution_source=NULL,
               resolution_decision_revision_id=NULL,resolution_adjustment_object_id=NULL,
               resolution_adjustment_receipt_id=NULL,updated_at=clock_timestamp() WHERE scope_id=%s AND object_id=%s""",
            (cycle_id, assigned, self.ctx.scope_id, self.target["object_id"]),
        )
        # Same content, a new immutable revision/cycle: old acceptance cannot be reused.
        revision = self.insert_revision(self.target, self.target_revision["payload"], version=self.target["object_version"] + 1)
        obj = self.bump(self.target, status="investigating" if assigned else "open", cycle=cycle_id,
                        latest=revision["revision_id"], effective=revision["revision_id"],
                        detail={"previous_cycle_id": state["cycle_id"], "reopen_reason": self.request.reason})
        return {"object_id": obj["object_id"], "revision_id": revision["revision_id"], "cycle_id": cycle_id}

    def revoke_assignment(self) -> dict[str, Any]:
        if not any(item["role"] == "CEO" for item in self.action_assignments):
            _fail("FORBIDDEN")
        assignment = self.conn.execute(
            "SELECT * FROM gov_role_assignments WHERE scope_id=%s AND assignment_id=%s FOR UPDATE",
            (self.ctx.scope_id, self.params["assignment_id"]),
        ).fetchone()
        if assignment is None:
            _fail("NOT_FOUND")
        assignment = db.jsonable(assignment)
        if assignment["principal_id"] == self.ctx.principal_id:
            _fail("FORBIDDEN", "Self-revocation is not supported by this pilot.")
        previous_epoch = self.ctx.auth_epoch
        if assignment["active"]:
            self.conn.execute(
                "UPDATE gov_role_assignments SET active=false WHERE scope_id=%s AND assignment_id=%s",
                (self.ctx.scope_id, assignment["assignment_id"]),
            )
            row = self.conn.execute(
                "UPDATE gov_scopes SET auth_epoch=auth_epoch+1 WHERE scope_id=%s RETURNING auth_epoch",
                (self.ctx.scope_id,),
            ).fetchone()
            self.receipt_epoch = row["auth_epoch"]
        else:
            self.receipt_epoch = previous_epoch
        # Assignment is not a gov_objects row: the immutable receipt is its audit
        # event, including the exact before/after authority state and scope epoch.
        return {"assignment_id": assignment["assignment_id"], "before_active": assignment["active"],
                "after_active": False, "before_auth_epoch": previous_epoch, "auth_epoch": self.receipt_epoch}

    def run_action(self) -> dict[str, Any]:
        if self.kind in delivery.ACTIONS:
            return delivery.run_action(self)
        methods = {
            "create_object": self.create_object,
            "propose_revision": self.propose_revision,
            "accept_commitment": self.accept_commitment,
            "activate_commitment": self.activate_commitment,
            "confirm_adjustment": self.confirm_adjustment,
            "confirm_closure": self.confirm_closure,
            "route_feedback": self.route_feedback,
            "accept_feedback": lambda: self.assigned_transition("routed", "accepted"),
            "investigate_feedback": lambda: self.assigned_transition("accepted", "investigating"),
            "confirm_decision": lambda: self.confirm_content("Decision"),
            "confirm_outcome": lambda: self.confirm_content("CompanyOutcome"),
            "record_acceptance": self.record_acceptance,
            "request_feedback_acceptance": self.request_feedback_acceptance,
            "reopen_feedback": self.reopen_feedback,
            "revoke_assignment": self.revoke_assignment,
        }
        return methods[self.kind]()

    def finish(self, result: dict[str, Any], request_hash: str) -> dict[str, Any]:
        versions = [{"object_id": key, "object_version": value["object_version"]}
                    for key, value in sorted(self.changed.items())]
        result.update(domain_id=self.domain_id, referenced_object_ids=sorted(self.heads),
                      required_assignment_ids=sorted(self.required_assignments))
        if self.kind in {"activate_commitment", "confirm_adjustment"}:
            effect_payload = {"effect_key": f"governed:{self.ctx.scope_id}:{self.action_id}:0",
                              "scope_id": self.ctx.scope_id, "receipt_id": self.action_id,
                              "action_type": self.kind, "object_versions": versions,
                              "required_assignment_ids": sorted(self.required_assignments)}
            enqueued = enqueue_task(
                self.conn, tenant_id=self.ctx.tenant_id, organization_id=self.ctx.company_id,
                task_type="governance.dispatch", idempotency_key=effect_payload["effect_key"], payload=effect_payload,
            )
            self.effect_task_ids.append(enqueued.task.task_id)
            result["effect_payload"] = effect_payload
        epoch = getattr(self, "receipt_epoch", self.ctx.auth_epoch)
        row = self.conn.execute(
            """INSERT INTO gov_action_receipts
               (receipt_id,scope_id,principal_id,idempotency_key,request_hash,action_type,auth_epoch,result,
                object_versions,effect_task_ids,target_object_id)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
            (self.action_id, self.ctx.scope_id, self.ctx.principal_id, self.request.idempotency_key,
             request_hash, self.kind, epoch, Jsonb(result), Jsonb(versions), Jsonb(self.effect_task_ids),
             self.request.target.object_id if self.request.target else result.get("object_id")),
        ).fetchone()
        checkpoints.checkpoint("before_business_commit", {"action_type": self.kind, "receipt_id": self.action_id,
                                                            "auth_epoch": epoch})
        # Natural assignment expiry is not stopped by the scope lock. Recheck
        # after slow evidence calls/test barriers and immediately before return.
        db.authorize_domain(self.conn, self.ctx, self.domain_id, action_type=self.kind)
        for assignment_id in self.required_assignments:
            self.assignment(assignment_id)
        return _receipt(row)


def _replay(conn: Any, ctx: Any, request: ActionRequest, digest: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM gov_action_receipts WHERE scope_id=%s AND principal_id=%s AND idempotency_key=%s",
        (ctx.scope_id, ctx.principal_id, request.idempotency_key),
    ).fetchone()
    if row is None:
        return None
    row = db.jsonable(row)
    # Reauthorize before returning a historical success, including every linked
    # dependency and any target whose domain rights have since been revoked.
    if row["result"].get("domain_id"):
        db.authorize_domain(conn, ctx, row["result"]["domain_id"], action_type="read")
    ids = set(row["result"].get("referenced_object_ids", []))
    ids.update(item["object_id"] for item in row["object_versions"])
    if row["target_object_id"]:
        ids.add(row["target_object_id"])
    for object_id in ids:
        db.object_row(conn, ctx, object_id)
    if row["request_hash"] != digest or row["action_type"] != request.action_type:
        _fail("IDEMPOTENCY_CONFLICT", "The key was used for a different command.")
    return _receipt(row)


def prepare_action(conn: Any, ctx: Any, request: ActionRequest) -> dict[str, Any]:
    """Resolve required versions without mutating business state or receipts."""
    execution = ActionExecution(conn, ctx, request)
    execution.authorize()
    execution.collect_dependencies()
    return {
        "action_type": request.action_type, "auth_epoch": ctx.auth_epoch,
        "target": ({"object_id": execution.target["object_id"],
                    "expected_version": execution.target["object_version"],
                    "revision_id": request.target.revision_id} if request.target else None),
        "expected_versions": [{"object_id": object_id, "expected_version": execution.heads[object_id]["object_version"]}
                              for object_id in sorted(execution.dependencies)],
    }


def execute_action(conn: Any, ctx: Any, request: ActionRequest) -> dict[str, Any]:
    digest = _hash(request.model_dump(mode="json", exclude_none=True))
    replay = _replay(conn, ctx, request, digest)
    if replay is not None:
        return replay
    execution = ActionExecution(conn, ctx, request)
    execution.authorize()
    execution.collect_dependencies()
    execution.check_versions()
    result = execution.run_action()
    return execution.finish(result, digest)


__all__ = ["execute_action", "prepare_action"]
