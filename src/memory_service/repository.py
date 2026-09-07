"""Public read APIs over Memory Service persistence."""
from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Any

from psycopg.rows import dict_row

def iter_governance_reviews(
    connect_factory,
    *,
    organization_id: str | None = None,
    since: datetime | None = None,
    page_size: int = 1000,
) -> Iterator[dict[str, Any]]:
    """Yield confirmed/rejected governance audit rows in stable keyset order."""
    cursor: datetime | None = None
    cursor_id: str | None = None
    while True:
        conditions = ["TRUE"]
        params: dict[str, Any] = {"limit": page_size}
        if organization_id is not None:
            conditions.append("p.organization_id=%(organization_id)s")
            params["organization_id"] = organization_id
        if since is not None:
            conditions.append("a.decided_at >= %(since)s")
            params["since"] = since
        if cursor is not None:
            conditions.append("(a.decided_at, a.audit_id) > (%(cursor)s, %(cursor_id)s)")
            params.update(cursor=cursor, cursor_id=cursor_id)
        with connect_factory() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""SELECT a.audit_id, a.proposal_id,
                           a.target_kind AS audit_target_kind, a.target_id AS audit_target_id,
                           a.revision, a.before_content, a.after_content, a.decision,
                           a.decided_by, a.decision_note, a.decided_at,
                           p.target_kind, p.action, p.proposed_content, p.evidence, p.source, p.confidence,
                           p.import_session_id, p.organization_id, p.tenant_id
                      FROM memory_audit a
                      JOIN memory_proposals p ON p.proposal_id=a.proposal_id
                     WHERE {' AND '.join(conditions)}
                     ORDER BY a.decided_at, a.audit_id LIMIT %(limit)s""",
                params,
            )
            rows = cur.fetchall()
        if not rows:
            return
        for row in rows:
            yield dict(row)
        cursor = rows[-1]["decided_at"]
        cursor_id = str(rows[-1]["audit_id"])
