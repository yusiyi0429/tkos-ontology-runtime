"""Independent, read-only PostgreSQL oracles for governed Runtime acceptance.

No implementation module is imported. These functions require an idle psycopg
connection: they create and finish their own read-only transactions, never commit
an existing caller transaction. Reports contain counts, hashes and catalog
metadata only, never business rows or credential digests.

Take rollback-comparison snapshots while the test Worker is paused. Otherwise a
legitimate lease/heartbeat/task transition can change runtime_tasks independently
of the action being checked. Security rejection logs are deliberately excluded;
business history/audit, successful receipts and outbox tasks are not excluded.
"""
from __future__ import annotations

from collections import deque
import hashlib
from typing import Any

from psycopg import sql
from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row


class OracleFailure(AssertionError):
    """A required invariant failed, with no row/credential data in the message."""


# This is intentionally an explicit list. A new table is included until its
# rejection-only purpose has been independently checked and listed here.
REJECTION_ONLY_TABLES = frozenset({
    "gov_rejection_audit", "gov_request_rejections", "gov_denial_audit",
    "gov_rejected_requests",
})
MUTABLE_GOV_TABLES = frozenset({
    "gov_scopes", "gov_principals", "gov_credentials",
    "gov_role_assignments", "gov_objects", "gov_feedback_state", "gov_work_item_state",
})
# Credentials are an authentication bootstrap table whose RLS policy requires a
# digest, not the business scope GUC. Do not read/set digests for this oracle or
# quietly represent an RLS-filtered zero count as credential coverage.
AUTH_BOOTSTRAP_TABLES = frozenset({"gov_credentials"})
_SECRET_MARKERS = (
    "password", "credential", "bearer", "secret", "token", "private_key",
)


def _require_idle(connection) -> None:
    if connection.info.transaction_status != TransactionStatus.IDLE:
        raise OracleFailure("SQL oracle requires an idle connection; finish the caller transaction first")


def _tables(cursor) -> list[dict[str, Any]]:
    cursor.execute(
        """SELECT c.oid, n.nspname AS schema_name, c.relname AS table_name,
                  c.relowner, c.relrowsecurity, c.relforcerowsecurity,
                  pg_get_userbyid(c.relowner) AS owner_name
             FROM pg_class c
             JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relkind IN ('r','p')
              AND (left(c.relname,4)='gov_' OR c.relname='runtime_tasks')
            ORDER BY c.relname"""
    )
    return cursor.fetchall()


def _columns(cursor, oid: int) -> list[str]:
    cursor.execute(
        """SELECT attname FROM pg_attribute
            WHERE attrelid=%s AND attnum>0 AND NOT attisdropped
            ORDER BY attnum""",
        (oid,),
    )
    return [row["attname"] for row in cursor.fetchall()]


def _safe_columns(columns: list[str]) -> tuple[list[str], list[str]]:
    excluded = [name for name in columns if any(mark in name.lower() for mark in _SECRET_MARKERS)]
    return [name for name in columns if name not in excluded], excluded


def _foreign_keys(cursor) -> dict[str, list[dict[str, Any]]]:
    cursor.execute(
        """SELECT child.relname AS child_table, parent.relname AS parent_table,
                  array_agg(ca.attname ORDER BY k.ord) AS child_columns,
                  array_agg(pa.attname ORDER BY k.ord) AS parent_columns
             FROM pg_constraint f
             JOIN pg_class child ON child.oid=f.conrelid
             JOIN pg_namespace cn ON cn.oid=child.relnamespace
             JOIN pg_class parent ON parent.oid=f.confrelid
             JOIN pg_namespace pn ON pn.oid=parent.relnamespace
             CROSS JOIN LATERAL unnest(f.conkey,f.confkey) WITH ORDINALITY
                  AS k(child_num,parent_num,ord)
             JOIN pg_attribute ca ON ca.attrelid=child.oid AND ca.attnum=k.child_num
             JOIN pg_attribute pa ON pa.attrelid=parent.oid AND pa.attnum=k.parent_num
            WHERE f.contype='f' AND cn.nspname='public' AND pn.nspname='public'
              AND left(child.relname,4)='gov_' AND left(parent.relname,4)='gov_'
            GROUP BY f.oid,child.relname,parent.relname
            ORDER BY child.relname,parent.relname,f.oid"""
    )
    result: dict[str, list[dict[str, Any]]] = {}
    for row in cursor.fetchall():
        result.setdefault(row["child_table"], []).append(row)
    return result


def _direct_scope(
    alias: str, columns: list[str], scope_id: str, tenant_id: str, company_id: str,
) -> tuple[Any, tuple[str, ...]] | None:
    if "scope_id" in columns:
        predicates = [sql.SQL("{}::text=%s").format(sql.Identifier(alias, "scope_id"))]
        values = [scope_id]
        if "tenant_id" in columns:
            predicates.append(sql.SQL("{}::text=%s").format(sql.Identifier(alias, "tenant_id")))
            values.append(tenant_id)
        company_column = "company_id" if "company_id" in columns else "organization_id"
        if company_column in columns:
            predicates.append(sql.SQL("{}::text=%s").format(sql.Identifier(alias, company_column)))
            values.append(company_id)
        return sql.SQL(" AND ").join(predicates), tuple(values)
    company_column = "company_id" if "company_id" in columns else "organization_id"
    if "tenant_id" in columns and company_column in columns:
        return (
            sql.SQL("{}::text=%s AND {}::text=%s").format(
                sql.Identifier(alias, "tenant_id"), sql.Identifier(alias, company_column),
            ),
            (tenant_id, company_id),
        )
    return None


def _scope_filter(
    table: str, columns: dict[str, list[str]], foreign_keys: dict[str, list[dict]],
    scope_id: str, tenant_id: str, company_id: str,
) -> tuple[Any, tuple[str, ...], list[str]]:
    direct = _direct_scope("row_data", columns[table], scope_id, tenant_id, company_id)
    if direct is not None:
        return direct[0], direct[1], [table]

    # Indirect immutable records can be scoped through real FK relationships.
    # Never fall back to an unfiltered SELECT if the schema cannot prove scope.
    queue = deque([(table, [], {table})])
    while queue:
        current, path, visited = queue.popleft()
        for edge in foreign_keys.get(current, []):
            parent = edge["parent_table"]
            if parent in visited or parent not in columns:
                continue
            new_path = path + [edge]
            parent_alias = f"scope_parent_{len(new_path)}"
            anchor = _direct_scope(parent_alias, columns[parent], scope_id, tenant_id, company_id)
            if anchor is not None:
                from_items = []
                links = []
                child_alias = "row_data"
                for number, step in enumerate(new_path, 1):
                    alias = f"scope_parent_{number}"
                    from_items.append(sql.SQL("{} AS {}").format(
                        sql.Identifier("public", step["parent_table"]), sql.Identifier(alias),
                    ))
                    links.extend(
                        sql.SQL("{}={}").format(
                            sql.Identifier(child_alias, child_column),
                            sql.Identifier(alias, parent_column),
                        )
                        for child_column, parent_column in zip(
                            step["child_columns"], step["parent_columns"], strict=True,
                        )
                    )
                    child_alias = alias
                links.append(anchor[0])
                condition = sql.SQL("EXISTS (SELECT 1 FROM {} WHERE {})").format(
                    sql.SQL(", ").join(from_items), sql.SQL(" AND ").join(links),
                )
                return condition, anchor[1], [table] + [item["parent_table"] for item in new_path]
            if len(new_path) < 6:
                queue.append((parent, new_path, visited | {parent}))
    raise OracleFailure(f"Cannot establish a scope predicate for public.{table}; schema adaptation is required")


def snapshot_scope(connection, scope_id, tenant_id, company_id) -> dict[str, Any]:
    """Hash a coherent scope snapshot, including transactional outbox tasks.

    The function fails when governed tables/runtime_tasks are absent, when an
    unknown table cannot be scoped, or when no seeded gov_objects are visible.
    The credential bootstrap table is explicitly excluded: its RLS policy uses
    a credential digest, not scope. Other secret-bearing columns are never
    selected and are named as exclusions. This is not a credential audit.
    """
    _require_idle(connection)
    scope_id, tenant_id, company_id = map(str, (scope_id, tenant_id, company_id))
    if not all((scope_id, tenant_id, company_id)):
        raise OracleFailure("scope_id, tenant_id and company_id must be nonempty")
    report: dict[str, Any] = {
        "scope_id": scope_id, "tenant_id": tenant_id, "company_id": company_id,
        "tables": {}, "excluded_rejection_tables": [], "excluded_auth_tables": [],
    }
    with connection.transaction(), connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        cursor.execute("SET LOCAL TIME ZONE 'UTC'")
        cursor.execute("SET LOCAL DateStyle='ISO, YMD'")
        cursor.execute("SELECT set_config('app.governed_scope_id',%s,true)", (scope_id,))
        tables = _tables(cursor)
        by_name = {table["table_name"]: table for table in tables}
        if not {"gov_objects", "gov_scopes", "runtime_tasks"}.issubset(by_name):
            raise OracleFailure("Missing gov_objects, gov_scopes or runtime_tasks; an empty schema is not a pass")
        columns = {name: _columns(cursor, table["oid"]) for name, table in by_name.items()}
        foreign_keys = _foreign_keys(cursor)

        for name, table in by_name.items():
            if name in AUTH_BOOTSTRAP_TABLES:
                report["excluded_auth_tables"].append(name)
                continue
            if name in REJECTION_ONLY_TABLES:
                report["excluded_rejection_tables"].append(name)
                continue
            safe_columns, excluded_columns = _safe_columns(columns[name])
            if not safe_columns:
                raise OracleFailure(f"No non-secret columns available for public.{name}")
            predicate, params, scope_path = _scope_filter(
                name, columns, foreign_keys, scope_id, tenant_id, company_id,
            )
            pairs = []
            for column in safe_columns:
                pairs.extend([sql.Literal(column), sql.Identifier("row_data", column)])
            canonical = sql.SQL("jsonb_build_object({})::text").format(sql.SQL(", ").join(pairs))
            cursor.execute(
                sql.SQL("SELECT canonical_row FROM (SELECT {} AS canonical_row FROM {} AS row_data WHERE {}) AS canonical_rows ORDER BY canonical_row COLLATE \"C\"").format(
                    canonical, sql.Identifier(table["schema_name"], name), predicate,
                ),
                params,
            )
            digest = hashlib.sha256()
            row_count = 0
            while rows := cursor.fetchmany(128):
                for row in rows:
                    encoded = row["canonical_row"].encode("utf-8")
                    digest.update(len(encoded).to_bytes(8, "big"))
                    digest.update(encoded)
                    row_count += 1
            report["tables"][name] = {
                "row_count": row_count, "sha256": digest.hexdigest(),
                "hashed_columns": safe_columns, "excluded_columns": excluded_columns,
                "scope_path": scope_path,
            }
        if report["tables"]["gov_scopes"]["row_count"] != 1:
            raise OracleFailure("Expected exactly one visible scope matching the supplied scope/tenant/company")
        if report["tables"]["gov_objects"]["row_count"] < 1:
            raise OracleFailure("No seeded gov_objects are visible; empty/RLS-filtered data is not a pass")
    return report


def assert_application_role(connection) -> dict[str, Any]:
    """Fail unless the actual current DB role satisfies the pilot grant boundary.

    This inspects effective table AND column UPDATE privileges, TRUNCATE, owner
    role membership, and ENABLE/FORCE RLS. It is not a substitute for adversarial
    cross-scope reads through the API and app-role connection.
    """
    _require_idle(connection)
    failures = []
    report: dict[str, Any] = {"tables": {}}
    with connection.transaction(), connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute("SET TRANSACTION READ ONLY")
        cursor.execute(
            """SELECT current_user AS current_role, session_user AS session_role,
                      r.rolsuper, r.rolbypassrls, r.rolcreaterole,
                      pg_has_role(current_user,d.datdba,'MEMBER') AS database_owner_member
                 FROM pg_roles r CROSS JOIN pg_database d
                WHERE r.rolname=current_user AND d.datname=current_database()"""
        )
        identity = cursor.fetchone()
        if identity is None:
            raise OracleFailure("Could not inspect the current database role")
        report["identity"] = identity
        for flag in ("rolsuper", "rolbypassrls", "rolcreaterole", "database_owner_member"):
            if identity[flag]:
                failures.append(flag)
        # An owner/superuser session SET ROLE is weaker evidence than a real
        # application login; require the caller to use the application DSN.
        if identity["current_role"] != identity["session_role"]:
            failures.append("current_role differs from session_role; connect with the application login")
        tables = _tables(cursor)
        governed = [table for table in tables if table["table_name"].startswith("gov_")]
        if not governed or not any(table["table_name"] == "gov_objects" for table in governed):
            raise OracleFailure("Governed business tables are missing; no role acceptance can be inferred")
        for table in tables:
            name, oid = table["table_name"], table["oid"]
            cursor.execute(
                """SELECT pg_has_role(current_user,%s,'MEMBER') AS owner_member,
                          has_table_privilege(current_user,%s,'SELECT') AS can_select,
                          has_table_privilege(current_user,%s,'INSERT') AS can_insert,
                          has_table_privilege(current_user,%s,'UPDATE') AS can_update,
                          has_any_column_privilege(current_user,%s,'UPDATE') AS can_update_column,
                          has_table_privilege(current_user,%s,'DELETE') AS can_delete,
                          has_table_privilege(current_user,%s,'TRUNCATE') AS can_truncate""",
                (table["relowner"], oid, oid, oid, oid, oid, oid),
            )
            grants = cursor.fetchone()
            row = {
                "owner": table["owner_name"], "rls_enabled": table["relrowsecurity"],
                "rls_forced": table["relforcerowsecurity"], **grants,
            }
            report["tables"][name] = row
            if grants["owner_member"]:
                failures.append(f"{name}: application role owns or can assume an owner role")
            if not grants["can_select"]:
                failures.append(f"{name}: application role cannot perform the required read oracle")
            if name.startswith("gov_"):
                if not row["rls_enabled"] or not row["rls_forced"]:
                    failures.append(f"{name}: ENABLE/FORCE RLS required")
                if name not in MUTABLE_GOV_TABLES and (
                    grants["can_update"] or grants["can_update_column"] or grants["can_delete"]
                ):
                    failures.append(f"{name}: immutable records have UPDATE/DELETE privilege")
                if grants["can_truncate"]:
                    failures.append(f"{name}: TRUNCATE bypasses the append-only boundary")
        if failures:
            raise OracleFailure("Application role gate failed: " + "; ".join(failures))
    report["passed"] = True
    return report


def assert_scope_integrity(connection, scope_id, tenant_id, company_id) -> dict[str, Any]:
    """Check persisted ownership/hash invariants without trusting API projections.

    Historical assignments need not remain active: revocation must not invalidate
    previously legitimate signatures or acceptance history. Current authorization
    and external evidence bytes are checked separately by HTTP/receiver tests.
    """
    _require_idle(connection)
    scope_id, tenant_id, company_id = map(str, (scope_id, tenant_id, company_id))
    checks = {
        "head_revision_ownership": """
            SELECT count(*) AS violations FROM gov_objects o
            LEFT JOIN gov_object_revisions latest
              ON latest.scope_id=o.scope_id AND latest.object_id=o.object_id
             AND latest.revision_id=o.latest_revision_id
            LEFT JOIN gov_object_revisions effective
              ON effective.scope_id=o.scope_id AND effective.object_id=o.object_id
             AND effective.revision_id=o.effective_revision_id
            WHERE o.scope_id=%s AND (
                o.latest_revision_id IS NULL OR latest.revision_id IS NULL
                OR latest.object_version > o.object_version
                OR (o.effective_revision_id IS NOT NULL AND effective.revision_id IS NULL))""",
        "handshake_revision_hash_and_person": """
            SELECT count(*) AS violations FROM gov_handshakes h
            LEFT JOIN gov_object_revisions r
              ON r.scope_id=h.scope_id AND r.object_id=h.object_id AND r.revision_id=h.revision_id
            LEFT JOIN gov_role_assignments a
              ON a.scope_id=h.scope_id AND a.assignment_id=h.assignment_id
             AND a.principal_id=h.principal_id
            LEFT JOIN gov_principals p ON p.scope_id=h.scope_id AND p.principal_id=h.principal_id
            WHERE h.scope_id=%s AND (r.revision_id IS NULL OR a.assignment_id IS NULL
                OR p.principal_type IS DISTINCT FROM 'human' OR h.terms_hash<>r.payload_hash)""",
        "handshake_distinct_people_per_revision": """
            SELECT count(*) AS violations FROM (
                SELECT object_id,revision_id FROM gov_handshakes WHERE scope_id=%s
                GROUP BY object_id,revision_id
                HAVING count(*)>2 OR count(*)<>count(DISTINCT principal_id)
            ) invalid_signatures""",
        "acceptance_revision_and_verifier_ownership": """
            SELECT count(*) AS violations FROM gov_acceptances a
            LEFT JOIN gov_object_revisions f
              ON f.scope_id=a.scope_id AND f.object_id=a.feedback_object_id
             AND f.revision_id=a.feedback_revision_id
            LEFT JOIN gov_object_revisions d
              ON d.scope_id=a.scope_id AND d.revision_id=a.decision_revision_id
            LEFT JOIN gov_objects decision
              ON decision.scope_id=d.scope_id AND decision.object_id=d.object_id
            LEFT JOIN gov_role_assignments v
              ON v.scope_id=a.scope_id AND v.assignment_id=a.verifier_assignment_id
             AND v.principal_id=a.verifier_principal_id
            LEFT JOIN gov_principals p
              ON p.scope_id=a.scope_id AND p.principal_id=a.verifier_principal_id
            WHERE a.scope_id=%s AND (f.revision_id IS NULL OR d.revision_id IS NULL
                OR decision.object_type IS DISTINCT FROM 'Decision' OR v.assignment_id IS NULL
                OR p.principal_type IS DISTINCT FROM 'human')""",
        "acceptance_exact_evidence_references": """
            SELECT count(*) AS violations FROM gov_acceptances a
            WHERE a.scope_id=%s AND (
                jsonb_array_length(a.evidence_revision_ids)=0 OR EXISTS (
                    SELECT 1 FROM jsonb_array_elements_text(a.evidence_revision_ids) ref(id)
                    LEFT JOIN gov_object_revisions r ON r.scope_id=a.scope_id AND r.revision_id::text=ref.id
                    LEFT JOIN gov_objects o ON o.scope_id=r.scope_id AND o.object_id=r.object_id
                    WHERE r.revision_id IS NULL OR o.object_type IS DISTINCT FROM 'EvidenceAsset'))""",
    }
    report: dict[str, Any] = {"scope_id": scope_id, "violations": {}}
    with connection.transaction(), connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        cursor.execute("SELECT set_config('app.governed_scope_id',%s,true)", (scope_id,))
        cursor.execute(
            """SELECT count(*) AS objects FROM gov_objects o JOIN gov_scopes s USING(scope_id)
                WHERE s.scope_id=%s AND s.tenant_id=%s AND s.company_id=%s""",
            (scope_id, tenant_id, company_id),
        )
        report["seeded_object_count"] = cursor.fetchone()["objects"]
        if report["seeded_object_count"] < 1:
            raise OracleFailure("No seeded objects match the supplied scope; integrity cannot pass vacuously")
        for name, statement in checks.items():
            cursor.execute(statement, (scope_id,))
            report["violations"][name] = cursor.fetchone()["violations"]
        failed = [name for name, count in report["violations"].items() if count]
        if failed:
            raise OracleFailure("Persisted governed integrity failed: " + ", ".join(failed))
    report["passed"] = True
    return report
