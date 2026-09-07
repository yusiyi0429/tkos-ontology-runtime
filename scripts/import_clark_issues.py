"""Import clark issue titles into Working Memory via the governance write path.

Usage (inside a container that has memory_service installed):

    python scripts/import_clark_issues.py /input/titles.json

``titles.json`` is a JSON array of objects: [{"title": "..."}, ...].

Environment: DATABASE_URL, MEMORY_TENANT, MEMORY_ORG, VIEWER_USER_ID (an
in-scope human who acts as creator/confirmer).

Idempotency: a title whose chain already exists in the scope is skipped, so the
script can be rerun to pick up newly added clark issues.  Every write goes
through ``memory_service.working`` public functions; no raw SQL against wm_*
tables.  Duplicate titles inside the input are imported once and reported.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import psycopg
from pgvector.psycopg import register_vector

from memory_service import working


def _norm(title: str) -> str:
    import unicodedata

    return " ".join(unicodedata.normalize("NFKC", title).split())


def main() -> int:
    titles_path = Path(sys.argv[1] if len(sys.argv) > 1 else "/input/titles.json")
    database_url = os.environ["DATABASE_URL"]
    tenant_id = os.environ["MEMORY_TENANT"]
    organization_id = os.environ["MEMORY_ORG"]
    human = os.environ["VIEWER_USER_ID"]

    entries = json.loads(titles_path.read_text(encoding="utf-8"))
    seen_norm: set[str] = set()
    titles: list[str] = []
    duplicates: list[str] = []
    for entry in entries:
        title = str(entry.get("title", "")).strip()
        if not title:
            continue
        key = _norm(title)
        if key in seen_norm:
            duplicates.append(title)
            continue
        seen_norm.add(key)
        titles.append(title)

    imported: list[str] = []
    skipped: list[str] = []
    failed: list[tuple[str, str]] = []

    with psycopg.connect(database_url) as conn:
        try:
            register_vector(conn)
        except psycopg.ProgrammingError:
            pass
        existing = {
            _norm(chain["title"])
            for chain in working.list_chains(
                conn, tenant_id=tenant_id, organization_id=organization_id
            )
        }
        for title in titles:
            if _norm(title) in existing:
                skipped.append(title)
                continue
            try:
                with conn.transaction():
                    chain = working.create_chain_with_first_signal(
                        conn,
                        title=title,
                        signal_content={
                            "title": f"{title}（clark 议题信号）",
                            "description": "自 clark 议题库受控导入",
                        },
                        created_by=human,
                        tenant_id=tenant_id,
                        organization_id=organization_id,
                    )
                    issue = working.form_issue(
                        conn,
                        chain_id=chain["chain_id"],
                        signal_object_ids=[chain["signal"]["object_id"]],
                        key_question=title,
                        rationale="自 clark 议题库受控导入；判断历史未迁移（首次形成）",
                        created_by=human,
                    )
                    working.advance_issue_to_strategic(
                        conn,
                        issue["object_id"],
                        confirmed_by=human,
                        transition_note="clark 议题受控导入",
                    )
                imported.append(title)
            except Exception as exc:  # noqa: BLE001 - report and stop writing this title
                conn.rollback()
                failed.append((title, f"{exc.__class__.__name__}: {exc}"))

    report = {
        "input": len(entries),
        "imported": len(imported),
        "skipped_existing": len(skipped),
        "input_duplicates": len(duplicates),
        "failed": len(failed),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    for title, error in failed:
        print(f"FAILED: {title} -> {error}", file=sys.stderr)
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
