"""Read-only integrity verifier for Memory Service data.

``verify_memory`` borrows the caller's connection (no transaction management, no writes)
and reports invariant violations as frozen findings.  It never repairs data and never
reports a clean result it did not actually check.  All checks are plain SQL against the
borrowed connection; the caller decides the transaction scope.

Checks (current high-value invariants):
- confirmed actors exist / same scope / kind='human' (graph, WM, agreement, audit)
- WM persisted content satisfies the canonical required-field contract
- WM source snapshot: content_hash_snapshot == sha256(excerpt_snapshot)
- WM version chain linearity: supersedes target is exactly version-1 (DB enforces
  uniqueness/head/no-fork; continuity is service-level, so it is checked here)
- reference type logic the DB FK cannot express: wm_objects.issue_id target is an Issue,
  wm_issue_signals endpoints are Issue/Signal, confirmed_judgment_record_id points at a
  confirmed Judgment, agreement_record_id points at a confirmed Agreement
- Agreement consistency: confirmed Agreement has non-empty parties, confirmations equal
  the party set, every party/confirmation is a human in scope
- confirmed typed graph entities satisfy the canonical content/discriminator contract
- typed graph source refs: content_hash_snapshot == sha256(excerpt_snapshot)
  (self-contained frozen values; document_fragments is never queried)
- graph root/cardinality: exactly one confirmed CompanyVision and the required relation
  cardinalities per confirmed typed entity
- typed relation matrix: every confirmed relation between typed endpoints re-validated
  against the canonical ``validate_relation_matrix`` (no rule duplication)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from memory_service.context_graph.types import (
    ContextGraphValidationError,
    validate_entity_content,
    validate_relation_matrix,
)
from memory_service.working_contracts import AGREEMENT_DISPOSITIONS, missing_required_content


@dataclass(frozen=True)
class MemoryFinding:
    """One detected invariant violation."""

    check: str
    subject: str
    detail: str


@dataclass(frozen=True)
class MemoryCheckReport:
    tenant_id: str
    organization_id: str
    generation_id: str | None
    findings: tuple[MemoryFinding, ...]

    @property
    def ok(self) -> bool:
        return not self.findings


def verify_memory(
    conn: Any,
    tenant_id: str,
    organization_id: str,
    generation_id: str | None = None,
) -> MemoryCheckReport:
    """Run all checks read-only on the borrowed connection and return the report."""
    findings: list[MemoryFinding] = []
    resolved = _resolve_generation(conn, tenant_id, organization_id, generation_id)
    if resolved is None:
        if generation_id is None:
            findings.append(MemoryFinding(
                "generation", f"{tenant_id}/{organization_id}",
                "no current generation for scope; graph checks skipped",
            ))
        else:
            findings.append(MemoryFinding(
                "generation", generation_id,
                "generation does not exist in scope; graph checks skipped",
            ))
    else:
        findings.extend(_confirmed_actors_graph(conn, tenant_id, organization_id, resolved))
        findings.extend(_graph_entity_contract(conn, tenant_id, organization_id, resolved))
        findings.extend(_graph_source_snapshot_sha(conn, tenant_id, organization_id, resolved))
        findings.extend(_graph_root_and_cardinality(conn, tenant_id, organization_id, resolved))
        findings.extend(_graph_relation_matrix(conn, tenant_id, organization_id, resolved))
    findings.extend(_confirmed_actors_shared(conn, tenant_id, organization_id))
    findings.extend(_wm_required_content(conn, tenant_id, organization_id))
    findings.extend(_wm_source_snapshot_sha(conn, tenant_id, organization_id))
    findings.extend(_wm_version_linearity(conn, tenant_id, organization_id))
    findings.extend(_wm_reference_types(conn, tenant_id, organization_id))
    findings.extend(_agreement_consistency(conn, tenant_id, organization_id))
    return MemoryCheckReport(
        tenant_id=tenant_id,
        organization_id=organization_id,
        generation_id=resolved,
        findings=tuple(findings),
    )


def _resolve_generation(
    conn: Any, tenant_id: str, organization_id: str, generation_id: str | None,
) -> str | None:
    if generation_id is not None:
        row = conn.execute(
            """SELECT generation_id FROM context_graph_versions
               WHERE generation_id=%s AND tenant_id=%s AND organization_id=%s""",
            (generation_id, tenant_id, organization_id),
        ).fetchone()
        return str(row[0]) if row else None
    row = conn.execute(
        """SELECT generation_id FROM context_graph_versions
           WHERE tenant_id=%s AND organization_id=%s AND status='current'""",
        (tenant_id, organization_id),
    ).fetchone()
    return str(row[0]) if row else None


def _confirmed_actors_graph(
    conn: Any, tenant_id: str, organization_id: str, generation_id: str,
) -> list[MemoryFinding]:
    """Confirmed graph rows must be confirmed by an existing human in the same scope."""
    findings: list[MemoryFinding] = []
    for table, key in (("semantic_entities", "entity_id"), ("semantic_relations", "relation_id")):
        rows = conn.execute(
            f"""SELECT t.{key} AS subject_id, t.confirmed_by
                FROM {table} t
                LEFT JOIN users u ON u.user_id = t.confirmed_by
                WHERE t.tenant_id=%s AND t.organization_id=%s AND t.graph_generation_id=%s
                  AND t.status='confirmed'
                  AND (u.user_id IS NULL OR u.kind<>'human'
                       OR u.tenant_id<>t.tenant_id OR u.organization_id<>t.organization_id)""",
            (tenant_id, organization_id, generation_id),
        ).fetchall()
        for subject_id, confirmed_by in rows:
            findings.append(MemoryFinding(
                "confirmed_actors", f"{table}/{subject_id}",
                f"confirmed_by {confirmed_by} is missing, not human, or out of scope",
            ))
    return findings


def _confirmed_actors_shared(conn: Any, tenant_id: str, organization_id: str) -> list[MemoryFinding]:
    """WM versions, agreement confirmations/parties and audit actors: human + same scope."""
    findings: list[MemoryFinding] = []
    rows = conn.execute(
        """SELECT v.record_id, v.confirmed_by
           FROM wm_object_versions v
           JOIN wm_objects o ON o.object_id = v.object_id
           JOIN wm_issue_chains c ON c.chain_id = o.chain_id
           LEFT JOIN users u ON u.user_id = v.confirmed_by
           WHERE c.tenant_id=%s AND c.organization_id=%s
             AND v.confirmation_status='confirmed'
             AND (u.user_id IS NULL OR u.kind<>'human'
                  OR u.tenant_id<>c.tenant_id OR u.organization_id<>c.organization_id)""",
        (tenant_id, organization_id),
    ).fetchall()
    for record_id, confirmed_by in rows:
        findings.append(MemoryFinding(
            "confirmed_actors", f"wm_object_versions/{record_id}",
            f"confirmed_by {confirmed_by} is missing, not human, or out of scope",
        ))

    rows = conn.execute(
        """SELECT ac.agreement_record_id, ac.confirmer
           FROM wm_agreement_confirmations ac
           JOIN wm_object_versions v ON v.record_id = ac.agreement_record_id
           JOIN wm_objects o ON o.object_id = v.object_id
           JOIN wm_issue_chains c ON c.chain_id = o.chain_id
           LEFT JOIN users u ON u.user_id = ac.confirmer
           WHERE c.tenant_id=%s AND c.organization_id=%s
             AND (u.user_id IS NULL OR u.kind<>'human'
                  OR u.tenant_id<>c.tenant_id OR u.organization_id<>c.organization_id)""",
        (tenant_id, organization_id),
    ).fetchall()
    for record_id, confirmer in rows:
        findings.append(MemoryFinding(
            "confirmed_actors", f"wm_agreement_confirmations/{record_id}/{confirmer}",
            f"confirmer {confirmer} is missing, not human, or out of scope",
        ))

    rows = conn.execute(
        """SELECT p.agreement_record_id, p.party
           FROM wm_agreement_parties p
           JOIN wm_object_versions v ON v.record_id = p.agreement_record_id
           JOIN wm_objects o ON o.object_id = v.object_id
           JOIN wm_issue_chains c ON c.chain_id = o.chain_id
           LEFT JOIN users u ON u.user_id = p.party
           WHERE c.tenant_id=%s AND c.organization_id=%s
             AND (u.user_id IS NULL OR u.kind<>'human'
                  OR u.tenant_id<>c.tenant_id OR u.organization_id<>c.organization_id)""",
        (tenant_id, organization_id),
    ).fetchall()
    for record_id, party in rows:
        findings.append(MemoryFinding(
            "confirmed_actors", f"wm_agreement_parties/{record_id}/{party}",
            f"party {party} is missing, not human, or out of scope",
        ))

    rows = conn.execute(
        """SELECT a.audit_id, a.decided_by
           FROM memory_audit a
           JOIN memory_proposals p ON p.proposal_id = a.proposal_id
           LEFT JOIN users u ON u.user_id = a.decided_by
           WHERE p.tenant_id=%s AND p.organization_id=%s
             AND (u.user_id IS NULL OR u.kind<>'human'
                  OR u.tenant_id<>p.tenant_id OR u.organization_id<>p.organization_id)""",
        (tenant_id, organization_id),
    ).fetchall()
    for audit_id, decided_by in rows:
        findings.append(MemoryFinding(
            "confirmed_actors", f"memory_audit/{audit_id}",
            f"decided_by {decided_by} is missing, not human, or out of scope",
        ))
    return findings


def _wm_required_content(conn: Any, tenant_id: str, organization_id: str) -> list[MemoryFinding]:
    rows = conn.execute(
        """SELECT v.record_id, v.object_type, v.content
           FROM wm_object_versions v
           JOIN wm_objects o ON o.object_id = v.object_id
           JOIN wm_issue_chains c ON c.chain_id = o.chain_id
           WHERE c.tenant_id=%s AND c.organization_id=%s""",
        (tenant_id, organization_id),
    ).fetchall()
    findings: list[MemoryFinding] = []
    for record_id, object_type, content in rows:
        if not isinstance(content, dict):
            findings.append(MemoryFinding(
                "wm_required_content", f"wm_object_versions/{record_id}",
                f"{object_type} content must be an object",
            ))
            continue
        missing = missing_required_content(object_type, content)
        if missing:
            findings.append(MemoryFinding(
                "wm_required_content", f"wm_object_versions/{record_id}",
                f"{object_type} missing required content fields {list(missing)}",
            ))
        if object_type == "Agreement" and content.get("disposition") not in AGREEMENT_DISPOSITIONS:
            findings.append(MemoryFinding(
                "wm_required_content", f"wm_object_versions/{record_id}",
                f"Agreement has invalid disposition {content.get('disposition')!r}",
            ))
    return findings


def _wm_source_snapshot_sha(conn: Any, tenant_id: str, organization_id: str) -> list[MemoryFinding]:
    rows = conn.execute(
        """SELECT r.record_id, r.fragment_id
           FROM wm_version_source_refs r
           JOIN wm_issue_chains c ON c.chain_id = r.chain_id
           WHERE c.tenant_id=%s AND c.organization_id=%s
             AND r.content_hash_snapshot <> encode(sha256(convert_to(r.excerpt_snapshot,'UTF8')),'hex')""",
        (tenant_id, organization_id),
    ).fetchall()
    return [MemoryFinding(
        "wm_source_snapshot_sha", f"wm_version_source_refs/{record_id}/{fragment_id}",
        "content_hash_snapshot does not match sha256 of excerpt_snapshot",
    ) for record_id, fragment_id in rows]


def _graph_entity_contract(
    conn: Any, tenant_id: str, organization_id: str, generation_id: str,
) -> list[MemoryFinding]:
    rows = conn.execute(
        """SELECT entity_id,type_key,content,rationale,strategic_level,strategic_period,
                  outcome_level,status_scope,org_subtype,is_moat
           FROM semantic_entities
           WHERE tenant_id=%s AND organization_id=%s AND graph_generation_id=%s
             AND status='confirmed' AND type_key IS NOT NULL""",
        (tenant_id, organization_id, generation_id),
    ).fetchall()
    findings: list[MemoryFinding] = []
    for (entity_id, type_key, content, rationale, strategic_level, strategic_period,
         outcome_level, status_scope, org_subtype, is_moat) in rows:
        try:
            validate_entity_content(
                type_key, content, rationale,
                strategic_level=strategic_level,
                strategic_period=strategic_period,
                outcome_level=outcome_level,
                status_scope=status_scope,
                org_subtype=org_subtype,
                is_moat=is_moat,
            )
        except ContextGraphValidationError as exc:
            findings.append(MemoryFinding(
                "graph_entity_contract", f"semantic_entities/{entity_id}", str(exc),
            ))
    return findings


def _graph_source_snapshot_sha(
    conn: Any, tenant_id: str, organization_id: str, generation_id: str,
) -> list[MemoryFinding]:
    findings: list[MemoryFinding] = []
    for table, owner_key in (
        ("semantic_entity_source_refs", "entity_id"),
        ("semantic_relation_source_refs", "relation_id"),
    ):
        rows = conn.execute(
            f"""SELECT r.{owner_key} AS owner_id, r.fragment_id
                FROM {table} r
                WHERE r.tenant_id=%s AND r.organization_id=%s AND r.graph_generation_id=%s
                  AND r.content_hash_snapshot <> encode(sha256(convert_to(r.excerpt_snapshot,'UTF8')),'hex')""",
            (tenant_id, organization_id, generation_id),
        ).fetchall()
        for owner_id, fragment_id in rows:
            findings.append(MemoryFinding(
                "graph_source_snapshot_sha", f"{table}/{owner_id}/{fragment_id}",
                "content_hash_snapshot does not match sha256 of excerpt_snapshot",
            ))
    return findings


def _wm_version_linearity(conn: Any, tenant_id: str, organization_id: str) -> list[MemoryFinding]:
    rows = conn.execute(
        """SELECT v.record_id, v.version, p.version AS previous_version
           FROM wm_object_versions v
           JOIN wm_object_versions p ON p.record_id = v.supersedes
           JOIN wm_objects o ON o.object_id = v.object_id
           JOIN wm_issue_chains c ON c.chain_id = o.chain_id
           WHERE c.tenant_id=%s AND c.organization_id=%s AND p.version <> v.version - 1""",
        (tenant_id, organization_id),
    ).fetchall()
    return [MemoryFinding(
        "wm_version_linearity", f"wm_object_versions/{record_id}",
        f"version {version} supersedes version {previous_version} (must be version-1)",
    ) for record_id, version, previous_version in rows]


def _wm_reference_types(conn: Any, tenant_id: str, organization_id: str) -> list[MemoryFinding]:
    """Type logic the composite FKs deliberately cannot express (service-level contracts)."""
    findings: list[MemoryFinding] = []
    rows = conn.execute(
        """SELECT o.object_id, t.object_type
           FROM wm_objects o
           JOIN wm_objects t ON t.object_id = o.issue_id
           JOIN wm_issue_chains c ON c.chain_id = o.chain_id
           WHERE c.tenant_id=%s AND c.organization_id=%s
             AND o.issue_id IS NOT NULL AND t.object_type <> 'Issue'""",
        (tenant_id, organization_id),
    ).fetchall()
    for object_id, target_type in rows:
        findings.append(MemoryFinding(
            "wm_reference_types", f"wm_objects/{object_id}",
            f"issue_id target has object_type {target_type!r}, expected 'Issue'",
        ))

    rows = conn.execute(
        """SELECT s.issue_object_id, i.object_type, s.signal_object_id, g.object_type
           FROM wm_issue_signals s
           JOIN wm_objects i ON i.object_id = s.issue_object_id
           JOIN wm_objects g ON g.object_id = s.signal_object_id
           JOIN wm_issue_chains c ON c.chain_id = s.chain_id
           WHERE c.tenant_id=%s AND c.organization_id=%s
             AND (i.object_type <> 'Issue' OR g.object_type <> 'Signal')""",
        (tenant_id, organization_id),
    ).fetchall()
    for issue_id, issue_type, signal_id, signal_type in rows:
        findings.append(MemoryFinding(
            "wm_reference_types", f"wm_issue_signals/{issue_id}/{signal_id}",
            f"formation endpoints must be Issue/Signal, got {issue_type!r}/{signal_type!r}",
        ))

    rows = conn.execute(
        """SELECT v.record_id, j.object_type, j.confirmation_status
           FROM wm_object_versions v
           JOIN wm_object_versions j ON j.record_id = v.confirmed_judgment_record_id
           JOIN wm_objects o ON o.object_id = v.object_id
           JOIN wm_objects jo ON jo.object_id = j.object_id
           JOIN wm_issue_chains c ON c.chain_id = o.chain_id
           WHERE c.tenant_id=%s AND c.organization_id=%s
             AND (j.object_type <> 'Judgment' OR j.confirmation_status <> 'confirmed'
                  OR jo.chain_id <> o.chain_id OR jo.issue_id IS DISTINCT FROM o.issue_id)""",
        (tenant_id, organization_id),
    ).fetchall()
    for record_id, target_type, status in rows:
        findings.append(MemoryFinding(
            "wm_reference_types", f"wm_object_versions/{record_id}",
            f"confirmed_judgment_record_id target is {target_type!r}/{status!r}; "
            "expected a confirmed Judgment in the same chain and Issue",
        ))

    rows = conn.execute(
        """SELECT v.record_id, o.object_type, a.object_type, a.confirmation_status,
                  a.content->>'disposition'
           FROM wm_object_versions v
           JOIN wm_object_versions a ON a.record_id = v.agreement_record_id
           JOIN wm_objects o ON o.object_id = v.object_id
           JOIN wm_objects ao ON ao.object_id = a.object_id
           JOIN wm_issue_chains c ON c.chain_id = o.chain_id
           WHERE c.tenant_id=%s AND c.organization_id=%s
             AND (a.object_type <> 'Agreement' OR a.confirmation_status <> 'confirmed'
                  OR ao.chain_id <> o.chain_id OR ao.issue_id IS DISTINCT FROM o.issue_id
                  OR o.object_type NOT IN ('Strategic Mission','Close')
                  OR (o.object_type='Strategic Mission'
                      AND a.content->>'disposition' IS DISTINCT FROM 'strategic_mission')
                  OR (o.object_type='Close'
                      AND a.content->>'disposition' IS DISTINCT FROM 'close'))""",
        (tenant_id, organization_id),
    ).fetchall()
    for record_id, source_type, target_type, status, disposition in rows:
        expected_disposition = {
            "Strategic Mission": "strategic_mission", "Close": "close",
        }.get(source_type)
        findings.append(MemoryFinding(
            "wm_reference_types", f"wm_object_versions/{record_id}",
            f"agreement_record_id target is {target_type!r}/{status!r}, "
            f"disposition={disposition!r}; expected a confirmed Agreement with "
            f"disposition={expected_disposition!r} in the same chain and Issue",
        ))
    return findings


def _agreement_consistency(conn: Any, tenant_id: str, organization_id: str) -> list[MemoryFinding]:
    """Confirmed Agreement: parties non-empty, confirmations exactly cover the party set."""
    findings: list[MemoryFinding] = []
    rows = conn.execute(
        """SELECT v.record_id
           FROM wm_object_versions v
           JOIN wm_objects o ON o.object_id = v.object_id
           JOIN wm_issue_chains c ON c.chain_id = o.chain_id
           WHERE c.tenant_id=%s AND c.organization_id=%s
             AND v.object_type='Agreement' AND v.confirmation_status='confirmed'
             AND NOT EXISTS (
                 SELECT 1 FROM wm_agreement_parties p WHERE p.agreement_record_id = v.record_id)""",
        (tenant_id, organization_id),
    ).fetchall()
    for (record_id,) in rows:
        findings.append(MemoryFinding(
            "agreement_consistency", f"wm_object_versions/{record_id}",
            "confirmed Agreement has no parties",
        ))

    rows = conn.execute(
        """SELECT ac.agreement_record_id, ac.confirmer
           FROM wm_agreement_confirmations ac
           JOIN wm_object_versions v ON v.record_id = ac.agreement_record_id
           JOIN wm_objects o ON o.object_id = v.object_id
           JOIN wm_issue_chains c ON c.chain_id = o.chain_id
           LEFT JOIN wm_agreement_parties p
             ON p.agreement_record_id = ac.agreement_record_id AND p.party = ac.confirmer
           WHERE c.tenant_id=%s AND c.organization_id=%s AND p.agreement_record_id IS NULL""",
        (tenant_id, organization_id),
    ).fetchall()
    for record_id, confirmer in rows:
        findings.append(MemoryFinding(
            "agreement_consistency", f"wm_agreement_confirmations/{record_id}/{confirmer}",
            "confirmation by non-party",
        ))

    rows = conn.execute(
        """SELECT v.record_id
           FROM wm_object_versions v
           JOIN wm_objects o ON o.object_id = v.object_id
           JOIN wm_issue_chains c ON c.chain_id = o.chain_id
           WHERE c.tenant_id=%s AND c.organization_id=%s
             AND v.object_type='Agreement' AND v.confirmation_status='confirmed'
             AND EXISTS (
                 SELECT 1 FROM wm_agreement_parties p
                 WHERE p.agreement_record_id = v.record_id
                   AND NOT EXISTS (
                     SELECT 1 FROM wm_agreement_confirmations ac
                     WHERE ac.agreement_record_id = v.record_id AND ac.confirmer = p.party))""",
        (tenant_id, organization_id),
    ).fetchall()
    for (record_id,) in rows:
        findings.append(MemoryFinding(
            "agreement_consistency", f"wm_object_versions/{record_id}",
            "confirmed Agreement with a party that has not confirmed",
        ))
    return findings


def _graph_root_and_cardinality(
    conn: Any, tenant_id: str, organization_id: str, generation_id: str,
) -> list[MemoryFinding]:
    """Typed confirmed graph: one root, required relation cardinalities, root never sources."""
    findings: list[MemoryFinding] = []
    scope = (tenant_id, organization_id, generation_id)

    roots = conn.execute(
        """SELECT entity_id FROM semantic_entities
           WHERE tenant_id=%s AND organization_id=%s AND graph_generation_id=%s
             AND status='confirmed' AND type_key='CompanyVision'""",
        scope,
    ).fetchall()
    if len(roots) != 1:
        findings.append(MemoryFinding(
            "graph_root_cardinality", f"generation/{generation_id}",
            f"expected exactly 1 confirmed CompanyVision root, found {len(roots)}",
        ))

    requirements = (
        ("primary_alignment", "every confirmed typed non-root entity",
         """AND (e.type_key IS DISTINCT FROM 'CompanyVision')"""),
        ("sub_strategy_of", "confirmed sub_strategy StrategicChoice",
         """AND e.type_key='StrategicChoice' AND e.strategic_level='sub_strategy'"""),
        ("responsible_for", "confirmed Outcome", """AND e.type_key='Outcome'"""),
        ("domain_outcome_of", "confirmed domain Outcome",
         """AND e.type_key='Outcome' AND e.outcome_level='domain'"""),
    )
    for relation_type, applies_to, extra in requirements:
        rows = conn.execute(
            f"""SELECT e.entity_id
                FROM semantic_entities e
                WHERE e.tenant_id=%s AND e.organization_id=%s AND e.graph_generation_id=%s
                  AND e.status='confirmed' AND e.type_key IS NOT NULL {extra}
                  AND NOT EXISTS (
                      SELECT 1 FROM semantic_relations r
                      WHERE r.source_id = e.entity_id AND r.relation_type = %s
                        AND r.status='confirmed'
                        AND r.graph_generation_id=%s AND r.tenant_id=%s AND r.organization_id=%s)""",
            (tenant_id, organization_id, generation_id, relation_type,
             generation_id, tenant_id, organization_id),
        ).fetchall()
        for (entity_id,) in rows:
            findings.append(MemoryFinding(
                "graph_root_cardinality", f"semantic_entities/{entity_id}",
                f"{applies_to} lacks a confirmed {relation_type} relation",
            ))
    return findings


def _graph_relation_matrix(
    conn: Any, tenant_id: str, organization_id: str, generation_id: str,
) -> list[MemoryFinding]:
    """Re-validate confirmed typed relations via the canonical relation matrix."""
    rows = conn.execute(
        """SELECT r.relation_id, r.relation_type,
                  s.type_key, s.strategic_level, s.strategic_period, s.outcome_level,
                  t.type_key, t.strategic_level, t.strategic_period, t.outcome_level
           FROM semantic_relations r
           JOIN semantic_entities s ON s.entity_id = r.source_id
           JOIN semantic_entities t ON t.entity_id = r.target_id
           WHERE r.tenant_id=%s AND r.organization_id=%s AND r.graph_generation_id=%s
             AND r.status='confirmed'
             AND s.type_key IS NOT NULL AND t.type_key IS NOT NULL""",
        (tenant_id, organization_id, generation_id),
    ).fetchall()
    findings: list[MemoryFinding] = []
    for (relation_id, relation_type,
         s_type, s_level, s_period, s_outcome,
         t_type, t_level, t_period, t_outcome) in rows:
        source = {"type_key": s_type, "strategic_level": s_level,
                  "strategic_period": s_period, "outcome_level": s_outcome}
        target = {"type_key": t_type, "strategic_level": t_level,
                  "strategic_period": t_period, "outcome_level": t_outcome}
        try:
            validate_relation_matrix(relation_type, source, target)
        except ContextGraphValidationError as exc:
            findings.append(MemoryFinding(
                "graph_relation_matrix", f"semantic_relations/{relation_id}", str(exc),
            ))
    return findings
