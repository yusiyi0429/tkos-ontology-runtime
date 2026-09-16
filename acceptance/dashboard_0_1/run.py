"""Read-only acceptance of tkos.dashboard/0.1 against a real Method 0.1 dataset.

Runs the dashboard read models in-process against an isolated clone database
created by the upgrade rehearsal (or any explicit acceptance database) and
proves, with a before/after table-count oracle, that browsing writes nothing.

Usage (paths are private and never printed):

    uv run python -m acceptance.dashboard_0_1.run \
        --env-file .runtime-acceptance/<rehearsal>/env.json \
        --identity-file <private identities.json> \
        --output .runtime-acceptance/dashboard-0-1-report

The identity file is the synthetic fixture JSON containing an ``actors`` map;
the named actor's bearer token is used only in-process. Nothing in the output
names a token, DSN or business object id.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from memory_service_runtime.governed import dashboard, db
from memory_service_runtime.governed.errors import GovernedError

READ_ONLY_TABLES = (
    "gov_objects", "gov_object_revisions", "gov_object_protocol_bindings",
    "gov_action_receipts", "gov_method_reviews", "gov_method_state",
    "gov_method_strategy_heads", "gov_method_impacts", "gov_context_snapshots",
    "runtime_tasks",
)
GROUPS = ("strategy", "architecture", "ltco", "pco", "mission", "operating")


def _admin_env(env_file: Path) -> dict[str, str]:
    values = json.loads(env_file.read_text())
    if "APP_DATABASE_URL" not in values:
        raise ValueError("environment file lacks APP_DATABASE_URL")
    dsn = conninfo_to_dict(values["APP_DATABASE_URL"])
    if dsn.get("host") not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("dashboard 0.1 acceptance requires a loopback acceptance database")
    return values


def _token(identity_file: Path, actor: str) -> str:
    data = json.loads(identity_file.read_text())
    token = (data.get("actors") or {}).get(actor, {}).get("token")
    if not isinstance(token, str) or len(token) < 32:
        raise ValueError("identity file does not contain the requested actor token")
    return token


def _counts(dsn: str, scope_id: str) -> dict[str, int]:
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        conn.execute("SELECT set_config('app.runtime_write_capability','tkos-runtime-a1',false)")
        conn.execute("SELECT set_config('app.governed_scope_id',%s,false)", (scope_id,))
        result = {}
        for table in READ_ONLY_TABLES:
            row = conn.execute("SELECT to_regclass(%s) AS name", (f"public.{table}",)).fetchone()
            if row["name"] is None:
                continue
            count = conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]
            result[table] = int(count)
        return result


def _pages(reader, *, limit: int = 50, max_pages: int = 20) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    cursor = None
    for _ in range(max_pages):
        page = reader(cursor)
        items.extend(page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    return items


def run(env_file: Path, identity_file: Path, actor: str, output: Path) -> dict[str, Any]:
    values = _admin_env(env_file)
    dsn = values["APP_DATABASE_URL"]
    token = _token(identity_file, actor)
    checks: list[str] = []

    def check(name: str, value: Any = True) -> None:
        assert value, name
        checks.append(name)

    import os
    os.environ["DATABASE_URL"] = dsn
    with db.transaction(token) as (conn, ctx):
        before = _counts(dsn, ctx.scope_id)
        overview = dashboard.overview(conn, ctx)
        check("overview_is_tkos_dashboard_0_1", overview["schema_version"] == dashboard.SCHEMA_VERSION)
        check("overview_has_an_explicit_strategy_selection",
              bool(overview["selected_strategy_id"]) or overview["selection_required"])
        check("overview_group_availability_has_no_totals",
              all("available" in group and "reason" in group for group in overview["groups"]))

        per_group: dict[str, int] = {}
        details: list[dict[str, Any]] = []
        for group in GROUPS:
            items = _pages(lambda cursor, group=group: dashboard.objects(
                conn, ctx, group=group, limit=25, cursor=cursor))
            combined = _pages(lambda cursor, group=group: dashboard.objects(
                conn, ctx, group=group, basis="all", limit=25, cursor=cursor))
            check(f"combined_group_{group}_keeps_current_and_excludes_unrelated",
                  len(combined) >= len(items)
                  and all(item["basis"]["status"] != "unrelated" for item in combined))
            check(f"list_responsibility_{group}_excludes_participants",
                  all(entry["relation"] != "participant"
                      for item in items for entry in (item.get("responsibility") or [])))
            owner_entry = next((entry for item in items
                                for entry in (item.get("responsibility") or [])
                                if entry.get("principal")), None)
            if owner_entry:
                principal_id = owner_entry["principal"]["principal_id"]
                filtered = _pages(lambda cursor: dashboard.objects(
                    conn, ctx, group=group, owner_id=principal_id, limit=25, cursor=cursor))
                check(f"owner_filter_{group}_matches_recorded_responsibility",
                      bool(filtered) and all(
                          any((entry.get("principal") or {}).get("principal_id") == principal_id
                              for entry in (item.get("responsibility") or []))
                          for item in filtered))
            per_group[group] = len(items)
            availability = next(entry for entry in overview["groups"] if entry["group"] == group)
            if availability["available"]:
                check(f"group_{group}_availability_matches_readable_objects", len(items) > 0)
            else:
                check(f"group_{group}_unavailable_reason_is_explicit",
                      len(items) == 0 and bool(availability["reason"]))
            for item in items[:5]:
                detail = dashboard.detail(conn, ctx, item["object_id"])
                details.append(detail)
                check(f"detail_{item['object_type']}_carries_exact_and_protocol",
                      bool(detail["selected_revision"]["payload_hash"])
                      and detail["protocol"]["registration_status"] == "registered")
                for entry in detail["responsibility"]["entries"]:
                    check(f"appointment_{entry['relation']}_status_is_explicit",
                          entry["appointment"]["status"] in
                          {"current", "future", "expired", "revoked", "not_visible", "not_recorded"})
                    if entry["relation"] == "mission_owner" and entry.get("principal"):
                        rows = conn.execute(
                            "SELECT active FROM gov_role_assignments WHERE scope_id=%s"
                            " AND principal_id=%s AND active",
                            (ctx.scope_id, entry["principal"]["principal_id"])).fetchall()
                        if rows:
                            check("mission_owner_appointment_resolved_from_real_assignments",
                                  entry["appointment"]["status"] != "not_recorded")
                downstream = detail["relations"]["downstream"]
                check(f"downstream_{item['object_type']}_is_a_paginated_projection",
                      isinstance(downstream.get("items"), list)
                      and "next_cursor" in downstream and "has_more" in downstream)
                for edge in downstream["items"]:
                    check(f"downstream_edge_{item['object_type']}_matched_a_recorded_field",
                          bool(edge["matched_fields"]) and bool(edge["ref"]["payload_hash"]))

        # Cross-check recorded exact references the other way round: an object's
        # own basis ref must list it as that target's downstream edge.
        for detail in details:
            for basis in detail["relations"]["own_basis_refs"]:
                target = dashboard.detail(conn, ctx, basis["object_id"],
                                          revision_id=basis["revision_id"])
                downstream = target["relations"]["downstream"]["items"]
                check(f"recorded_ref_{detail['object']['object_type']}_is_in_target_downstream",
                      any(edge["object_id"] == detail["object"]["object_id"]
                          and edge["ref"]["revision_id"] == detail["selected_revision"]["revision_id"]
                          for edge in downstream))

        # Cursor binding: a strategy-revision change invalidates outstanding cursors.
        missions = dashboard.objects(conn, ctx, group="mission", limit=1)
        if missions["next_cursor"]:
            try:
                dashboard.objects(conn, ctx, group="mission", limit=1, basis="historical",
                                  cursor=missions["next_cursor"])
                raise AssertionError("cursor was accepted under a different basis")
            except GovernedError as exc:
                check("cursor_is_bound_to_filters", exc.status == 422)

        after = _counts(dsn, ctx.scope_id)
        check("browsing_wrote_nothing", before == after)

    result = {
        "scope": "read-only tkos.dashboard/0.1 acceptance on an explicit isolated dataset",
        "contract_version": dashboard.SCHEMA_VERSION,
        "actor": actor,
        "passed": len(checks),
        "checks": checks,
        "readable_object_counts": per_group,
        "table_counts_before_after_equal": before == after,
        "wrote_to_database": False,
        "browser_acceptance": "not_run",
        "deployed": False,
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "summary.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--identity-file", type=Path, required=True)
    parser.add_argument("--actor", default="ceo")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.env_file, args.identity_file, args.actor, args.output)
    print(json.dumps({key: result[key] for key in
                      ("passed", "readable_object_counts", "table_counts_before_after_equal",
                       "wrote_to_database")}, indent=2))


if __name__ == "__main__":
    main()
