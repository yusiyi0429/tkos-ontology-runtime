"""Root-invoked control-plane DB probes; import and default CLI are non-executing.

The root owns creation/migration and supplies valid synthetic probe inputs.  A
normal role must still be rejected after the historical broad INSERT grant is
reapplied.  Every row probe is rolled back, including unexpected successes.
Only exact, supplied function signatures are invoked; discovery is read-only.

Private spec: scope_id, optional session_settings, tables [{case_id, table,
columns, values, expected_sqlstates}], functions [{case_id, signature, args,
expected_sqlstates}], function_allowlist [exact regprocedure signatures].  Use
{"$jsonb": value} for a JSONB argument; ordinary lists remain PostgreSQL arrays.
No SQL, parameters, connection strings, exception messages or role names enter
public results.  This file does not reset/drop a DB or operate containers.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Any
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .support import Environment, public_json


_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_CASE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,100}$")
_CODE = re.compile(r"^[0-9A-Z]{5}$")
_DATABASE = re.compile(r"^tkos_a1_[a-z0-9_]+$")
_DEFAULT_REJECTIONS = ("42501", "55000")


def _name(value: Any) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError("probe identifiers must be simple lowercase SQL identifiers")
    return value


def _codes(item: dict) -> list[str]:
    codes = item.get("expected_sqlstates", list(_DEFAULT_REJECTIONS))
    if (not isinstance(codes, list) or not codes
            or any(not isinstance(code, str) or not _CODE.fullmatch(code) for code in codes)
            or any(code.startswith(("00", "01", "02", "22", "23", "42P")) for code in codes)):
        raise ValueError("oracle must name permission or reviewed governance rejection SQLSTATEs")
    return codes


def _argument(value: Any) -> Any:
    if isinstance(value, dict):
        if set(value) != {"$jsonb"}:
            raise ValueError("object SQL arguments require an explicit $jsonb wrapper")
        return Jsonb(value["$jsonb"])
    return value


def validate_spec(spec: dict) -> dict:
    if not isinstance(spec, dict):
        raise ValueError("probe spec must be an object")
    if set(spec) - {"scope_id", "session_settings", "tables", "functions", "function_allowlist"}:
        raise ValueError("probe spec contains an unknown field")
    UUID(spec["scope_id"])
    tables, functions = spec.get("tables", []), spec.get("functions", [])
    allowlist, settings = spec.get("function_allowlist", []), spec.get("session_settings", {})
    if not isinstance(tables, list) or not isinstance(functions, list) or not tables + functions:
        raise ValueError("at least one explicit control-plane probe is required")
    if not isinstance(allowlist, list) or any(not isinstance(s, str) for s in allowlist):
        raise ValueError("function allowlist must contain exact signatures")
    if not isinstance(settings, dict) or any(
            not isinstance(k, str) or not re.fullmatch(r"app\.[a-z][a-z0-9_]{0,62}", k)
            or not isinstance(v, str) for k, v in settings.items()):
        raise ValueError("only string app.* session settings may be supplied")
    if "app.governed_scope_id" in settings:
        raise ValueError("scope setting is controlled by scope_id")
    ids = set()
    for item in tables + functions:
        if not isinstance(item, dict) or not _CASE.fullmatch(str(item.get("case_id", ""))):
            raise ValueError("each probe requires a safe case_id")
        if item["case_id"] in ids:
            raise ValueError("probe case_id values must be unique")
        ids.add(item["case_id"])
        _codes(item)
    for item in tables:
        if set(item) - {"case_id", "table", "columns", "values", "expected_sqlstates"}:
            raise ValueError("unknown table probe field")
        table = _name(item["table"])
        if not table.startswith("gov_"):
            raise ValueError("control-plane table probes must name a gov_ table")
        columns, values = item["columns"], item["values"]
        if (not isinstance(columns, list) or not columns or not isinstance(values, list)
                or len(columns) != len(values) or len(set(columns)) != len(columns)):
            raise ValueError("table probe requires distinct columns and matching values")
        for column in columns:
            _name(column)
        for value in values:
            _argument(value)
    for item in functions:
        if set(item) - {"case_id", "signature", "args", "expected_sqlstates"}:
            raise ValueError("unknown function probe field")
        signature = item["signature"]
        if (not isinstance(signature, str) or len(signature) > 1000
                or not re.fullmatch(r"public\.[a-z][a-z0-9_]*\([A-Za-z0-9_., \[\]]*\)", signature)
                or signature not in allowlist or not isinstance(item["args"], list)):
            raise ValueError("function probe requires an exact allowed public regprocedure signature")
        for value in item["args"]:
            _argument(value)
    return spec


def plan(spec: dict) -> dict:
    validate_spec(spec)
    return {"status": "not_run", "executed": False, "contract_a1_accepted": False,
            "owner_positive_required": True, "unexpected_success_always_rolled_back": True,
            "tables": [{"case_id": p["case_id"], "table": p["table"],
                        "expected_sqlstates": _codes(p)} for p in spec.get("tables", [])],
            "functions": [{"case_id": p["case_id"], "signature": p["signature"],
                           "expected_sqlstates": _codes(p)} for p in spec.get("functions", [])]}


def _settings(conn, spec: dict) -> None:
    conn.execute("SELECT set_config('statement_timeout','5000',true)")
    conn.execute("SELECT set_config('lock_timeout','3000',true)")
    conn.execute("SELECT set_config('app.governed_scope_id',%s,true)", (spec["scope_id"],))
    for key, value in spec.get("session_settings", {}).items():
        conn.execute("SELECT set_config(%s,%s,true)", (key, value))


def _attempt(conn, statement, args: list[Any], spec: dict) -> dict:
    try:
        with conn.transaction(force_rollback=True):
            _settings(conn, spec)
            conn.execute(statement, args)
            # force_rollback skips commit-time checks, so explicitly fire
            # deferred FK/constraint triggers before recording the attempt.
            conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
        return {"succeeded": True, "sqlstate": None, "rolled_back": True}
    except psycopg.Error as exc:
        return {"succeeded": False, "sqlstate": exc.sqlstate, "rolled_back": True}


def _identity(owner, app, expected_app: str, expected_owner: str) -> dict:
    query = """SELECT current_database() AS db, current_user AS actor,
        session_user AS session_actor, r.rolsuper, r.rolbypassrls,
        r.rolcreatedb, r.rolcreaterole, r.rolreplication
        FROM pg_roles r WHERE r.rolname=current_user"""
    own, actor = owner.execute(query).fetchone(), app.execute(query).fetchone()
    owned = app.execute("""SELECT count(*) AS n FROM pg_class c
        JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'
        AND pg_get_userbyid(c.relowner)=current_user""").fetchone()["n"]
    inherits_owner = app.execute("SELECT pg_has_role(current_user,%s,'MEMBER') AS yes",
                                (expected_owner,)).fetchone()["yes"]
    safe = (own["db"] == actor["db"] and bool(_DATABASE.fullmatch(own["db"]))
            and own["actor"] == own["session_actor"] == expected_owner
            and actor["actor"] == actor["session_actor"] == expected_app
            and own["actor"] != actor["actor"] and not owned and not inherits_owner
            and not any(actor[k] for k in ("rolsuper", "rolbypassrls", "rolcreatedb",
                                            "rolcreaterole", "rolreplication")))
    return {"passed": bool(safe), "one_off_database": bool(_DATABASE.fullmatch(own["db"])),
            "actual_roles_match": own["actor"] == expected_owner and actor["actor"] == expected_app,
            "application_owns_no_objects": owned == 0,
            "application_does_not_inherit_owner": not inherits_owner,
            "application_unprivileged": not any(actor[k] for k in
                ("rolsuper", "rolbypassrls", "rolcreatedb", "rolcreaterole", "rolreplication"))}


def _catalog(owner, app_role: str, tables: list[str], signatures: list[str]) -> dict:
    relations = owner.execute("""SELECT c.relname AS table, c.relrowsecurity AS rls,
        c.relforcerowsecurity AS force_rls,
        has_table_privilege(%s,c.oid,'INSERT') AS app_effective_insert,
        has_any_column_privilege(%s,c.oid,'INSERT') AS app_column_insert,
        has_table_privilege(%s,c.oid,'UPDATE') AS app_update,
        has_table_privilege(%s,c.oid,'DELETE') AS app_delete,
        has_table_privilege(%s,c.oid,'TRUNCATE') AS app_truncate
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relname=ANY(%s) ORDER BY c.relname""",
        (app_role, app_role, app_role, app_role, app_role, tables)).fetchall()
    functions = owner.execute("""SELECT p.oid::regprocedure::text AS signature,
        p.prosecdef AS security_definer, p.prokind AS kind,
        has_function_privilege(%s,p.oid,'EXECUTE') AS app_execute,
        EXISTS(SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
               WHERE a.grantee=0 AND a.privilege_type='EXECUTE') AS public_execute,
        EXISTS(SELECT 1 FROM unnest(COALESCE(p.proconfig,'{}'::text[])) cfg
               WHERE cfg LIKE 'search_path=%%') AS has_fixed_search_path
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND (p.proname LIKE 'gov_%%' OR p.prosecdef
            OR p.oid IN (SELECT to_regprocedure(s)::oid FROM unnest(%s::text[]) s))
        ORDER BY signature""", (app_role, signatures)).fetchall()
    return {"tables": relations, "functions": functions,
            "function_discovery_is_read_only": True,
            "catalog_does_not_prove_function_semantic_safety": True}


def _direct_insert_acl(owner, table: str, app_role: str) -> list[dict]:
    return owner.execute("""SELECT a.grantor, a.grantee, a.is_grantable FROM pg_class c
        JOIN pg_namespace n ON n.oid=c.relnamespace
        CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) a
        WHERE n.nspname='public' AND c.relname=%s
        AND a.grantee=(SELECT oid FROM pg_roles WHERE rolname=%s)
        AND a.privilege_type='INSERT' ORDER BY a.grantor,a.grantee,a.is_grantable""",
        (table, app_role)).fetchall()


def _table_probe(owner, app, app_role: str, probe: dict, spec: dict) -> dict:
    table = probe["table"]
    relation = owner.execute("""SELECT c.relkind, pg_get_userbyid(c.relowner)=current_user AS owned
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relname=%s""", (table,)).fetchone()
    if not relation or relation["relkind"] not in {"r", "p"} or not relation["owned"]:
        return {"case_id": probe["case_id"], "passed": False, "reason": "owner_table_precondition_failed"}
    statement = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
        sql.Identifier("public", table), sql.SQL(",").join(map(sql.Identifier, probe["columns"])),
        sql.SQL(",").join(sql.Placeholder() for _ in probe["columns"]))
    arguments = [_argument(value) for value in probe["values"]]
    result = {"case_id": probe["case_id"], "table": table, "passed": False}
    positive = _attempt(owner, statement, arguments, spec)
    result["owner_positive"] = positive
    if not positive["succeeded"]:
        result["reason"] = "valid_owner_fixture_required"
        return result
    before = _direct_insert_acl(owner, table, app_role)
    grant_needed, grant_added = not before, False
    result["temporary_direct_grant_added"] = False
    try:
        if grant_needed:
            owner.execute(sql.SQL("GRANT INSERT ON TABLE {} TO {}").format(
                sql.Identifier("public", table), sql.Identifier(app_role)))
            grant_added = True
            result["temporary_direct_grant_added"] = True
        negative = _attempt(app, statement, arguments, spec)
        result["application_attempt"] = negative
        result["passed"] = not negative["succeeded"] and negative["sqlstate"] in _codes(probe)
        if not result["passed"]:
            result["reason"] = ("application_insert_unexpectedly_succeeded" if negative["succeeded"]
                                else "unexpected_sqlstate")
    finally:
        if grant_added:
            owner.execute(sql.SQL("REVOKE INSERT ON TABLE {} FROM {}").format(
                sql.Identifier("public", table), sql.Identifier(app_role)))
        restored = _direct_insert_acl(owner, table, app_role) == before
        result["original_direct_insert_acl_restored"] = restored
        result["passed"] = result["passed"] and restored
    return result


def _function_probe(owner, app, probe: dict, spec: dict) -> dict:
    # to_regprocedure receives a parameter, never executable caller SQL.
    function = owner.execute("""SELECT p.oid, n.nspname, p.proname, p.prokind,
        p.provariadic, p.pronargs, p.prosecdef,
        ARRAY(SELECT format_type(t,NULL) FROM unnest(p.proargtypes) t) AS argtypes
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE p.oid=to_regprocedure(%s)""", (probe["signature"],)).fetchone()
    if (not function or function["nspname"] != "public" or function["prokind"] != "f"
            or function["provariadic"] or function["pronargs"] != len(probe["args"])):
        return {"case_id": probe["case_id"], "passed": False, "reason": "exact_function_precondition_failed"}
    # format_type comes from PostgreSQL's catalog, not spec interpolation.
    placeholders = [sql.SQL("{}::{}").format(sql.Placeholder(), sql.SQL(t)) for t in function["argtypes"]]
    statement = sql.SQL("SELECT {}({})").format(
        sql.Identifier(function["nspname"], function["proname"]), sql.SQL(",").join(placeholders))
    arguments = [_argument(value) for value in probe["args"]]
    positive = _attempt(owner, statement, arguments, spec)
    result = {"case_id": probe["case_id"], "signature": probe["signature"],
              "security_definer": function["prosecdef"], "owner_positive": positive, "passed": False}
    if not positive["succeeded"]:
        result["reason"] = "valid_owner_function_fixture_required"
        return result
    negative = _attempt(app, statement, arguments, spec)
    result["application_attempt"] = negative
    result["passed"] = not negative["succeeded"] and negative["sqlstate"] in _codes(probe)
    if not result["passed"]:
        result["reason"] = ("application_control_call_unexpectedly_succeeded" if negative["succeeded"]
                            else "unexpected_sqlstate")
    return result


def run_control_plane_probes(env_file: Path | None, spec: dict, *, execute: bool = False) -> dict:
    result = plan(spec)
    if not execute:
        return result
    if env_file is None:
        raise ValueError("execute requires a private environment file")
    environment = Environment(env_file)
    app_info = conninfo_to_dict(environment.values["APP_DATABASE_URL"])
    owner_info = conninfo_to_dict(environment.values["MIGRATION_DATABASE_URL"])
    if not _DATABASE.fullmatch(owner_info.get("dbname", "")):
        raise ValueError("control-plane probes require an isolated tkos_a1_* database")
    result.update(status="failed", executed=True)
    try:
        with psycopg.connect(environment.values["MIGRATION_DATABASE_URL"], autocommit=True,
                              row_factory=dict_row, connect_timeout=5) as owner, \
                psycopg.connect(environment.values["APP_DATABASE_URL"], autocommit=True,
                                 row_factory=dict_row, connect_timeout=5) as app:
            identity = _identity(owner, app, app_info["user"], owner_info["user"])
            result["identity"] = identity
            if not identity["passed"]:
                result["reason"] = "actual_database_identity_precondition_failed"
                return result
            tables = sorted({probe["table"] for probe in spec.get("tables", [])})
            result["catalog"] = _catalog(owner, app_info["user"], tables,
                                         [p["signature"] for p in spec.get("functions", [])])
            if len(result["catalog"]["tables"]) != len(tables):
                result["reason"] = "specified_control_table_missing"
                return result
            result["tables"], result["functions"] = [], []
            for probe in spec.get("tables", []):
                result["tables"].append(_table_probe(owner, app, app_info["user"], probe, spec))
            for probe in spec.get("functions", []):
                result["functions"].append(_function_probe(owner, app, probe, spec))
            checks = result["tables"] + result["functions"]
            result["status"] = "passed" if all(check["passed"] for check in checks) else "failed"
    except psycopg.Error as exc:
        result.update(status="failed", reason="database_operation_error", sqlstate=exc.sqlstate)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec-file", required=True, type=Path)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--execute", action="store_true", help="root-only explicit one-off DB probe execution")
    args = parser.parse_args()
    try:
        spec = json.loads(args.spec_file.read_text())
        result = run_control_plane_probes(args.env_file, spec, execute=args.execute)
    except (ValueError, KeyError, TypeError, OSError):
        result = {"status": "failed", "executed": False, "reason": "invalid_private_probe_configuration",
                  "contract_a1_accepted": False}
    public_json(args.output, result)
    print(json.dumps({"status": result["status"], "executed": result["executed"],
                      "contract_a1_accepted": False}))
    raise SystemExit(0 if result["status"] in {"not_run", "passed"} else 1)


if __name__ == "__main__":
    main()
